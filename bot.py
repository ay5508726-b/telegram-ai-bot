import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

TG_TOKEN = os.environ["TELEGRAM_TOKEN"].strip()
GEMINI_KEY = os.environ["GEMINI_API_KEY"].strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Amit Yadav").strip()

TG = f"https://api.telegram.org/bot{TG_TOKEN}"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

WELCOME = "Hello, welcome to Vidya Verse!"
ADMIN_REPLY = f"Iske liye please {ADMIN_NAME} (admin) se baat karo, wo aapki better help kar denge."
PROBLEM_REPLY = f"Aapki problem note kar li gayi hai. Admin ({ADMIN_NAME}) isse jald se jald theek karne ki koshish karenge. Thoda sabr rakho."
SCAM_REPLY = "Warning: Scam, fraud ya spam yahan bilkul allowed nahi hai. Agar ye dobara hua to aapko block karke group se remove kar diya jayega."
ERROR_REPLY = "Abhi thoda issue aa raha hai, thodi der baad try karo."

SYSTEM_PROMPT = f"""You are the friendly helper of the "Vidya Verse" community on Telegram. The admin of this community is {ADMIN_NAME}.

STYLE
- Talk in simple Hinglish (Hindi written in English letters mixed with easy English words), like a friendly senior. Keep replies short, 2 to 4 lines. No headings, no bullet lists, no markdown.
- Sound natural and human. Never mention Gemini, Google, AI models or "as an AI". Never claim to be a human. If someone asks who you are, say you are the Vidya Verse helper bot.

WHAT YOU DO
- Discuss and answer general doubts: studies, general knowledge, simple everyday questions and friendly chit-chat.
- Understand the person's doubt by talking. Ask one short follow-up question if needed.

SPECIAL REPLIES (reply with exactly one word, nothing else)
- SCAM : the person is trying to scam or spam, for example asking for money, OTP, UPI or bank details, fake offers, "double your money", investment or crypto lures, phishing links, promoting other channels or groups, fake giveaways, or impersonating the admin.
- PROBLEM : the person reports a problem with an app, website, account, payment, course or any service.
- ADMIN : the person asks you to bring or find a link, app, website, file or download, asks you to write code or do a task for them, or raises a serious matter (complaint, medical, legal, money emergency).

NEVER
- Never give links or URLs. Never write code. Never provide apps, websites, files or downloads.
"""

GROUP_NOTE = """
GROUP RULE
- You are in a group. Messages arrive as "Name: message".
- If a message is only casual talk between members and does not need your answer (and is not a scam), reply with exactly: SKIP
"""

PRIVATE_NOTE = """
This is a private chat. Always reply, never say SKIP.
"""

FIXED = {
    "SCAM": SCAM_REPLY,
    "PROBLEM": PROBLEM_REPLY,
    "ADMIN": ADMIN_REPLY,
}

history = {}


def ask_gemini(chat_id, text, is_group):
    h = history.setdefault(chat_id, [])
    h.append({"role": "user", "parts": [{"text": text}]})
    del h[:-20]
    while h and h[0]["role"] != "user":
        h.pop(0)
    prompt = SYSTEM_PROMPT + (GROUP_NOTE if is_group else PRIVATE_NOTE)
    r = None
    try:
        r = requests.post(
            GEMINI_URL,
            headers={"x-goog-api-key": GEMINI_KEY},
            json={
                "systemInstruction": {"parts": [{"text": prompt}]},
                "contents": h,
            },
            timeout=60,
        )
        parts = r.json()["candidates"][0]["content"]["parts"]
        reply = "".join(p.get("text", "") for p in parts).strip()
    except Exception as e:
        h.pop()
        print("Gemini error:", e, r.text[:300] if r is not None else "")
        return None

    tag = reply.strip().strip(".!*`").upper()
    if tag == "SKIP":
        h.pop()
        return None if is_group else ERROR_REPLY
    if tag in FIXED:
        reply = FIXED[tag]
    else:
        low = reply.lower()
        if "http" in low or "www." in low or "```" in reply:
            reply = ADMIN_REPLY
    h.append({"role": "model", "parts": [{"text": reply}]})
    return reply


def send(chat_id, text, reply_to=None):
    payload = {"chat_id": chat_id, "text": text[:4000]}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    requests.post(f"{TG}/sendMessage", json=payload, timeout=30)


def handle(msg):
    chat = msg["chat"]
    chat_id = chat["id"]
    is_group = chat.get("type") in ("group", "supergroup")

    new_members = msg.get("new_chat_members")
    if new_members:
        humans = [m for m in new_members if not m.get("is_bot")]
        if humans:
            names = ", ".join(m.get("first_name", "friend") for m in humans)
            send(chat_id, f"Hello {names}, welcome to Vidya Verse!")
        return

    text = msg.get("text")
    sender = msg.get("from", {})
    if not text or sender.get("is_bot"):
        return

    parts = text.split()
    cmd = parts[0].split("@")[0].lower() if parts else ""

    if cmd == "/start":
        send(chat_id, WELCOME)
    elif cmd == "/reset":
        history.pop(chat_id, None)
        send(chat_id, "Baat-cheet reset ho gayi.")
    elif is_group:
        name = sender.get("first_name", "User")
        reply = ask_gemini(chat_id, f"{name}: {text}", True)
        if reply:
            send(chat_id, reply, msg["message_id"])
    else:
        requests.post(f"{TG}/sendChatAction", json={"chat_id": chat_id, "action": "typing"}, timeout=10)
        reply = ask_gemini(chat_id, text, False)
        send(chat_id, reply or ERROR_REPLY)


def poll():
    offset = 0
    while True:
        try:
            r = requests.get(f"{TG}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=40)
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message")
                if msg:
                    handle(msg)
        except Exception as e:
            print("Poll error:", e)
            time.sleep(5)


class Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), Ping).serve_forever()
