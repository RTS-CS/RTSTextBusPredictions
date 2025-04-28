import os
import logging
from flask import Flask, request, render_template
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from datetime import datetime, timedelta
import requests
from threading import Lock
import re

app = Flask(__name__)

# ========== SECTION 1: Logging Setup and Configuration ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
API_KEY = os.getenv("BUS_API_KEY", "7GqnDentpEHC9wjD7jeSvP7P6")
BASE_URL = "https://riderts.app/bustime/api/v3/getpredictions"
RTPIDATAFEED = os.getenv("RTPIDATAFEED", "bustime")
MESSAGE_LIMIT = int(os.getenv("MESSAGE_LIMIT", 8))
MAX_ATTEMPTS = 3
MAX_BUS_REQUESTS = 3

request_counts = {}
rate_limit_lock = Lock()

# ========== SECTION 2: MESSAGES Dictionary ==========
# [Keep your MESSAGES dictionary here unchanged]

# ========== SECTION 3: Helper Functions ==========

def get_prediction(stop_id: str, route_id: str = None, lang: str = "en", web_mode: bool = False) -> str:
    logger.info(f"Fetching prediction for stop_id={stop_id}, route_id={route_id}, lang={lang}, web_mode={web_mode}")
    padded_stop_id = str(stop_id).zfill(4)
    params = {"key": API_KEY, "rtpidatafeed": RTPIDATAFEED, "stpid": padded_stop_id, "format": "json", "max": 99}
    
    try:
        response = requests.get(BASE_URL, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        if "bustime-response" not in data or "prd" not in data["bustime-response"]:
            return "No predictions available for this stop."

        predictions = data["bustime-response"]["prd"]
        if not predictions:
            return "No predictions available for this stop."

        route_label = "Route" if lang == "en" else "Ruta"
        minutes_label = "minutes" if lang == "en" else "minutos"
        due_text = "Due" if lang == "en" else "llega en menos de 1 minuto"
        direction_word = "Going toward" if lang == "en" else "dirigiéndose a"

        grouped = {}
        for prd in predictions:
            rt = prd.get('rt', 'N/A')
            des = prd.get('des', 'N/A').replace("/", f" {direction_word} ")
            key = f"{route_label} {rt} {des}"
            arrival = prd.get('prdctdn', 'N/A')

            if arrival == "DUE":
                arrival_text = due_text
            else:
                try:
                    arrival_min = int(arrival)
                    if web_mode and arrival_min > 45:
                        continue
                    arrival_text = f"{arrival_min} {minutes_label}"
                except ValueError:
                    arrival_text = arrival

            if key not in grouped:
                grouped[key] = []
            grouped[key].append(arrival_text)

        if not grouped:
            return "No buses expected in the next 45 minutes."

        results = []
        for key, times in grouped.items():
            if len(times) == 1:
                results.append(f"{key}: {times[0]}")
            else:
                formatted_times = " and ".join(times)
                results.append(f"{key}: {formatted_times}")

        if web_mode:
            return results
        else:
            return "\n".join(results[:3])

    except requests.RequestException as e:
        logger.error(f"API request failed: {e}")
        return f"Network error: {e}"
    except ValueError:
        logger.error("Invalid API response")
        return "Invalid API response."

def check_rate_limit(user_id: str) -> bool:
    now = datetime.now()
    with rate_limit_lock:
        if user_id == "+17867868466":
            return True
        if user_id not in request_counts:
            request_counts[user_id] = {"count": 1, "reset_time": now + timedelta(hours=1)}
            return True
        user_data = request_counts[user_id]
        if now > user_data["reset_time"]:
            user_data["count"] = 1
            user_data["reset_time"] = now + timedelta(hours=1)
            return True
        elif user_data["count"] < MESSAGE_LIMIT:
            user_data["count"] += 1
            return True
        return False

def smart_extract_stop_id(text: str) -> str:
    text = text.strip()
    match = re.search(r'\b\d{1,4}\b', text)
    if match:
        return match.group()
    return None

# ========== SECTION 5: Web Interface ==========
@app.route("/", methods=["GET", "POST"])
def web_home():
    predictions = None
    error = None
    if request.method == "POST":
        user_input = request.form.get("stop_id", "").strip()
        stop_id = smart_extract_stop_id(user_input)
        if stop_id:
            predictions = get_prediction(stop_id, web_mode=True)
            if not predictions:
                error = "❗ No buses expected at this stop in the next 45 minutes."
                predictions = None
        else:
            error = "❗ Please enter a valid 1-4 digit bus stop number."
    return render_template("home.html", predictions=predictions, error=error)

# ========== SECTION 6: SMS Bot ==========
@app.route("/bot", methods=["POST"])
def bot():
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    response = MessagingResponse()
    if not from_number:
        response.message("Error: No sender.")
    elif check_rate_limit(from_number):
        if incoming_msg.isdigit() and 1 <= len(incoming_msg) <= 4:
            response.message(get_prediction(incoming_msg))
        else:
            response.message("Send a valid 1-4 digit stop number.")
    else:
        response.message("You’ve reached the limit of 8 interactions per hour.")
    return str(response)

# ========== SECTION 7: Voice Bot ==========
# (Voice endpoints - unchanged. Copy what you had.)

# ========== SECTION 8: Run the App ==========
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
