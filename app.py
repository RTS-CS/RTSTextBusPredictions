import os
import logging
from flask import Flask, request, render_template_string, redirect, url_for
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from datetime import datetime, timedelta
import requests
from threading import Lock

app = Flask(__name__)

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurations
API_KEY = os.getenv("BUS_API_KEY", "KfRiwhzgjPeFG9rviJvkpCjnr")
BASE_URL = "https://riderts.app/bustime/api/v3/getpredictions"
RTPIDATAFEED = os.getenv("RTPIDATAFEED", "bustime")
MESSAGE_LIMIT = int(os.getenv("MESSAGE_LIMIT", 8))
MAX_ATTEMPTS = 3
MAX_BUS_REQUESTS = 3

# Rate limiting setup
request_counts = {}
rate_limit_lock = Lock()

# Messages (copy your full MESSAGES dictionary here!)
MESSAGES = {
    "en": {
        "welcome": "Press 1 for English, Dos para Español.",
        "limit_reached": "You’ve reached the limit of 8 interactions per hour. Ok, Thank you for using our services, Bye.",
        "start": "Hi, welcome to Gainesville RTS automatic Customer Service. Enter your stop ID number, then press pound.",
        "no_input": "No input received. Ok, Thank you for using our services, Bye.",
        "invalid_stop": "Invalid input. Please call again and enter a valid stop number. Ok, Thank you for using our services, Bye.",
        "stop_too_long": "Stop ID number can be only up to 4 digits. Please try again.",
        "stop_attempts_exceeded": "Stop ID number can be only up to 4 digits. Too many attempts. Ok, Thank you for using our services, Bye.",
        "route_prompt": "Now enter your bus route number, then press pound.",
        "no_route": "No route number received. Ok, Thank you for using our services, Bye.",
        "invalid_route": "Invalid route number. Call again with a valid route number. Ok, Thank you for using our services, Bye.",
        "route_too_long": "Route number can be only up to 3 digits. Please try again.",
        "route_attempts_exceeded": "Route number can be only up to 3 digits. Too many attempts. Ok, Thank you for using our services, Bye.",
        "prediction_prefix": "For stop {stop_id}, ",
        "no_prediction": "The bus you requested is not expected to be here for the next hour.",
        "more_prompt": "Would you like predictions for another bus number at this stop? Press 1 for yes, 2 for no.",
        "no_more_response": "No response received. Ok, Thank you for using our services, Bye.",
        "request_limit": "Ok, Thank you for using our services, Bye.",
        "more_route_prompt": "Enter another bus route number, then press pound.",
        "invalid_choice": "Invalid input. Ok, Thank you for using our services, Bye.",
        "error": "Error: No caller identified. Ok, Thank you for using our services, Bye."
    }
}

# HTML Template for the web form
WEB_FORM = """
<!DOCTYPE html>
<html>
<head>
    <title>Bus Predictions</title>
</head>
<body>
    <h1>Enter Stop ID to Get Bus Predictions</h1>
    <form action="/" method="post">
        <input type="text" name="stop_id" placeholder="Stop ID" required>
        <button type="submit">Get Predictions</button>
    </form>

    {% if predictions %}
        <h2>Prediction Results:</h2>
        <ul>
            {% for prediction in predictions %}
                <li>{{ prediction }}</li>
            {% endfor %}
        </ul>
        <a href="/">Go Back</a>
    {% endif %}
</body>
</html>
"""

def get_prediction(stop_id: str, route_id: str = None, lang: str = "en") -> list:
    logger.info(f"Fetching prediction for stop_id={stop_id}, route_id={route_id}, lang={lang}")
    padded_stop_id = str(stop_id).zfill(4)
    params = {"key": API_KEY, "rtpidatafeed": RTPIDATAFEED, "stpid": padded_stop_id, "format": "json", "max": 99}
    results = []
    try:
        response = requests.get(BASE_URL, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        if "bustime-response" not in data or "prd" not in data["bustime-response"]:
            return ["No predictions available for this stop."]
        predictions = data["bustime-response"]["prd"]

        for prd in predictions:
            route = prd.get("rt", "N/A")
            destination = prd.get("des", "N/A")
            arrival_time = prd.get("prdctdn", "N/A")
            if arrival_time == "DUE":
                arrival_time = "Due"
            results.append(f"Route {route} {destination} in {arrival_time} minutes")

    except requests.RequestException as e:
        logger.error(f"API request failed: {e}")
        results.append(f"Network error: {e}")
    except ValueError:
        logger.error("Invalid API response")
        results.append("Invalid API response.")
    return results

def check_rate_limit(user_id: str) -> bool:
    now = datetime.now()
    with rate_limit_lock:
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

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        stop_id = request.form.get("stop_id", "")
        predictions = get_prediction(stop_id)
        return render_template_string(WEB_FORM, predictions=predictions)
    return render_template_string(WEB_FORM)

@app.route("/bot", methods=["POST"])
def bot():
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    response = MessagingResponse()
    if not from_number:
        response.message("Error: No sender.")
    elif check_rate_limit(from_number):
        if incoming_msg.isdigit() and 1 <= len(incoming_msg) <= 4:
            pred = get_prediction(incoming_msg)
            response.message("\n".join(pred))
        else:
            response.message("Send a valid 1-4 digit stop number.")
    else:
        response.message("You’ve reached the limit of 8 interactions per hour.")
    return str(response)

@app.route("/voice", methods=["POST"])
def voice():
    response = VoiceResponse()
    from_number = request.values.get('From', '')
    if not from_number:
        response.say(MESSAGES["en"]["error"])
        response.hangup()
        return str(response)

    gather = Gather(input='dtmf', num_digits=1, action='/language', timeout=5)
    gather.say(MESSAGES["en"]["welcome"])
    response.append(gather)
    response.say(MESSAGES["en"]["no_input"])
    return str(response)

@app.route("/language", methods=["POST"])
def language():
    response = VoiceResponse()
    choice = request.values.get('Digits', None)
    if not choice or choice not in ["1", "2"]:
        response.say(MESSAGES["en"]["invalid_choice"])
        response.hangup()
        return str(response)
    lang = "en" if choice == "1" else "es"
    gather = Gather(input='dtmf', finish_on_key='#', num_digits=4, action=f'/gather_stop?lang={lang}', timeout=5)
    gather.say(MESSAGES[lang]["start"])
    response.append(gather)
    response.say(MESSAGES[lang]["no_input"])
    return str(response)

@app.route("/gather_stop", methods=["POST"])
def gather_stop():
    response = VoiceResponse()
    stop_id = request.values.get('Digits', None)
    lang = request.args.get('lang', 'en')
    if not stop_id or not stop_id.isdigit() or len(stop_id) > 4:
        response.say(MESSAGES[lang]["invalid_stop"])
        response.hangup()
        return str(response)
    stop_id = stop_id.zfill(4)
    gather = Gather(input='dtmf', finish_on_key='#', num_digits=3, action=f'/gather_route?stop_id={stop_id}&lang={lang}', timeout=5)
    gather.say(MESSAGES[lang]["route_prompt"])
    response.append(gather)
    response.say(MESSAGES[lang]["no_route"])
    return str(response)

@app.route("/gather_route", methods=["POST"])
def gather_route():
    response = VoiceResponse()
    route_id = request.values.get('Digits', None)
    stop_id = request.args.get('stop_id', '')
    lang = request.args.get('lang', 'en')
    if not route_id or not route_id.isdigit() or len(route_id) > 3:
        response.say(MESSAGES[lang]["invalid_route"])
        response.hangup()
        return str(response)

    predictions = get_prediction(stop_id, route_id)
    for pred in predictions:
        response.say(pred)
    return str(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
