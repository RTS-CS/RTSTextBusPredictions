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
API_KEY = os.getenv("BUS_API_KEY", "7GqnDentpEHC9wjD7jeSvP7P6")
BASE_URL = "https://riderts.app/bustime/api/v3/getpredictions"
RTPIDATAFEED = os.getenv("RTPIDATAFEED", "bustime")
MESSAGE_LIMIT = int(os.getenv("MESSAGE_LIMIT", 8))
MAX_ATTEMPTS = 3
MAX_BUS_REQUESTS = 3

# Rate limiting (in-memory)
request_counts = {}
rate_limit_lock = Lock()

# Language-specific messages (same as your full MESSAGES dictionary)
MESSAGES = {
    # (your full English and Spanish MESSAGES dictionary goes here, unchanged)
    # For brevity here, assume you paste the full MESSAGES dict from your original code
}

# ---------------- New Homepage Web Form ----------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        stop_id = request.form.get("stop_id")
        if stop_id and stop_id.isdigit():
            prediction = get_prediction(stop_id)
            return render_template_string("""
                <h1>Prediction Result</h1>
                <p>{{ prediction }}</p>
                <a href="/">Go Back</a>
            """, prediction=prediction)
        else:
            return render_template_string("""
                <h1>Invalid input</h1>
                <a href="/">Try Again</a>
            """)
    return '''
        <form method="post">
            <label>Enter Stop ID:</label>
            <input type="text" name="stop_id" maxlength="4" required>
            <input type="submit" value="Get Prediction">
        </form>
    '''

# ---------------- All your Original Code ----------------

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

        return "\n".join(
            f"{route_label} {prd.get('rt', 'N/A')} {prd.get('des', 'N/A').replace('/', f' {direction} ')} in {prd.get('prdctdn', 'N/A')} {minutes_label}"
            for prd in predictions[:3]
        )
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

# ---------------- The rest of your /voice, /language, /gather_stop, /gather_route, /more_routes endpoints ----------------
# (you just paste the rest of your full code exactly here — no changes needed)

# Final run
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
