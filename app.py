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
# This section contains the dictionary of messages used by the application,
# organized by language (English and Spanish).

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
    },
    "es": {
        "welcome": "Presione 1 para inglés, Marque 2 para Español.",
        "limit_reached": "Ha alcanzado el límite de 8 interacciones por hora. Gracias por utilizar nuestro servicio, adiós.",
        "start": "Hola, bienvenido al servicio automático de Gainesville RTS. Ingrese el número de su parada, luego presione el símbolo de número (#).",
        "no_input": "No se recibió entrada. Gracias por utilizar nuestro servicio, adiós.",
        "invalid_stop": "Entrada inválida. Por favor llame de nuevo e ingrese un número de parada válido. Gracias por utilizar nuestro servicio, adiós.",
        "stop_too_long": "El número de parada puede tener hasta 4 dígitos. Inténtelo de nuevo.",
        "stop_attempts_exceeded": "El número de parada puede tener hasta 4 dígitos. Demasiados intentos. Gracias por utilizar nuestro servicio, adiós.",
        "route_prompt": "Ahora ingrese el número de su ruta de autobús, luego presione el símbolo de número (#).",
        "no_route": "No se recibió número de ruta. Gracias por utilizar nuestro servicio, adiós.",
        "invalid_route": "Número de ruta inválido. Llame de nuevo con un número de ruta válido. Gracias por utilizar nuestro servicio, adiós.",
        "route_too_long": "El número de ruta puede tener hasta 3 dígitos. Inténtelo de nuevo.",
        "route_attempts_exceeded": "El número de ruta puede tener hasta 3 dígitos. Demasiados intentos. Gracias por utilizar nuestro servicio, adiós.",
        "prediction_prefix": "Para la parada {stop_id}, ",
        "no_prediction": "No se esperan autobuses en esta parada en los próximos 45 minutos.",
        "more_prompt": "¿Desea predicciones para otro número de autobús en esta parada? Presione 1 para sí, 2 para no.",
        "no_more_response": "No se recibió respuesta. Gracias por utilizar nuestro servicio, adiós.",
        "request_limit": "Gracias por utilizar nuestro servicio, adiós.",
        "more_route_prompt": "Ingrese otro número de ruta de autobús, luego presione el símbolo de número (#).",
        "invalid_choice": "Entrada inválida. Gracias por utilizar nuestro servicio, adiós.",
        "error": "Error: No se identificó al llamante. Gracias por utilizar nuestro servicio, adiós."
    }
}


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
