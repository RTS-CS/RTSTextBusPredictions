import os
import logging
from flask import Flask, request, render_template_string
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from datetime import datetime, timedelta
import requests
from threading import Lock
import re

app = Flask(__name__)

# ========== SECTION 1: Logging Setup and Configuration ==========
# This section initializes the logging system for the application and
# defines various configuration parameters.

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

# ========== SECTION 2: MESSAGES Dictionary ==========
# This section contains the dictionary of messages used by the application,
# organized by language (English and Spanish).

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
    },
    "es": {
        "welcome": "Presione 1 para inglés, Marque dos para Español.",
        "limit_reached": "Ha alcanzado el límite de 8 interacciones por hora. Ok, Gracias por utilizar nuestro servicio.",
        "start": "Hola, bienvenido al servicio automático de Gainesville RTS. Ingrese el número de su parada, luego presione el símbolo de número.",
        "no_input": "No se recibió entrada. Ok, Gracias por utilizar nuestro servicio.",
        "invalid_stop": "Entrada inválida. Por favor llame de nuevo e ingrese un número de parada válido. Ok, Gracias por utilizar nuestro servicio.",
        "stop_too_long": "El número de parada puede tener hasta 4 dígitos. Intente de nuevo.",
        "stop_attempts_exceeded": "El número de parada puede tener hasta 4 dígitos. Demasiados intentos. Ok, Gracias por utilizar nuestro servicio.",
        "route_prompt": "Ahora ingrese el número de su ruta de autobús, luego presione el símbolo de número.",
        "no_route": "No se recibió número de ruta. Ok, Gracias por utilizar nuestro servicio.",
        "invalid_route": "Número de ruta inválido. Llame de nuevo con un número de ruta válido. Ok, Gracias por utilizar nuestro servicio.",
        "route_too_long": "El número de ruta puede tener hasta 3 dígitos. Intente de nuevo.",
        "route_attempts_exceeded": "El número de ruta puede tener hasta 3 dígitos. Demasiados intentos. Ok, Gracias por utilizar nuestro servicio, Adiós.",
        "prediction_prefix": "Para la parada {stop_id}, ",
        "no_prediction": "El autobús que solicitó no se espera aquí en la próxima hora.",
        "more_prompt": "¿Desea predicciones para otro número de autobús en esta parada? Presione 1 para sí, 2 para no.",
        "no_more_response": "No se recibió respuesta. Ok, Gracias por utilizar nuestro servicio.",
        "request_limit": "Ok, Gracias por utilizar nuestro servicio.",
        "more_route_prompt": "Ingrese otro número de ruta de autobús, luego presione el símbolo de número.",
        "invalid_choice": "Entrada inválida. Ok, Gracias por utilizar nuestro servicio.",
        "error": "Error: No se identificó al llamante. Ok, Gracias por utilizar nuestro servicio."
    }
}

# ========== SECTION 3: Helper Function - get_prediction ==========
# This function fetches bus predictions from the external API based on the
# provided stop ID and optional route ID. It handles API requests,
# response parsing, and formats the prediction output.

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
        arrives_label = "arrives" if lang == "en" else "llega"
        minutes_label = "minutes" if lang == "en" else "minutos"
        direction = "Going toward" if lang == "en" else "dirigiéndose a"

        results = []
        for prd in predictions:
            arrival = prd.get('prdctdn', 'N/A')
            if arrival == "DUE":
                arrival_text = "Due" if lang == "en" else "llega en menos de 1 minuto"
            else:
                try:
                    arrival_min = int(arrival)
                    if web_mode:
                        if arrival_min <= 45:
                            arrival_text = f"{arrival_min} {minutes_label}"
                        else:
                            continue  # skip buses later than 45 min
                    else:
                        arrival_text = f"{arrival_min} {minutes_label}"
                except ValueError:
                    arrival_text = arrival
            destination = prd.get('des', 'N/A').replace("/", f" {direction} ")
            result = f"{route_label} {prd.get('rt', 'N/A')} {destination} in {arrival_text}"
            results.append(result)

        if not results:
            return "No buses expected in the next 45 minutes."

        if web_mode:
            return results
        else:
            return "\n".join(results[:3])  # Only first 3 predictions for SMS

    except requests.RequestException as e:
        logger.error(f"API request failed: {e}")
        return f"Network error: {e}"
    except ValueError:
        logger.error("Invalid API response")
        return "Invalid API response."

# ========== SECTION 4: Helper Function - check_rate_limit ==========
# This function implements a simple in-memory rate limiting mechanism to
# control the number of interactions per user within a one-hour window.

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

# ========== SECTION 5: Web Interface Endpoints ==========
# This section defines the Flask routes for the web interface, allowing
# users to input a stop ID and view bus predictions.

import re

# Light LLM-style function: Smartly extract stop ID from messy user input
def smart_extract_stop_id(text: str) -> str:
    """Extracts a valid 1-4 digit stop ID from messy input."""
    text = text.strip()
    match = re.search(r'\b\d{1,4}\b', text)
    if match:
        return match.group()
    return None

HTML_TEMPLATE = '''
<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>RTS Bus Predictions</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background-color: #f4f4f4; }
        h1 { color: #333; }
        form { margin-bottom: 20px; }
        input[type="text"] { padding: 8px; width: 200px; }
        button { padding: 8px 15px; }
        .predictions { margin-top: 20px; padding: 20px; background: white; border-radius: 8px; }
        .error { color: red; margin-top: 10px; }
    </style>
</head>
<body>
    <h1>Enter Stop ID to Get Bus Predictions</h1>
    <form method="POST">
        <input type="text" name="stop_id" placeholder="Enter Stop ID" required>
        <button type="submit">Get Predictions</button>
    </form>
    {% if predictions %}
    <div class="predictions">
        <h2>Prediction Results:</h2>
        <ul>
            {% for prediction in predictions %}
            <li>{{ prediction }}</li>
            {% endfor %}
        </ul>
        <br>
        <a href="/">Go Back</a>
    </div>
    {% elif error %}
    <div class="error">
        {{ error }}
    </div>
    {% endif %}
</body>
</html>
'''

@app.route("/", methods=["GET", "POST"])
def web_home():
    predictions = None
    error = None
    if request.method == "POST":
        user_input = request.form.get("stop_id", "").strip()
        stop_id = smart_extract_stop_id(user_input)
        if stop_id:
            predictions = get_prediction(stop_id, web_mode=True)
            if not predictions:  # If empty list from API
                error = "❗ No buses expected at this stop in the next 45 minutes."
                predictions = None
        else:
            error = "❗ Please enter a valid 1-4 digit bus stop number."
    return render_template_string(HTML_TEMPLATE, predictions=predictions, error=error)

# ========== SECTION 6: SMS Textbot Endpoint ==========
# This section defines the Flask route for handling incoming SMS messages.
# It checks the rate limit and responds with bus predictions based on the
# stop ID provided in the message.

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

# ========== SECTION 7: Voice IVR Endpoints ==========
# This section defines the Flask routes for the voice IVR system. It handles
# incoming calls, language selection, gathering stop and route IDs, and
# providing bus predictions via voice.

@app.route("/voice", methods=["POST"])
def voice():
    logger.info("Entering /voice")
    response = VoiceResponse()
    from_number = request.values.get('From', '')
    if not from_number:
        response.say(MESSAGES["en"]["error"], voice="Polly.Joanna")
        response.hangup()
        return str(response)

    english_voice = "Polly.Joanna"
    spanish_voice = "Polly.Lucia"
    gather = Gather(input='dtmf', num_digits=1, action='/language', timeout=5)
    gather.say("Press 1 for English", voice=english_voice, language="en")
    gather.say("Marque dos para Español", voice=spanish_voice, language="es")
    response.append(gather)
    response.say(MESSAGES["en"]["no_input"], voice=english_voice, language="en")
    return str(response)

@app.route("/language", methods=["POST"])
def language():
    logger.info("Entering /language")
    choice = request.values.get('Digits', None)
    from_number = request.values.get('From', '')
    response = VoiceResponse()

    if not from_number:
        response.say(MESSAGES["en"]["error"], voice="Polly.Joanna")
        response.hangup()
        return str(response)

    if not (choice and choice.isdigit() and choice in ["1", "2"]):
        response.say(MESSAGES["en"]["invalid_choice"], voice="Polly.Joanna")
        response.hangup()
        return str(response)

    lang = "en" if choice == "1" else "es"
    voice = "Polly.Joanna" if lang == "en" else "Polly.Lucia"

    if not check_rate_limit(from_number):
        response.say(MESSAGES[lang]["limit_reached"], voice=voice, language=lang)
        response.hangup()
        return str(response)

    gather = Gather(input='dtmf', finish_on_key='#', num_digits=5, action=f'/gather_stop?lang={lang}&attempt=1', timeout=5)
    gather.say(MESSAGES[lang]["start"], voice=voice, language=lang)
    response.append(gather)
    response.say(MESSAGES[lang]["no_input"], voice=voice, language=lang)
    return str(response)

@app.route("/gather_stop", methods=["POST"])
def gather_stop():
    logger.info("Entering /gather_stop")
    stop_id = request.values.get('Digits', None)
    from_number = request.values.get('From', '')
    lang = request.args.get('lang', 'en')
    attempt = int(request.args.get('attempt', 1))
    response = VoiceResponse()
    voice = "Polly.Joanna" if lang == "en" else "Polly.Lucia"

    if not from_number:
        response.say(MESSAGES[lang]["error"], voice=voice, language=lang)
        response.hangup()
    elif not check_rate_limit(from_number):
        response.say(MESSAGES[lang]["limit_reached"], voice=voice, language=lang)
        response.hangup()
    elif not (stop_id and stop_id.isdigit()):
        response.say(MESSAGES[lang]["invalid_stop"], voice=voice, language=lang)
        response.hangup()
    elif len(stop_id) > 4:
        next_attempt = attempt + 1
        if next_attempt > MAX_ATTEMPTS:
            response.say(MESSAGES[lang]["stop_attempts_exceeded"], voice=voice, language=lang)
        else:
            response.say(MESSAGES[lang]["stop_too_long"], voice=voice, language=lang)
            response.redirect(f'/language?Digits={1 if lang == "en" else 2}&attempt={next_attempt}')
    else:
        stop_id = stop_id.zfill(4)
        gather = Gather(input='dtmf', finish_on_key='#', num_digits=4, action=f'/gather_route?stop_id={stop_id}&lang={lang}&bus_count=1&attempt=1', timeout=5)
        gather.say(MESSAGES[lang]["route_prompt"], voice=voice, language=lang)
        response.append(gather)
        response.say(MESSAGES[lang]["no_route"], voice=voice, language=lang)
    return str(response)

@app.route("/gather_route", methods=["POST"])
def gather_route():
    logger.info("Entering /gather_route")
    stop_id = request.args.get('stop_id', '')
    route_id = request.values.get('Digits', None)
    from_number = request.values.get('From', '')
    lang = request.args.get('lang', 'en')
    attempt = int(request.args.get('attempt', 1))
    bus_count = int(request.args.get('bus_count', 1))
    response = VoiceResponse()
    voice = "Polly.Joanna" if lang == "en" else "Polly.Lucia"
    english_voice = "Polly.Joanna"

    if not from_number:
        response.say(MESSAGES[lang]["error"], voice=voice, language=lang)
        response.hangup()
    elif not check_rate_limit(from_number):
        response.say(MESSAGES[lang]["limit_reached"], voice=voice, language=lang)
        response.hangup()
    elif not (route_id and route_id.isdigit()):
        response.say(MESSAGES[lang]["invalid_route"], voice=voice, language=lang)
        response.hangup()
    elif len(route_id) > 3:
        next_attempt = attempt + 1
        if next_attempt > MAX_ATTEMPTS:
            response.say(MESSAGES[lang]["route_attempts_exceeded"], voice=voice, language=lang)
        else:
            response.say(MESSAGES[lang]["route_too_long"], voice=voice, language=lang)
            response.redirect(f'/gather_route?stop_id={stop_id}&lang={lang}&bus_count={bus_count}&attempt={next_attempt}')
    else:
        stop_id_stripped = str(int(stop_id))
        prediction = get_prediction(stop_id, route_id, lang)

        if prediction == "no_prediction":
            response.say(f"{MESSAGES[lang]['prediction_prefix'].format(stop_id=stop_id_stripped)}{MESSAGES[lang]['no_prediction']}", voice=voice, language=lang)
        else:
            if lang == "es":
                parts = prediction.split('|')
                response.say(f"{MESSAGES[lang]['prediction_prefix'].format(stop_id=stop_id_stripped)}{parts[0]}", voice=voice, language=lang)
                response.say(parts[1], voice=english_voice, language="en")
                response.say(parts[2], voice=voice, language=lang)
            else:
                response.say(f"{MESSAGES[lang]['prediction_prefix'].format(stop_id=stop_id_stripped)}{prediction}", voice=voice, language=lang)

        if bus_count < MAX_BUS_REQUESTS:
            gather = Gather(input='dtmf', num_digits=1, action=f'/more_routes?stop_id={stop_id}&lang={lang}&bus_count={bus_count}', timeout=5)
            gather.say(MESSAGES[lang]["more_prompt"], voice=voice, language=lang)
            response.append(gather)
            response.say(MESSAGES[lang]["no_more_response"], voice=voice, language=lang)
        else:
            response.say(MESSAGES[lang]["request_limit"], voice=voice, language=lang)
    return str(response)

@app.route("/more_routes", methods=["POST"])
def more_routes():
    logger.info("Entering /more_routes")
    choice = request.values.get('Digits', None)
    from_number = request.values.get('From', '')
    stop_id = request.args.get('stop_id', '')
    lang = request.args.get('lang', 'en')
    bus_count = int(request.args.get('bus_count', 1))
    response = VoiceResponse()
    voice = "Polly.Joanna" if lang == "en" else "Polly.Lucia"

    if not from_number:
        response.say(MESSAGES[lang]["error"], voice=voice, language=lang)
        response.hangup()
    elif not check_rate_limit(from_number):
        response.say(MESSAGES[lang]["limit_reached"], voice=voice, language=lang)
        response.hangup()
    elif not (choice and choice.isdigit()):
        response.say(MESSAGES[lang]["invalid_choice"], voice=voice, language=lang)
        response.hangup()
    elif choice == "1":
        next_bus_count = bus_count + 1
        gather = Gather(input='dtmf', finish_on_key='#', num_digits=4, action=f'/gather_route?stop_id={stop_id}&lang={lang}&bus_count={next_bus_count}&attempt=1', timeout=5)
        gather.say(MESSAGES[lang]["more_route_prompt"], voice=voice, language=lang)
        response.append(gather)
        response.say(MESSAGES[lang]["no_route"], voice=voice, language=lang)
    else:
        response.say(MESSAGES[lang]["request_limit"], voice=voice, language=lang)
    return str(response)


# ========== SECTION 8: Run App ==========
# This section contains the main entry point for running the Flask application.

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)        
