import os
import logging
import json
from typing import Union
from flask import Flask, request, render_template, session, jsonify
from datetime import datetime, timedelta
import requests
from threading import Lock
from time import time

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
opt_in_file_path = "opted_in_users.json"

# ========== CLICKSend OUTBOUND HELPER ==========
def send_clicksend_sms(to, message):
    """Send an SMS reply via ClickSend REST API."""
    CLICKSEND_USERNAME = os.getenv("CLICKSEND_USERNAME")
    CLICKSEND_API_KEY = os.getenv("CLICKSEND_API_KEY")
    if not (CLICKSEND_USERNAME and CLICKSEND_API_KEY):
        logger.error("Missing ClickSend credentials in environment variables.")
        return None

    payload = {"messages": [{"source": "python", "body": message, "to": to}]}
    try:
        r = requests.post(
            "https://rest.clicksend.com/v3/sms/send",
            json=payload,
            auth=(CLICKSEND_USERNAME, CLICKSEND_API_KEY),
            timeout=10,
        )
        r.raise_for_status()
        logger.info(f"✅ Sent SMS to {to}: {message[:60]}...")
        return r.json()
    except Exception as e:
        logger.error(f"ClickSend send failed: {e}")
        return None

# ========== OPT-IN MANAGEMENT ==========
def load_opted_in_users():
    if os.path.exists(opt_in_file_path):
        try:
            with open(opt_in_file_path, "r") as f:
                data = json.load(f)
                return {str(k): bool(v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"Failed to read {opt_in_file_path}: {e}")
            return {}
    try:
        with open(opt_in_file_path, "w") as f:
            json.dump({}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to create {opt_in_file_path}: {e}")
    return {}

def save_opted_in_users(users_dict):
    try:
        with open(opt_in_file_path, "w") as f:
            json.dump(users_dict, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save {opt_in_file_path}: {e}")

opted_in_users = load_opted_in_users()

# ========== RATE LIMIT ==========
def check_rate_limit(phone_number: str) -> bool:
    now = datetime.utcnow()
    with rate_limit_lock:
        if phone_number not in request_counts:
            request_counts[phone_number] = []
        request_counts[phone_number] = [
            t for t in request_counts[phone_number] if now - t < timedelta(hours=1)
        ]
        if len(request_counts[phone_number]) < MESSAGE_LIMIT:
            request_counts[phone_number].append(now)
            return True
        return False

# ========== PREDICTION API ==========
def get_prediction(stop_id: str, route_id: str = None, lang: str = "en", web_mode: bool = False) -> Union[str, list]:
    logger.info(f"Fetching prediction for stop_id={stop_id}, route_id={route_id}, lang={lang}, web_mode={web_mode}")
    padded_stop_id = str(stop_id).zfill(4)
    params = {
        "key": API_KEY,
        "rtpidatafeed": RTPIDATAFEED,
        "stpid": padded_stop_id,
        "format": "json",
        "max": 99,
    }

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
            rt = prd.get("rt", "N/A")
            des = prd.get("des", "N/A")
            if "/" in des:
                des = des.replace("/", f" {direction_word} ")
            key = f"{route_label} {rt} - {des}"
            arrival = prd.get("prdctdn", "N/A")

            if arrival == "DUE":
                arrival_text = due_text
            else:
                try:
                    arrival_min = int(arrival)
                    if web_mode and arrival_min > 45:
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
            times_text = ", ".join(times[:-1]) + f" and {times[-1]}" if len(times) > 1 else times[0]
            results.append(f"{route}\n{destination}\n{times_text}\n")

        return results if web_mode else "\n".join(results)

    except requests.RequestException as e:
        logger.error(f"API request failed: {e}")
        return "Network error. Try again."
    except ValueError:
        logger.error("Invalid API response")
        return "Invalid API response."

# ========== WEB CHAT ==========
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

@app.route("/clear", methods=["POST"])
def clear_chat():
    session.pop("chat_history", None)
    return ("", 204)

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

# ========== SMS HANDLER ==========
more_sessions = {}
MORE_TIMEOUT_SEC = 10 * 60

def _sanitize_ascii(s: str) -> str:
    repl = {"\u2018": "'", "\u2019": "'", "\u201C": '"', "\u201D": '"', "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00A0": " ", "\u200B": ""}
    for k, v in repl.items():
        s = s.replace(k, v)
    return "".join(ch if ord(ch) < 128 else " " for ch in s)

def _shorten_to_160(s: str) -> str:
    s = _sanitize_ascii(s).strip()
    return s if len(s) <= 160 else (s[:157].rstrip() + "...")

def _make_pages(full_text: str, headroom: int = 160):
    text = _sanitize_ascii(full_text).strip()
    words = text.split()
    if not words:
        return ["Invalid API response."]
    pages, cur = [], ""
    suffix = " Reply MORE for next."
    for w in words:
        candidate = (cur + " " + w).strip()
        if len(candidate) > headroom:
            pages.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        pages.append(cur)
    if len(pages) <= 1:
        return [pages[0]]
    final_pages = []
    for i, p in enumerate(pages):
        if i < len(pages) - 1:
            needed = p + suffix
            if len(needed) <= 160:
                final_pages.append(needed)
            else:
                trim_len = 160 - len(suffix)
                final_pages.append((p[:trim_len].rstrip()) + suffix)
        else:
            final_pages.append(_shorten_to_160(p))
    return final_pages

def _handle_more(from_number: str) -> str:
    sess = more_sessions.get(from_number)
    now = time()
    if not sess or (now - sess.get("ts", 0)) > MORE_TIMEOUT_SEC:
        more_sessions.pop(from_number, None)
        return "No more results. Send a Stop ID (1-4 digits)."
    pages = sess["pages"]
    idx = sess["idx"]
    if idx >= len(pages):
        more_sessions.pop(from_number, None)
        return "Done. Send another Stop ID."
    msg = pages[idx]
    sess["idx"] = idx + 1
    sess["ts"] = now
    return msg

def build_reply_text(from_number: str, incoming_text: str) -> (str, bool):
    incoming_up = (incoming_text or "").strip().upper()
    if incoming_up in {"STOP", "UNSUBSCRIBE", "END", "CANCEL", "QUIT"}:
        opted_in_users[from_number] = False
        save_opted_in_users(opted_in_users)
        more_sessions.pop(from_number, None)
        return ("You have opted out of RTS. Reply START or YES to rejoin.", False)
    if incoming_up in {"START", "YES"}:
        opted_in_users[from_number] = True
        save_opted_in_users(opted_in_users)
        more_sessions.pop(from_number, None)
        return ("Subscribed. Send Stop ID (1-4 digits).", True)
    if opted_in_users.get(from_number, True) is False:
        return ("Opted out. Reply START or YES to rejoin.", True)
    if not check_rate_limit(from_number):
        return ("Limit reached (8/hr). Try later.", True)
    if incoming_up == "MORE":
        return (_shorten_to_160(_handle_more(from_number)), True)
    msg_clean = incoming_up.replace(" ", "")
    if msg_clean.isdigit() and 1 <= len(msg_clean) <= 4:
        full = get_prediction(msg_clean)
        pages = _make_pages(full, headroom=160)
        if len(pages) > 1:
            more_sessions[from_number] = {"pages": pages, "idx": 1, "ts": time()}
        else:
            more_sessions.pop(from_number, None)
        return (pages[0], True)
    return ("Invalid. Send Stop ID (1-4 digits).", True)

# ========== UNIFIED WEBHOOK FOR CLICKSEND (HYBRID JSON+FORM) ==========
@app.route("/bot", methods=["POST"])
def bot():
    data = request.get_json(silent=True)
    from_number, body = None, None

    if data:
        logger.info("Inbound format: JSON")
        from_number = (data.get("from") or data.get("From") or "").strip()
        body = (data.get("body") or data.get("Body") or "").strip()
    else:
        logger.info("Inbound format: FORM-ENCODED")
        from_number = (request.form.get("from") or request.values.get("from") or "").strip()
        body = (request.form.get("body") or request.values.get("body") or "").strip()

    logger.info("===== INBOUND SMS RECEIVED =====")
    logger.info(f"From: {from_number}, Body: {body}")
    logger.info(f"Raw form fields: {dict(request.form)}")

    if not from_number:
        logger.error("❌ Missing 'from' number in inbound payload.")
        return jsonify({"reply": "Error: No sender.", "send": False}), 200

    reply_text, should_send = build_reply_text(from_number, body)
    logger.info(f"Processed reply: {reply_text} | should_send={should_send}")

    result = None
    if should_send and from_number and reply_text:
        try:
            logger.info(f"Attempting to send reply via ClickSend to {from_number}...")
            result = send_clicksend_sms(from_number, reply_text)
            logger.info(f"ClickSend API response: {json.dumps(result, indent=2)}")
        except Exception as e:
            logger.error(f"❌ ClickSend send failed: {e}")
    else:
        logger.warning("Skipped sending (should_send=False or missing data).")

    logger.info("===== END INBOUND SMS =====")
    return jsonify({"status": "ok"}), 200

# ========== TEST CLICKSend CONNECTION ==========
@app.route("/test_send")
def test_send():
    to = request.args.get("to")
    msg = request.args.get("msg", "RTS Test message ✅")
    if not to:
        return "Add ?to=+1XXXXXXXXXX to the URL", 400

    result = send_clicksend_sms(to, msg)
    if not result:
        return "❌ Failed to send message. Check your ClickSend credentials.", 500
    return jsonify(result)

# ========== RUN APP ==========
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
