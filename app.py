import os
import logging
import json
from flask import Flask, request, render_template, session, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime, timedelta
import requests
from threading import Lock

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

opt_in_file_path = 'opted_in_users.json'

def load_opted_in_users():
    """Load opt-in map from JSON; create empty file if missing (no overwrite)."""
    if os.path.exists(opt_in_file_path):
        try:
            with open(opt_in_file_path, 'r') as f:
                data = json.load(f)
                # Ensure keys are strings
                return {str(k): bool(v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"Failed to read {opt_in_file_path}: {e}")
            return {}
    # Create an empty file so future saves succeed, but return empty mapping
    try:
        with open(opt_in_file_path, 'w') as f:
            json.dump({}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to create {opt_in_file_path}: {e}")
    return {}

def save_opted_in_users(users_dict):
    """Persist opt-in map safely."""
    try:
        with open(opt_in_file_path, 'w') as f:
            json.dump(users_dict, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save {opt_in_file_path}: {e}")

opted_in_users = load_opted_in_users()

# ========== LANGUAGE MESSAGES (web chat only) ==========
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

# ========== Helper: Rate Limiting ==========
def check_rate_limit(phone_number):
    now = datetime.utcnow()
    with rate_limit_lock:
        if phone_number not in request_counts:
            request_counts[phone_number] = []
        # keep only last 60 minutes of timestamps
        request_counts[phone_number] = [
            t for t in request_counts[phone_number] if now - t < timedelta(hours=1)
        ]
        if len(request_counts[phone_number]) < MESSAGE_LIMIT:
            request_counts[phone_number].append(now)
            return True
        return False

# ========== Helper: Predictions ==========
def get_prediction(stop_id: str, route_id: str = None, lang: str = "en", web_mode: bool = False) -> str | list[str]:
    logger.info(f"Fetching prediction for stop_id={stop_id}, route_id={route_id}, lang={lang}, web_mode={web_mode}")
    padded_stop_id = str(stop_id).zfill(4)
    params = {"key": API_KEY, "rtpidatafeed": RTPIDATAFEED, "stpid": padded_stop_id, "format": "json", "max": 99}

    try:
        r = requests.get(BASE_URL, params=params, timeout=5)
        r.raise_for_status()
        data = r.json()
        if "bustime-response" not in data or "prd" not in data["bustime-response"]:
            return "No predictions available for this stop."

        predictions = data["bustime-response"]["prd"] or []
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
                        # suppress very long waits in web list mode
                        continue
                    arrival_text = f"{arrival_min} {minutes_label}"
                except (ValueError, TypeError):
                    arrival_text = arrival

            grouped.setdefault(key, []).append(arrival_text)

        if not grouped:
            return "No buses expected in the next 45 minutes."

        results = [f"🚌 ETA for Stop ID {stop_id}:\n"]
        for key, times in grouped.items():
            if " - " in key:
                route, destination = key.split(" - ", 1)
            else:
                route, destination = key, ""
            times_text = ', '.join(times[:-1]) + f" and {times[-1]}" if len(times) > 1 else times[0]
            results.append(f"{route}\n{destination}\n{times_text}\n")

        return results if web_mode else "\n".join(results)

    except requests.RequestException as e:
        logger.error(f"API request failed: {e}")
        return "Network error. Try again."
    except ValueError:
        logger.error("Invalid API response")
        return "Invalid API response."

# ========== ROUTE: WEB CHAT HOME ==========
@app.route("/", methods=["GET", "POST"])
def web_home():
    if "chat_history" not in session:
        session["chat_history"] = []

    if request.method == "POST":
        user_input = (request.form.get("message", "") or "").strip()

        if user_input:
            if not (user_input.isdigit() and 1 <= len(user_input) <= 4):
                session["chat_history"].append({"sender": "user", "text": user_input})

            if user_input.isdigit() and 1 <= len(user_input) <= 4:
                session["chat_history"].append({"sender": "bot", "text": f"🔎 Searching predictions for Stop ID {user_input}..."})
                predictions = get_prediction(user_input, web_mode=True)
                if isinstance(predictions, str):
                    session["chat_history"].append({"sender": "bot", "text": predictions})
                else:
                    for line in predictions:
                        session["chat_history"].append({"sender": "bot", "text": line})
            else:
                session["chat_history"].append({
                    "sender": "bot",
                    "text": "🤖 I'm a simple bus assistant! Please enter a numeric Stop ID (1–4 digits) to get bus predictions!"
                })

    return render_template("home.html", chat_history=session.get("chat_history", []))

# ========== ROUTE: CLEAR CHAT ==========
@app.route("/clear", methods=["POST"])
def clear_chat():
    session.pop("chat_history", None)
    return ("", 204)

# ========== ROUTE: BACKGROUND PREDICTION REFRESH ==========
@app.route("/refresh", methods=["POST"])
def refresh_predictions():
    if not session.get("chat_history"):
        return jsonify(success=False)

    last_user_input = next((e["text"] for e in reversed(session["chat_history"]) if e["sender"] == "user"), None)
    if last_user_input and last_user_input.isdigit():
        predictions = get_prediction(last_user_input, web_mode=True)
        session["chat_history"] = [m for m in session["chat_history"] if m["sender"] != "bot"]
        if isinstance(predictions, str):
            session["chat_history"].append({"sender": "bot", "text": predictions})
        else:
            for line in predictions:
                session["chat_history"].append({"sender": "bot", "text": line})
        return jsonify(success=True)

    return jsonify(success=False)

# ========== ROUTE: TWILIO SMS BOT (COST-OPTIMIZED, PRESERVES OPT-INS + PAGINATION) ==========
from time import time

# Session store for paginated predictions: { from_number: {"pages": [...], "idx": 1, "ts": 1234567890} }
more_sessions = {}
MORE_TIMEOUT_SEC = 10 * 60  # 10 minutes window to request MORE

@app.route("/bot", methods=["POST"])
def bot():
    incoming_msg_raw = request.values.get('Body', '')
    incoming_msg = (incoming_msg_raw or '').strip()
    incoming_up = incoming_msg.upper()
    from_number = (request.values.get('From', '') or '').strip()
    resp = MessagingResponse()

    # --- helpers to keep replies in 1 SMS segment (GSM-7 ~160 chars) ---
    def _sanitize_ascii(s: str) -> str:
        repl = {
            "\u2018": "'", "\u2019": "'", "\u201C": '"', "\u201D": '"',
            "\u2013": "-", "\u2014": "-", "\u2026": "...",
            "\u00A0": " ", "\u200B": ""  # nbsp, zero-width
        }
        for k, v in repl.items():
            s = s.replace(k, v)
        # Drop any remaining non-ASCII just in case
        return ''.join(ch if ord(ch) < 128 else ' ' for ch in s)

    def _shorten_to_160(s: str) -> str:
        s = _sanitize_ascii(s).strip()
        return s if len(s) <= 160 else (s[:157].rstrip() + "...")

    def _make_pages(full_text: str, headroom: int = 160):
        """
        Split long predictions into <= headroom char pages at word boundaries.
        Adds a short suffix " Reply MORE for next." to first n-1 pages (fits within 160).
        """
        text = _sanitize_ascii(full_text).strip()
        words = text.split()
        pages, cur = [], ""
        # Reserve space for suffix on non-final pages
        suffix = " Reply MORE for next."
        for w in words:
            candidate = (cur + " " + w).strip()
            limit = headroom if cur == "" else headroom
            # try to pack words; if adding this word would exceed limit (with suffix if not final), cut page
            # We don't know final page yet; we build tentatively, then append suffix later where needed
            if len(candidate) > limit:
                pages.append(cur)
                cur = w
            else:
                cur = candidate
        if cur:
            pages.append(cur)

        # If only one page, return as-is
        if len(pages) <= 1:
            return [pages[0]]

        # Rebuild with suffix on all but last, ensuring <=160 by trimming if needed
        final_pages = []
        for i, p in enumerate(pages):
            if i < len(pages) - 1:
                base = p
                # Add suffix, trimming if needed to keep <=160
                needed = base + suffix
                if len(needed) <= 160:
                    final_pages.append(needed)
                else:
                    # Trim base to fit suffix
                    trim_len = 160 - len(suffix)
                    final_pages.append((base[:trim_len].rstrip()) + suffix)
            else:
                final_pages.append(_shorten_to_160(p))
        return final_pages

    def send_short(text: str):
        resp.message(_shorten_to_160(text))
        return str(resp)

    # --- MORE flow: deliver next page if session exists and not expired ---
    def handle_more():
        sess = more_sessions.get(from_number)
        now = time()
        if not sess or (now - sess.get("ts", 0)) > MORE_TIMEOUT_SEC:
            more_sessions.pop(from_number, None)
            return send_short("No more results. Send a Stop ID (1-4 digits).")
        pages = sess["pages"]
        idx = sess["idx"]
        if idx >= len(pages):
            more_sessions.pop(from_number, None)
            return send_short("Done. Send another Stop ID.")
        # send next page
        msg = pages[idx]
        sess["idx"] = idx + 1
        sess["ts"] = now
        return send_short(msg)

    if not from_number:
        return send_short("Error: No sender.")

    # ---- ALWAYS HONOR KEYWORDS (CTIA/TCPA) ----
    if incoming_up in {"STOP", "UNSUBSCRIBE", "END", "CANCEL", "QUIT"}:
        opted_in_users[from_number] = False
        save_opted_in_users(opted_in_users)
        more_sessions.pop(from_number, None)
        return send_short("You have opted out of RTS. Reply START or YES to rejoin.")

    if incoming_up in {"START", "YES"}:
        opted_in_users[from_number] = True
        save_opted_in_users(opted_in_users)
        more_sessions.pop(from_number, None)
        return send_short("Subscribed. Send Stop ID (1-4 digits).")

    # ---- OPTED OUT: remind and exit (no other messages) ----
    if opted_in_users.get(from_number, True) is False:
        return send_short("Opted out. Reply START or YES to rejoin.")

    # ---- NO FORCED WELCOME/OPT-IN FOR NEW NUMBERS ----
    # Unknown numbers can query directly (cost saver).

    # ---- RATE LIMIT ----
    if not check_rate_limit(from_number):
        return send_short("Limit reached (8/hr). Try later.")

    # ---- MORE pagination request ----
    if incoming_up == "MORE":
        return handle_more()

    # ---- HANDLE STOP ID ----
    msg_clean = incoming_up.replace(' ', '')
    if msg_clean.isdigit() and 1 <= len(msg_clean) <= 4:
        # Get full prediction text (could be long for transfer stations)
        full = get_prediction(msg_clean)

        # Build <=160-char pages; first page includes "Reply MORE for next." when needed
        pages = _make_pages(full, headroom=160)

        # Start a session if multiple pages
        if len(pages) > 1:
            more_sessions[from_number] = {"pages": pages, "idx": 1, "ts": time()}
        else:
            more_sessions.pop(from_number, None)

        return send_short(pages[0])

    # ---- INVALID INPUT ----
    return send_short("Invalid. Send Stop ID (1-4 digits).")


# ========== RUN APP ==========
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
