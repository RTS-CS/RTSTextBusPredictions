import os
import logging
from flask import Flask, request, render_template_string
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from datetime import datetime, timedelta
import requests
from threading import Lock

app = Flask(__name__)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
API_KEY = os.getenv("BUS_API_KEY", "KfRiwhzgjPeFG9rviJvkpCjnr")
BASE_URL = "https://riderts.app/bustime/api/v3/getpredictions"
RTPIDATAFEED = os.getenv("RTPIDATAFEED", "bustime")
MESSAGE_LIMIT = int(os.getenv("MESSAGE_LIMIT", 8))
MAX_ATTEMPTS = 3
MAX_BUS_REQUESTS = 3

# Rate limiting (in-memory)
request_counts = {}
rate_limit_lock = Lock()

# IMPORTANT: Paste your full MESSAGES dictionary here:
MESSAGES = {
    # Your entire MESSAGES dictionary (English and Spanish) here
}

def get_prediction(stop_id: str, route_id: str = None, lang: str = "en") -> str:
    logger.info(f"Fetching prediction for stop_id={stop_id}, route_id={route_id}, lang={lang}")
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
        arrives_label = "arrives" if lang == "en" else "llega"
        minutes_label = "minutes" if lang == "en" else "minutos"
        direction = "Going toward" if lang == "en" else "dirigiéndose a"

        if route_id:
            route_id_stripped = str(int(route_id))
            matching = [prd for prd in predictions if prd.get("rt", "").lower() == route_id_stripped.lower()]
            if not matching:
                return "no_prediction"
            prd = matching[0]
            destination = prd.get('des', 'N/A')
            arrival_time = prd.get('prdctdn', 'N/A')
            if lang == "es" and arrival_time == "Due":
                return f"{route_label} {prd.get('rt', 'N/A')} {direction} |{destination}| llegará en menos de 1 minuto"
            elif lang == "es":
                return f"{route_label} {prd.get('rt', 'N/A')} {direction} |{destination}| {arrives_label} en {arrival_time} {minutes_label}"
            elif lang == "en" and arrival_time == "Due":
                return f"{route_label} {prd.get('rt', 'N/A')} {destination.replace('/', f' {direction} ')} is Due"
            else:
                return f"{route_label} {prd.get('rt', 'N/A')} {destination.replace('/', f' {direction} ')} {arrives_label} in {arrival_time} {minutes_label}"

        # Show ALL upcoming predictions within 45 minutes
results = []
for prd in predictions:
    minutes = prd.get('prdctdn')
    if minutes == "DUE" or (minutes and minutes.isdigit() and int(minutes) <= 45):
        results.append(f"{route_label} {prd.get('rt', 'N/A')} {prd.get('des', 'N/A').replace('/', f' {direction} ')} in {minutes} {minutes_label}")
if not results:
    return "No buses arriving within the next 45 minutes."
return "\n".join(results)


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
            logger.info(f"Rate limit bypassed for exempt number {user_id}")
            return True
        if user_id not in request_counts:
            request_counts[user_id] = {"count": 1, "reset_time": now + timedelta(hours=1)}
            logger.info(f"New user {user_id}: count=1")
            return True
        user_data = request_counts[user_id]
        if now > user_data["reset_time"]:
            user_data["count"] = 1
            user_data["reset_time"] = now + timedelta(hours=1)
            logger.info(f"Reset for {user_id}: count=1")
            return True
        elif user_data["count"] < MESSAGE_LIMIT:
            user_data["count"] += 1
            logger.info(f"Increment for {user_id}: count={user_data['count']}")
            return True
        logger.info(f"Limit reached for {user_id}")
        return False

# --- SMS and Twilio endpoints ---
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

# --- VOICE FUNCTIONS ---
# (paste here your full original /voice, /language, /gather_stop, /gather_route, /more_routes)
# These are unchanged!

# --- Web Interface (new) ---
@app.route("/", methods=["GET", "POST"])
def home():
    predictions = None
    if request.method == "POST":
        stop_id = request.form.get("stop_id")
        if stop_id:
            predictions = get_prediction(stop_id)
    html_template = """
    <!doctype html>
    <html>
    <head><title>RTS Bus Predictions</title></head>
    <body>
    <h1>Enter Stop ID to Get Bus Predictions</h1>
    <form method="POST">
      <input type="text" name="stop_id" maxlength="4" required>
      <input type="submit" value="Get Prediction">
    </form>
    {% if predictions %}
      <h2>Prediction Results:</h2>
      <ul>
        {% for prediction in predictions.split('\\n') %}
          <li>{{ prediction }}</li>
        {% endfor %}
      </ul>
      <br>
      <a href="/">Go Back</a>
    {% endif %}
    </body>
    </html>
    """
    return render_template_string(html_template, predictions=predictions)

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
