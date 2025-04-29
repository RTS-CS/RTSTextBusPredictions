import os
import logging
from flask import Flask, request, render_template, session, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from datetime import datetime, timedelta
import requests
from threading import Lock
import re

# ========== CONFIGURATION & INITIALIZATION ==========
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY = os.getenv("BUS_API_KEY", "7GqnDentpEHC9wjD7jeSvP7P6")
BASE_URL = "https://riderts.app/bustime/api/v3/getpredictions"
RTPIDATAFEED = os.getenv("RTPIDATAFEED", "bustime")
MESSAGE_LIMIT = int(os.getenv("MESSAGE_LIMIT", 8))

request_counts = {}
rate_limit_lock = Lock()

# ========== LANGUAGE MESSAGES ==========
MESSAGES = {
    "en": {
        "welcome": "Press 1 for English, Dos para Español.",
        "limit_reached": "You’ve reached the limit of 8 interactions per hour. Thank you for using our services, goodbye.",
        "start": "Hi, welcome to Gainesville RTS automatic Customer Service. Enter your stop ID number, then press pound (#).",
        "no_input": "No input received. Thank you for using our services, goodbye.",
        "invalid_stop": "Invalid input. Please call again and enter a valid stop number. Thank you for using our services, goodbye.",
        "stop_too_long": "Stop ID number can only be up to 4 digits. Please try again.",
        "stop_attempts_exceeded": "Stop ID number can only be up to 4 digits. Too many attempts. Thank you for using our services, goodbye.",
        "route_prompt": "Now enter your bus route number, then press pound (#).",
        "no_route": "No route number received. Thank you for using our services, goodbye.",
        "invalid_route": "Invalid route number. Call again with a valid route number. Thank you for using our services, goodbye.",
        "route_too_long": "Route number can only be up to 3 digits. Please try again.",
        "route_attempts_exceeded": "Route number can only be up to 3 digits. Too many attempts. Thank you for using our services, goodbye.",
        "prediction_prefix": "For stop {stop_id}, ",
        "no_prediction": "No buses expected at this stop in the next 45 minutes.",
        "more_prompt": "Would you like predictions for another bus number at this stop? Press 1 for yes, 2 for no.",
        "no_more_response": "No response received. Thank you for using our services, goodbye.",
        "request_limit": "Thank you for using our services, goodbye.",
        "more_route_prompt": "Enter another bus route number, then press pound (#).",
        "invalid_choice": "Invalid input. Thank you for using our services, goodbye.",
        "error": "Error: No caller identified. Thank you for using our services, goodbye."
    }
}

# ========== SECTION 3: Helper Functions ==========

def get_prediction(stop_id: str, route_id: str = None, lang: str = "en", web_mode: bool = False) -> str | list[str]:
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
            des = prd.get('des', 'N/A')
            if "/" in des:
                des = des.replace("/", f" {direction_word} ")
            key = f"{route_label} {rt} - {des}"
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

        results = [f"🚌 Estimated times for Stop ID {stop_id}:\n"]
        for key, times in grouped.items():
            if len(times) == 1:
                results.append(f"{key}: {times[0]}")
            else:
                results.append(f"{key}: {', '.join(times[:-1])} and {times[-1]}")

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

# ========== SECTION 5: Web Chat Interface ==========

@app.route("/", methods=["GET", "POST"])
def web_home():
    if "chat_history" not in session:
        session["chat_history"] = []

    if request.method == "POST":
        user_input = request.form.get("message", "").strip()

        if user_input:
            # Only save user input if it is NOT numeric (i.e., it's a general question)
            if not (user_input.isdigit() and 1 <= len(user_input) <= 4):
                session["chat_history"].append({"sender": "user", "text": user_input})

            # Handle numeric stop ID input separately
            if user_input.isdigit() and 1 <= len(user_input) <= 4:
                predictions = get_prediction(user_input, web_mode=True)
                if isinstance(predictions, str):
                    session["chat_history"].append({"sender": "bot", "text": predictions})
                else:
                    for line in predictions:
                        session["chat_history"].append({"sender": "bot", "text": line})
            else:
                # Basic fallback for other questions
                session["chat_history"].append({
                    "sender": "bot",
                    "text": (
                        "🤖 I'm a simple bus assistant! Please enter a numeric Stop ID (1–4 digits) "
                        "to get bus arrival predictions."
                    )
                })

    return render_template(
        "home.html",
        chat_history=session.get("chat_history", [])
    )

# ========== ROUTE: BACKGROUND PREDICTION REFRESH ==========
@app.route("/refresh", methods=["POST"])
def refresh_predictions():
    if not session.get("chat_history"):
        return jsonify(success=False)

    last_user_input = next(
        (entry["text"] for entry in reversed(session["chat_history"]) if entry["sender"] == "user"), None
    )

    if last_user_input and last_user_input.isdigit():
        predictions = get_prediction(last_user_input, web_mode=True)
        session["chat_history"] = [msg for msg in session["chat_history"] if msg["sender"] != "bot"]
        if isinstance(predictions, str):
            session["chat_history"].append({"sender": "bot", "text": predictions})
        else:
            for line in predictions:
                session["chat_history"].append({"sender": "bot", "text": line})
        return jsonify(success=True)

    return jsonify(success=False)

# ========== ROUTE: TWILIO SMS BOT ==========
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

# ========== ROUTE: TWILIO VOICE BOT ==========
# (Voice support not modified – insert previous voice routes here.)

# ========== RUN APP ==========
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
