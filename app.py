import os  # Import os module for environment variable access.
import logging  # Import logging module for application logging.
from flask import Flask, request  # Import Flask and request for web app and HTTP request handling.
from twilio.twiml.messaging_response import MessagingResponse  # Import MessagingResponse for SMS responses.
from twilio.twiml.voice_response import VoiceResponse, Gather  # Import VoiceResponse and Gather for voice call responses.
from datetime import datetime, timedelta  # Import datetime and timedelta for time-based rate limiting.
import requests  # Import requests for making API calls to the bus prediction service.
from threading import Lock  # Import Lock for thread-safe rate limiting.

app = Flask(__name__)  # Initialize the Flask application with the current module name.

# Logging setup
logging.basicConfig(level=logging.INFO)  # Configure logging to show INFO level messages and above.
logger = logging.getLogger(__name__)  # Create a logger instance for this module.

# Configuration
API_KEY = os.getenv("BUS_API_KEY", "KfRiwhzgjPeFG9rviJvkpCjnr")  # Get API key from environment or use default.
BASE_URL = "https://riderts.app/bustime/api/v3/getpredictions"  # Base URL for the bus prediction API.
RTPIDATAFEED = os.getenv("RTPIDATAFEED", "bustime")  # Get RTPIDATAFEED from environment or use default "bustime".
MESSAGE_LIMIT = int(os.getenv("MESSAGE_LIMIT", 8))  # Set max interactions per hour (default 8) from environment.
MAX_ATTEMPTS = 3  # Maximum attempts allowed for entering stop or route numbers.
MAX_BUS_REQUESTS = 3  # Maximum number of bus predictions a user can request in one call.

# Rate limiting (in-memory)
request_counts = {}  # Dictionary to store interaction counts and reset times per user.
rate_limit_lock = Lock()  # Thread-safe lock for updating request_counts.

# Language-specific messages
MESSAGES = {  # Dictionary containing messages in English ("en") and Spanish ("es").
    "en": {  # English messages.
        "welcome": "Press 1 for English, Dos para Español.",  # Initial greeting prompting language selection.
        "limit_reached": "You’ve reached the limit of 8 interactions per hour. Ok, Thank you for using our services, Bye.",  # Message when rate limit is hit.
        "start": "Hi, welcome to Gainesville RTS automatic Customer Service. Enter your stop ID number, then press pound.",  # Prompt for stop ID.
        "no_input": "No input received. Ok, Thank you for using our services, Bye.",  # Message when no input is detected.
        "invalid_stop": "Invalid input. Please call again and enter a valid stop number. Ok, Thank you for using our services, Bye.",  # Message for invalid stop ID.
        "stop_too_long": "Stop ID number can be only up to 4 digits. Please try again.",  # Message when stop ID exceeds 4 digits.
        "stop_attempts_exceeded": "Stop ID number can be only up to 4 digits. Too many attempts. Ok, Thank you for using our services, Bye.",  # Message after too many stop ID attempts.
        "route_prompt": "Now enter your bus route number, then press pound.",  # Prompt for route number.
        "no_route": "No route number received. Ok, Thank you for using our services, Bye.",  # Message when no route number is entered.
        "invalid_route": "Invalid route number. Call again with a valid route number. Ok, Thank you for using our services, Bye.",  # Message for invalid route number.
        "route_too_long": "Route number can be only up to 3 digits. Please try again.",  # Message when route number exceeds 3 digits.
        "route_attempts_exceeded": "Route number can be only up to 3 digits. Too many attempts. Ok, Thank you for using our services, Bye.",  # Message after too many route attempts.
        "prediction_prefix": "For stop {stop_id}, ",  # Prefix for bus prediction message with stop ID.
        "no_prediction": "The bus you requested is not expected to be here for the next hour.",  # Message when no bus prediction is available.
        "more_prompt": "Would you like predictions for another bus number at this stop? Press 1 for yes, 2 for no.",  # Prompt to request another bus prediction.
        "no_more_response": "No response received. Ok, Thank you for using our services, Bye.",  # Message when no response to more_prompt.
        "request_limit": "Ok, Thank you for using our services, Bye.",  # Farewell message after max bus requests.
        "more_route_prompt": "Enter another bus route number, then press pound.",  # Prompt for additional route number.
        "invalid_choice": "Invalid input. Ok, Thank you for using our services, Bye.",  # Message for invalid yes/no choice.
        "error": "Error: No caller identified. Ok, Thank you for using our services, Bye."  # Error message when caller ID is missing.
    },
    "es": {  # Spanish messages.
        "welcome": "Presione 1 para inglés, Marque dos para Español.",  # Initial greeting prompting language selection, updated to "Dial 2 for Spanish".
        "limit_reached": "Ha alcanzado el límite de 8 interacciones por hora. Ok, Gracias por utilizar nuestro servicio,.",  # Message when rate limit is hit.
        "start": "Hola, bienvenido al servicio automático de Gainesville RTS. Ingrese el número de su parada, luego presione el símbolo de número.",  # Prompt for stop ID.
        "no_input": "No se recibió entrada. Ok, Gracias por utilizar nuestro servicio.",  # Message when no input is detected.
        "invalid_stop": "Entrada inválida. Por favor llame de nuevo e ingrese un número de parada válido. Ok, Gracias por utilizar nuestro servicio.",  # Message for invalid stop ID.
        "stop_too_long": "El número de parada puede tener hasta 4 dígitos. Intente de nuevo.",  # Message when stop ID exceeds 4 digits.
        "stop_attempts_exceeded": "El número de parada puede tener hasta 4 dígitos. Demasiados intentos. Ok, Gracias por utilizar nuestro servicio.",  # Message after too many stop ID attempts.
        "route_prompt": "Ahora ingrese el número de su ruta de autobús, luego presione el símbolo de número.",  # Prompt for route number.
        "no_route": "No se recibió número de ruta. Ok, Gracias por utilizar nuestro servicio.",  # Message when no route number is entered.
        "invalid_route": "Número de ruta inválido. Llame de nuevo con un número de ruta válido. Ok, Gracias por utilizar nuestro servicio.",  # Message for invalid route number.
        "route_too_long": "El número de ruta puede tener hasta 3 dígitos. Intente de nuevo.",  # Message when route number exceeds 3 digits.
        "route_attempts_exceeded": "El número de ruta puede tener hasta 3 dígitos. Demasiados intentos. Ok, Gracias por utilizar nuestro servicio, Adiós.",  # Message after too many route attempts.
        "prediction_prefix": "Para la parada {stop_id}, ",  # Prefix for bus prediction message with stop ID.
        "no_prediction": "El autobús que solicitó no se espera aquí en la próxima hora.",  # Message when no bus prediction is available.
        "more_prompt": "¿Desea predicciones para otro número de autobús en esta parada? Presione 1 para sí, 2 para no.",  # Prompt to request another bus prediction.
        "no_more_response": "No se recibió respuesta. Ok, Gracias por utilizar nuestro servicio.",  # Message when no response to more_prompt.
        "request_limit": "Ok, Gracias por utilizar nuestro servicio.",  # Farewell message after max bus requests.
        "more_route_prompt": "Ingrese otro número de ruta de autobús, luego presione el símbolo de número.",  # Prompt for additional route number.
        "invalid_choice": "Entrada inválida. Ok, Gracias por utilizar nuestro servicio.",  # Message for invalid yes/no choice.
        "error": "Error: No se identificó al llamante. Ok, Gracias por utilizar nuestro servicio."  # Error message when caller ID is missing.
    }
}

def get_prediction(stop_id: str, route_id: str = None, lang: str = "en") -> str:  # Function to fetch bus predictions from the API.
    logger.info(f"Fetching prediction for stop_id={stop_id}, route_id={route_id}, lang={lang}")  # Log the prediction request details.
    padded_stop_id = str(stop_id).zfill(4)  # Pad stop ID with zeros to ensure 4 digits.
    params = {"key": API_KEY, "rtpidatafeed": RTPIDATAFEED, "stpid": padded_stop_id, "format": "json", "max": 99}  # API request parameters.
    try:  # Attempt to fetch data from the API.
        response = requests.get(BASE_URL, params=params, timeout=5)  # Send GET request to the API with a 5-second timeout.
        response.raise_for_status()  # Raise an exception if the request fails (e.g., 404, 500).
        data = response.json()  # Parse the API response as JSON.
        if "bustime-response" not in data or "prd" not in data["bustime-response"]:  # Check if the response contains prediction data.
            return "No predictions available for this stop."  # Return message if no predictions are found.
        predictions = data["bustime-response"]["prd"]  # Extract prediction list from the response.
        if not predictions:  # Check if the prediction list is empty.
            return "No predictions available for this stop."  # Return message if no predictions are available.
        
        route_label = "Route" if lang == "en" else "Ruta"  # Set route label based on language.
        arrives_label = "arrives" if lang == "en" else "llega"  # Set arrives label based on language.
        minutes_label = "minutes" if lang == "en" else "minutos"  # Set minutes label based on language.
        direction = "Going toward" if lang == "en" else "dirigiéndose a"  # Set direction label based on language.

        if route_id:  # If a specific route ID is provided.
            route_id_stripped = str(int(route_id))  # Convert route ID to integer then string to remove leading zeros.
            matching = [prd for prd in predictions if prd.get("rt", "").lower() == route_id_stripped.lower()]  # Filter predictions for the specified route.
            if not matching:  # Check if no predictions match the route.
                return "no_prediction"  # Return special string if no matching prediction.
            prd = matching[0]  # Take the first matching prediction.
            destination = prd.get('des', 'N/A')  # Get destination or "N/A" if missing.
            arrival_time = prd.get('prdctdn', 'N/A')  # Get predicted arrival time or "N/A" if missing.
            if lang == "es" and arrival_time == "Due":  # Special case for "Due" in Spanish.
                return f"{route_label} {prd.get('rt', 'N/A')} {direction} |{destination}| llegará en menos de 1 minuto"  # Return Spanish message for "Due".
            elif lang == "es":  # General case for Spanish.
                return f"{route_label} {prd.get('rt', 'N/A')} {direction} |{destination}| {arrives_label} en {arrival_time} {minutes_label}"  # Return Spanish prediction.
            elif lang == "en" and arrival_time == "Due":  # Special case for "Due" in English.
                return f"{route_label} {prd.get('rt', 'N/A')} {destination.replace('/', f' {direction} ')} is Due"  # Return English "Due" message.
            else:  # General case for English.
                return f"{route_label} {prd.get('rt', 'N/A')} {destination.replace('/', f' {direction} ')} {arrives_label} in {arrival_time} {minutes_label}"  # Return English prediction.
        return "\n".join(  # If no route specified, return up to 3 predictions.
            f"{route_label} {prd.get('rt', 'N/A')} {prd.get('des', 'N/A').replace('/', f' {direction} ')} in {prd.get('prdctdn', 'N/A')} {minutes_label}"
            for prd in predictions[:3]  # Iterate over the first 3 predictions.
        )
    except requests.RequestException as e:  # Catch network-related errors.
        logger.error(f"API request failed: {e}")  # Log the error.
        return f"Network error: {e}"  # Return error message to the user.
    except ValueError:  # Catch JSON parsing errors.
        logger.error("Invalid API response")  # Log the error.
        return "Invalid API response."  # Return error message to the user.

def check_rate_limit(user_id: str) -> bool:  # Function to check and enforce interaction rate limits.
    now = datetime.now()  # Get the current time.
    with rate_limit_lock:  # Use a lock to ensure thread-safe updates to request_counts.
        # Exempt the specific phone number "+17867868466" from rate limiting
        if user_id == "+17867868466":  # Check if the user ID matches your phone number (adjust to "7867868466" if Twilio sends it without country code).
            logger.info(f"Rate limit bypassed for exempt number {user_id}")  # Log that this number is exempt.
            return True  # Return True to allow unlimited interactions for this number.
        # Normal rate limiting logic for all other numbers
        if user_id not in request_counts:  # Check if the user is new to the request counts.
            request_counts[user_id] = {"count": 1, "reset_time": now + timedelta(hours=1)}  # Initialize count to 1 and set reset time 1 hour from now.
            logger.info(f"New user {user_id}: count=1")  # Log new user entry.
            return True  # Allow the first interaction.
        user_data = request_counts[user_id]  # Get the user's rate limit data.
        if now > user_data["reset_time"]:  # Check if the reset time has passed.
            user_data["count"] = 1  # Reset the count to 1.
            user_data["reset_time"] = now + timedelta(hours=1)  # Set a new reset time 1 hour from now.
            logger.info(f"Reset for {user_id}: count=1")  # Log the reset.
            return True  # Allow the interaction after reset.
        elif user_data["count"] < MESSAGE_LIMIT:  # Check if the count is below the limit (8).
            user_data["count"] += 1  # Increment the interaction count.
            logger.info(f"Increment for {user_id}: count={user_data['count']}")  # Log the increment.
            return True  # Allow the interaction.
        logger.info(f"Limit reached for {user_id}")  # Log when the limit is reached.
        return False  # Deny the interaction if the limit is hit.

@app.route("/bot", methods=["POST"])  # Define SMS endpoint at "/bot" accepting POST requests.
def bot():  # Function to handle SMS interactions.
    incoming_msg = request.values.get('Body', '').strip()  # Get the message body from the request, strip whitespace.
    from_number = request.values.get('From', '')  # Get the sender's phone number from the request.
    response = MessagingResponse()  # Create a new SMS response object.
    if not from_number:  # Check if sender number is missing.
        response.message("Error: No sender.")  # Send error message if no sender.
    elif check_rate_limit(from_number):  # Check if the sender is within rate limits.
        if incoming_msg.isdigit() and 1 <= len(incoming_msg) <= 4:  # Validate message is a 1-4 digit number.
            response.message(get_prediction(incoming_msg))  # Send bus prediction for the stop ID.
        else:  # If message is invalid.
            response.message("Send a valid 1-4 digit stop number.")  # Send error message for invalid input.
    else:  # If rate limit is exceeded.
        response.message("You’ve reached the limit of 8 interactions per hour.")  # Send rate limit exceeded message.
    return str(response)  # Return the response as a string.

@app.route("/voice", methods=["POST"])  # Define voice endpoint at "/voice" accepting POST requests.
def voice():  # Function to handle initial voice call interactions.
    logger.info("Entering /voice")  # Log entry to the voice endpoint.
    response = VoiceResponse()  # Create a new voice response object.
    from_number = request.values.get('From', '')  # Get the caller's phone number from the request.
    if not from_number:  # Check if caller number is missing.
        response.say(MESSAGES["en"]["error"], voice="Polly.Joanna")  # Say error message in English.
        response.hangup()  # Hang up the call.
        logger.error("No caller identified")  # Log the error.
        return str(response)  # Return the response as a string.

    english_voice = "Polly.Joanna"  # Define English voice as Polly.Joanna.
    spanish_voice = "Polly.Lucia"  # Define Spanish voice as Polly.Lucia.
    gather = Gather(input='dtmf', num_digits=1, action='/language', timeout=5)  # Create Gather object to collect 1 DTMF digit, redirect to /language, 5-sec timeout.
    gather.say("Press 1 for English", voice=english_voice, language="en")  # Prompt for English selection in English voice.
    gather.say("Marque dos para Español", voice=spanish_voice, language="es")  # Prompt for Spanish selection in Spanish voice, updated text.
    response.append(gather)  # Add the gather prompt to the response.
    response.say(MESSAGES["en"]["no_input"], voice=english_voice, language="en")  # Say English "no input" message with English voice if no input, updated.
    logger.info("Returning initial language prompt with split voices")  # Log the response preparation.
    return str(response)  # Return the response as a string.

@app.route("/language", methods=["POST"])  # Define language selection endpoint at "/language" accepting POST requests.
def language():  # Function to handle language selection.
    logger.info("Entering /language")  # Log entry to the language endpoint.
    choice = request.values.get('Digits', None)  # Get the user's DTMF input (1 or 2).
    from_number = request.values.get('From', '')  # Get the caller's phone number from the request.
    response = VoiceResponse()  # Create a new voice response object.

    if not from_number:  # Check if caller number is missing.
        response.say(MESSAGES["en"]["error"], voice="Polly.Joanna")  # Say error message in English.
        response.hangup()  # Hang up the call.
        logger.error("No caller identified")  # Log the error.
        return str(response)  # Return the response as a string.
    elif not (choice and choice.isdigit() and choice in ["1", "2"]):  # Validate the input is 1 or 2.
        response.say(MESSAGES["en"]["invalid_choice"], voice="Polly.Joanna")  # Say invalid choice message in English.
        response.hangup()  # Hang up the call.
        logger.error(f"Invalid language choice: {choice}")  # Log the invalid choice.
        return str(response)  # Return the response as a string.

    lang = "en" if choice == "1" else "es"  # Set language to English if 1, Spanish if 2.
    voice = "Polly.Joanna" if lang == "en" else "Polly.Lucia"  # Set voice based on language.
    logger.info(f"Language selected: {lang}, voice: {voice}")  # Log the selected language and voice.
    if not check_rate_limit(from_number):  # Check if the caller is within rate limits.
        response.say(MESSAGES[lang]["limit_reached"], voice=voice, language=lang)  # Say rate limit message in selected language.
        response.hangup()  # Hang up the call.
        return str(response)  # Return the response as a string.

    gather = Gather(input='dtmf', finish_on_key='#', num_digits=5, action=f'/gather_stop?lang={lang}&attempt=1', timeout=5)  # Create Gather for stop ID, up to 5 digits, end with #.
    gather.say(MESSAGES[lang]["start"], voice=voice, language=lang)  # Prompt for stop ID in selected language.
    response.append(gather)  # Add the gather prompt to the response.
    response.say(MESSAGES[lang]["no_input"], voice=voice, language=lang)  # Say "no input" message in selected language if no input.
    logger.info(f"Prompting for stop ID in {lang}")  # Log the stop ID prompt.
    return str(response)  # Return the response as a string.

@app.route("/gather_stop", methods=["POST"])  # Define stop ID collection endpoint at "/gather_stop" accepting POST requests.
def gather_stop():  # Function to handle stop ID input.
    logger.info("Entering /gather_stop")  # Log entry to the gather_stop endpoint.
    stop_id = request.values.get('Digits', None)  # Get the stop ID digits entered by the user.
    from_number = request.values.get('From', '')  # Get the caller's phone number from the request.
    lang = request.args.get('lang', 'en')  # Get the language from query parameters, default to English.
    attempt = int(request.args.get('attempt', 1))  # Get the attempt number from query parameters, default to 1.
    response = VoiceResponse()  # Create a new voice response object.
    voice = "Polly.Joanna" if lang == "en" else "Polly.Lucia"  # Set voice based on language.

    if not from_number:  # Check if caller number is missing.
        response.say(MESSAGES[lang]["error"], voice=voice, language=lang)  # Say error message in selected language.
        response.hangup()  # Hang up the call.
        logger.error("No caller identified")  # Log the error.
    elif not check_rate_limit(from_number):  # Check if the caller is within rate limits.
        response.say(MESSAGES[lang]["limit_reached"], voice=voice, language=lang)  # Say rate limit message in selected language.
        response.hangup()  # Hang up the call.
    elif not (stop_id and stop_id.isdigit()):  # Validate stop ID is numeric.
        response.say(MESSAGES[lang]["invalid_stop"], voice=voice, language=lang)  # Say invalid stop message in selected language.
        response.hangup()  # Hang up the call.
        logger.error(f"Invalid stop ID: {stop_id}")  # Log the invalid stop ID.
    elif len(stop_id) > 4:  # Check if stop ID exceeds 4 digits.
        next_attempt = attempt + 1  # Increment attempt counter.
        if next_attempt > MAX_ATTEMPTS:  # Check if max attempts exceeded.
            response.say(MESSAGES[lang]["stop_attempts_exceeded"], voice=voice, language=lang)  # Say too many attempts message.
        else:  # If attempts remain.
            response.say(MESSAGES[lang]["stop_too_long"], voice=voice, language=lang)  # Say stop too long message.
            response.redirect(f'/language?Digits={1 if lang == "en" else 2}&attempt={next_attempt}')  # Redirect to language selection with updated attempt.
        logger.info(f"Stop ID too long, attempt {next_attempt}")  # Log the attempt.
    else:  # If stop ID is valid.
        stop_id = stop_id.zfill(4)  # Pad stop ID with zeros to 4 digits.
        gather = Gather(input='dtmf', finish_on_key='#', num_digits=4, action=f'/gather_route?stop_id={stop_id}&lang={lang}&bus_count=1&attempt=1', timeout=5)  # Create Gather for route number.
        gather.say(MESSAGES[lang]["route_prompt"], voice=voice, language=lang)  # Prompt for route number in selected language.
        response.append(gather)  # Add the gather prompt to the response.
        response.say(MESSAGES[lang]["no_route"], voice=voice, language=lang)  # Say "no route" message if no input.
        logger.info(f"Prompting for route, stop_id={stop_id}")  # Log the route prompt.
    return str(response)  # Return the response as a string.

@app.route("/gather_route", methods=["POST"])  # Define route number collection endpoint at "/gather_route" accepting POST requests.
def gather_route():  # Function to handle route number input.
    logger.info("Entering /gather_route")  # Log entry to the gather_route endpoint.
    stop_id = request.args.get('stop_id', '')  # Get the stop ID from query parameters.
    route_id = request.values.get('Digits', None)  # Get the route ID digits entered by the user.
    from_number = request.values.get('From', '')  # Get the caller's phone number from the request.
    lang = request.args.get('lang', 'en')  # Get the language from query parameters, default to English.
    attempt = int(request.args.get('attempt', 1))  # Get the attempt number from query parameters, default to 1.
    bus_count = int(request.args.get('bus_count', 1))  # Get the bus request count from query parameters, default to 1.
    response = VoiceResponse()  # Create a new voice response object.
    voice = "Polly.Joanna" if lang == "en" else "Polly.Lucia"  # Set voice based on language.
    english_voice = "Polly.Joanna"  # Define English voice for mixed-language responses.

    if not from_number:  # Check if caller number is missing.
        response.say(MESSAGES[lang]["error"], voice=voice, language=lang)  # Say error message in selected language.
        response.hangup()  # Hang up the call.
        return str(response)  # Return the response as a string.
    elif not check_rate_limit(from_number):  # Check if the caller is within rate limits.
        response.say(MESSAGES[lang]["limit_reached"], voice=voice, language=lang)  # Say rate limit message in selected language.
        response.hangup()  # Hang up the call.
        return str(response)  # Return the response as a string.
    elif not (route_id and route_id.isdigit()):  # Validate route ID is numeric.
        response.say(MESSAGES[lang]["invalid_route"], voice=voice, language=lang)  # Say invalid route message in selected language.
        response.hangup()  # Hang up the call.
        logger.error(f"Invalid route ID: {route_id}")  # Log the invalid route ID.
        return str(response)  # Return the response as a string.
    elif len(route_id) > 3:  # Check if route ID exceeds 3 digits.
        next_attempt = attempt + 1  # Increment attempt counter.
        if next_attempt > MAX_ATTEMPTS:  # Check if max attempts exceeded.
            response.say(MESSAGES[lang]["route_attempts_exceeded"], voice=voice, language=lang)  # Say too many attempts message.
        else:  # If attempts remain.
            response.say(MESSAGES[lang]["route_too_long"], voice=voice, language=lang)  # Say route too long message.
            response.redirect(f'/gather_route?stop_id={stop_id}&lang={lang}&bus_count={bus_count}&attempt={next_attempt}')  # Redirect to retry route input.
        logger.info(f"Route ID too long, attempt {next_attempt}")  # Log the attempt.
        return str(response)  # Return the response as a string.

    stop_id_stripped = str(int(stop_id))  # Strip leading zeros from stop ID for display.
    prediction = get_prediction(stop_id, route_id, lang)  # Get bus prediction for the stop and route.
    if prediction == "no_prediction":  # Check if no prediction is available.
        response.say(f"{MESSAGES[lang]['prediction_prefix'].format(stop_id=stop_id_stripped)}{MESSAGES[lang]['no_prediction']}", voice=voice, language=lang)  # Say no prediction message.
    else:  # If a prediction is available.
        if lang == "es":  # Handle Spanish prediction with mixed language for destination.
            parts = prediction.split('|')  # Split prediction into parts for mixed voice handling.
            response.say(f"{MESSAGES[lang]['prediction_prefix'].format(stop_id=stop_id_stripped)}{parts[0]}", voice=voice, language=lang)  # Say prefix and route in Spanish.
            response.say(parts[1], voice=english_voice, language="en")  # Say destination in English voice.
            response.say(parts[2], voice=voice, language=lang)  # Say arrival time in Spanish.
        else:  # Handle English prediction.
            response.say(f"{MESSAGES[lang]['prediction_prefix'].format(stop_id=stop_id_stripped)}{prediction}", voice=voice, language=lang)  # Say full prediction in English.

    if bus_count < MAX_BUS_REQUESTS:  # Check if more bus requests are allowed.
        gather = Gather(input='dtmf', num_digits=1, action=f'/more_routes?stop_id={stop_id}&lang={lang}&bus_count={bus_count}', timeout=5)  # Create Gather for yes/no choice.
        gather.say(MESSAGES[lang]["more_prompt"], voice=voice, language=lang)  # Prompt for more predictions.
        response.append(gather)  # Add the gather prompt to the response.
        response.say(MESSAGES[lang]["no_more_response"], voice=voice, language=lang)  # Say "no response" message if no input.
    else:  # If max bus requests reached.
        response.say(MESSAGES[lang]["request_limit"], voice=voice, language=lang)  # Say farewell message.
    logger.info(f"Returning prediction for stop={stop_id}, route={route_id}, bus_count={bus_count}")  # Log the prediction response.
    return str(response)  # Return the response as a string.

@app.route("/more_routes", methods=["POST"])  # Define additional route request endpoint at "/more_routes" accepting POST requests.
def more_routes():  # Function to handle additional bus prediction requests.
    logger.info("Entering /more_routes")  # Log entry to the more_routes endpoint.
    choice = request.values.get('Digits', None)  # Get the user's yes/no choice (1 or 2).
    from_number = request.values.get('From', '')  # Get the caller's phone number from the request.
    stop_id = request.args.get('stop_id', '')  # Get the stop ID from query parameters.
    lang = request.args.get('lang', 'en')  # Get the language from query parameters, default to English.
    bus_count = int(request.args.get('bus_count', 1))  # Get the bus request count from query parameters, default to 1.
    response = VoiceResponse()  # Create a new voice response object.
    voice = "Polly.Joanna" if lang == "en" else "Polly.Lucia"  # Set voice based on language.

    if not from_number:  # Check if caller number is missing.
        response.say(MESSAGES[lang]["error"], voice=voice, language=lang)  # Say error message in selected language.
        response.hangup()  # Hang up the call.
    elif not check_rate_limit(from_number):  # Check if the caller is within rate limits.
        response.say(MESSAGES[lang]["limit_reached"], voice=voice, language=lang)  # Say rate limit message in selected language.
        response.hangup()  # Hang up the call.
    elif not (choice and choice.isdigit()):  # Validate choice is numeric.
        response.say(MESSAGES[lang]["invalid_choice"], voice=voice, language=lang)  # Say invalid choice message in selected language.
        response.hangup()  # Hang up the call.
    elif choice == "1":  # If user chooses yes (more predictions).
        next_bus_count = bus_count + 1  # Increment bus request count.
        gather = Gather(input='dtmf', finish_on_key='#', num_digits=4, action=f'/gather_route?stop_id={stop_id}&lang={lang}&bus_count={next_bus_count}&attempt=1', timeout=5)  # Create Gather for new route.
        gather.say(MESSAGES[lang]["more_route_prompt"], voice=voice, language=lang)  # Prompt for another route number.
        response.append(gather)  # Add the gather prompt to the response.
        response.say(MESSAGES[lang]["no_route"], voice=voice, language=lang)  # Say "no route" message if no input.
    else:  # If user chooses no (or invalid, assumed as no).
        response.say(MESSAGES[lang]["request_limit"], voice=voice, language=lang)  # Say farewell message.
    logger.info(f"Processed more_routes, choice={choice}")  # Log the choice processing.
    return str(response)  # Return the response as a string.

if __name__ == "__main__":  # Check if the script is run directly.
    app.run(debug=False, host="0.0.0.0", port=5000)  # Run the Flask app on all interfaces, port 5000, without debug mode.
