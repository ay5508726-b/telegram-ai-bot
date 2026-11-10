import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

TG_TOKEN = os.environ["TELEGRAM_TOKEN"].strip()
GEMINI_KEY = os.environ["GEMINI_API_KEY"].strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

TG = f"https://api.telegram.org/bot{TG_TOKEN}"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
SYSTEM_PROMPT = "You are a friendly AI assistant. Reply in the same language as the user (Hindi/Hinglish/English). Keep answers clear and short."

history = {}


def ask_gemini(chat_id, text):
    h = history.setdefault(chat_id, [])
    h.append({"role": "user", "parts": [{"text": text}]})
    del h[:-20]
    r = None
    try:
        r = requests.post(
            GEMINI_URL,
            headers={"x-goog-api-key": GEMINI_KEY},
            json={
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": h,
            },
            timeout=60,
        )
        reply = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        h.pop()
        detail = r.text[:300] if r is not None else ""
        print("Gemini error:", e, detail)
        return "Error: " + str(e) + " | model=" + MODEL + " | " + detail
    h.append({"role": "model", "parts": [{"text": reply}]})
    return reply


def send(chat_id, text):
    requests.post(f"{TG}/sendMessage", json={"chat_id": chat_id, "text": text[:4000]}, timeout=30)


def poll():
    offset = 0
    while True:
        try:
            r = requests.get(f"{TG}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=40)
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message")
                if not msg or "text" not in msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = msg["text"]
                if text == "/start":
                    send(chat_id, "hello, welcome to vidya verse.")
                elif text == "/reset":
                    history.pop(chat_id, None)
                    send(chat_id, "Baat-cheet reset ho gayi.")
                else:
                    requests.post(f"{TG}/sendChatAction", json={"chat_id": chat_id, "action": "typing"}, timeout=10)
                    send(chat_id, ask_gemini(chat_id, text))
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
