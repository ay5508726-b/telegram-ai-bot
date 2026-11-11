import html
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

TG_TOKEN = os.environ["TELEGRAM_TOKEN"].strip()
GEMINI_KEY = os.environ["GEMINI_API_KEY"].strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7752823256").strip())
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "Amit_yadav_o").strip().lstrip("@")

TG = f"https://api.telegram.org/bot{TG_TOKEN}"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

WELCOME = "Hello, welcome to Vidya Verse!"
ADMIN_REPLY = "Iske liye please Admin se baat karo, wo aapki better help kar denge."
PROBLEM_REPLY = "Aapki problem note kar li gayi hai. Admin isse jald se jald theek karne ki koshish karenge."
SCAM_REPLY = "Warning: Scam, fraud ya spam yahan allowed nahi hai. Dobara hua to aapko block karke group se remove kar diya jayega."
OFFER = "Aap kaho to main hi aapka ye message Admin ke paas bhej dun? Bas 'haan' ya 'nahi' likh do."
SENT_OK = "Done, aapka message Admin tak pahuncha diya hai."
SENT_FAIL = f"Abhi Admin ko message nahi pahunch paya. Aap seedha @{ADMIN_USERNAME} ko likh do."
NO_REPLY = "Theek hai, koi baat nahi. Kuch aur ho to batana."
ERROR_REPLY = "Abhi thoda issue aa raha hai, thodi der baad try karo."

SYSTEM_PROMPT = """You are Lumi, the friendly helper bot of the "Vidya Verse" Telegram community. The community has a person called "Admin". Always call that person just "Admin" and never use any personal name for them.

HOW TO WORK
1. First read the message carefully and understand what the person really wants. Write that in one short line in "analysis" (it is never shown to anyone).
2. Pick the "category".
3. Write "reply" in your own natural words, based on that understanding. Do not copy ready-made lines.

CATEGORIES
- CHAT: normal talk, doubts, studies, general knowledge, everyday questions. Answer the question properly and simply.
- PROBLEM: the person reports a problem with an app, website, account, payment, course or any service. In "reply", tell them in your own words that you understood their problem (mention it briefly) and that Admin will try to fix it as soon as possible.
- SCAM: the person is trying to scam or spam: asking for money, OTP, UPI or bank details, fake offers or giveaways, "double your money", investment or crypto lures, phishing links, promoting other channels or groups, or pretending to be Admin. In "reply", give a firm short warning in your own words that scam, fraud or spam is not allowed here and that if it happens again they will be blocked and removed from the group.
- ADMIN: the person asks you to bring or find a link, app, website, file or download, asks you to write code or do some task for them, or raises a serious matter (complaint, medical, legal, money emergency). In "reply", politely say this is something Admin should handle and they should contact Admin.
- SKIP: only in groups, when the message is just casual talk between members that does not need you. Keep "reply" empty.

STYLE
- Simple Hinglish (Hindi in English letters mixed with easy English words), like a friendly senior. 2 to 4 short lines. No markdown, no bullet lists.
- Sound natural and human. Never mention Gemini, Google, AI models or "as an AI". If asked who you are, say you are Lumi, the Vidya Verse helper bot.
- Never give links or URLs. Never write code. Never suggest or provide apps, websites, files or downloads.
"""

GROUP_NOTE = '\nYou are in a group. Messages arrive as "Name: message".\n'
PRIVATE_NOTE = "\nThis is a private chat, so never use SKIP.\n"

ADMIN_SYSTEM = """You are Lumi, the helper bot of the "Vidya Verse" Telegram community. Admin, the owner of the community, has just given you an instruction in the group.
First understand the instruction properly, then do exactly what it says and write the message that will be posted in the group: answer, explain, announce or warn, whatever Admin asked.
Write in simple Hinglish, in your own words, maximum 5 short lines. No markdown. Never invent links; only repeat a link if Admin wrote it in the instruction. Never mention Gemini, Google or AI models. Call the owner just "Admin"."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "analysis": {"type": "STRING"},
        "category": {"type": "STRING", "enum": ["CHAT", "PROBLEM", "SCAM", "ADMIN", "SKIP"]},
        "reply": {"type": "STRING"},
    },
    "required": ["analysis", "category", "reply"],
    "propertyOrdering": ["analysis", "category", "reply"],
}

CATEGORIES = {"CHAT", "PROBLEM", "SCAM", "ADMIN", "SKIP"}
FALLBACK = {"PROBLEM": PROBLEM_REPLY, "SCAM": SCAM_REPLY, "ADMIN": ADMIN_REPLY}

TRIGGER = re.compile(
    r"^\s*(?:hey|hay|he|hi|hello|हे|हेय)\s*[,!:\-]*\s*(?:lumibot|lumi|lomi|loomi|लोमी|लुमी|लूमी)(?=[\s,:!.\-]|$)[\s,:!.\-]*",
    re.IGNORECASE,
)
ADMIN_WORD = re.compile(r"(?<![A-Za-z])(?:admin|एडमिन)(?![A-Za-z])", re.IGNORECASE)
NAME_WORD = re.compile(r"amit\s*yadav", re.IGNORECASE)

YES_WORDS = {"haan", "han", "haa", "ha", "haanji", "yes", "y", "yep", "ok", "okay", "sure", "bhej", "bhejo",
             "bhejdo", "kardo", "kar", "send", "pls", "please", "जी", "हाँ", "हां", "हा", "भेज", "भेजो"}
NO_WORDS = {"nahi", "nhi", "nahin", "no", "na", "mat", "nope", "नहीं", "नही", "ना"}

history = {}
pending = {}


def fmt(text):
    safe = html.escape(text, quote=False)
    return ADMIN_WORD.sub("<b>Admin</b>", safe)


def send(chat_id, text, reply_to=None, plain=False):
    payload = {"chat_id": chat_id}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    try:
        if plain:
            r = requests.post(f"{TG}/sendMessage", json={**payload, "text": text[:4000]}, timeout=30)
            return r.ok
        r = requests.post(
            f"{TG}/sendMessage",
            json={**payload, "text": fmt(text[:3500]), "parse_mode": "HTML"},
            timeout=30,
        )
        if not r.ok:
            r = requests.post(f"{TG}/sendMessage", json={**payload, "text": text[:4000]}, timeout=30)
        return r.ok
    except Exception as e:
        print("Send error:", e)
        return False


def call_gemini(contents, system, as_json=False):
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents}
    if as_json:
        body["generationConfig"] = {"responseMimeType": "application/json", "responseSchema": SCHEMA}
    r = requests.post(GEMINI_URL, headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=60)
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception:
        print("Gemini error:", r.text[:300])
        raise


def analyze(chat_id, text, is_group):
    h = history.setdefault(chat_id, [])
    h.append({"role": "user", "parts": [{"text": text}]})
    del h[:-20]
    while h and h[0]["role"] != "user":
        h.pop(0)
    prompt = SYSTEM_PROMPT + (GROUP_NOTE if is_group else PRIVATE_NOTE)
    try:
        raw = call_gemini(h, prompt, as_json=True)
    except Exception:
        h.pop()
        return None

    try:
        data = json.loads(raw)
        cat = str(data.get("category", "CHAT")).strip().upper()
        reply = str(data.get("reply", "")).strip()
    except Exception:
        cat, reply = "CHAT", raw
        if raw.strip().upper().startswith("SKIP"):
            cat = "SKIP"
    if cat not in CATEGORIES:
        cat = "CHAT"

    if cat == "SKIP":
        if is_group:
            h.pop()
            return "SKIP", ""
        cat, reply = "CHAT", reply or "Ji, boliye, kya poochna hai?"

    low = reply.lower()
    if "http" in low or "www." in low or "```" in reply:
        cat, reply = "ADMIN", ADMIN_REPLY
    if not reply:
        reply = FALLBACK.get(cat, "Ji, boliye, kya poochna hai?")
    reply = NAME_WORD.sub("Admin", reply)

    h.append({"role": "model", "parts": [{"text": reply}]})
    return cat, reply


def yes_no(text):
    words = re.findall(r"[A-Za-z0-9\u0900-\u097F]+", text.lower())
    if not words or len(words) > 6:
        return None
    if any(w in NO_WORDS for w in words):
        return "no"
    if any(w in YES_WORDS for w in words):
        return "yes"
    return None


def forward_to_admin(p):
    lines = ["Problem report - Vidya Verse"]
    if p["title"]:
        lines.append(f"Group: {p['title']}")
    who = p["name"] + (f" (@{p['username']})" if p["username"] else "")
    lines.append(f"User: {who}")
    lines.append(f"Message: {p['text']}")
    if p["link"]:
        lines.append(f"Link: {p['link']}")
    return send(ADMIN_ID, "\n".join(lines), plain=True)


def admin_command(chat_id, instruction, msg_id):
    if not instruction:
        send(chat_id, "Ji Admin, boliye, kya karna hai?", msg_id)
        return
    recent = []
    for item in history.get(chat_id, [])[-6:]:
        who = "Lumi" if item["role"] == "model" else "Member"
        recent.append(f"{who}: {item['parts'][0]['text']}")
    context = "\n".join(recent)
    content = f"Recent group chat:\n{context}\n\nADMIN INSTRUCTION: {instruction}"
    try:
        reply = call_gemini([{"role": "user", "parts": [{"text": content}]}], ADMIN_SYSTEM)
    except Exception:
        send(chat_id, ERROR_REPLY, msg_id)
        return
    reply = NAME_WORD.sub("Admin", reply)
    send(chat_id, reply or "Ji Admin, ho gaya.", msg_id)


def respond(chat_id, key, msg, sender, res):
    cat, reply = res
    if cat == "SKIP":
        return
    send(chat_id, reply, msg["message_id"])
    if cat == "PROBLEM":
        send(chat_id, OFFER)
        chat = msg["chat"]
        link = ""
        if str(chat_id).startswith("-100"):
            link = f"https://t.me/c/{str(chat_id)[4:]}/{msg['message_id']}"
        pending[key] = {
            "t": time.time(),
            "title": chat.get("title", ""),
            "name": sender.get("first_name", "User"),
            "username": sender.get("username", ""),
            "text": msg.get("text", ""),
            "link": link,
        }


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

    user_id = sender.get("id")
    is_admin = user_id == ADMIN_ID
    msg_id = msg["message_id"]
    key = (chat_id, user_id)

    parts = text.split()
    cmd = parts[0].split("@")[0].lower() if parts else ""
    if cmd == "/start":
        send(chat_id, WELCOME)
        return
    if cmd == "/reset":
        history.pop(chat_id, None)
        send(chat_id, "Baat-cheet reset ho gayi.")
        return

    p = pending.get(key)
    if p and time.time() - p["t"] > 600:
        pending.pop(key, None)
        p = None
    if p:
        ans = yes_no(text)
        if ans == "yes":
            pending.pop(key, None)
            send(chat_id, SENT_OK if forward_to_admin(p) else SENT_FAIL, msg_id)
            return
        pending.pop(key, None)
        if ans == "no":
            send(chat_id, NO_REPLY, msg_id)
            return

    if is_group and is_admin:
        m = TRIGGER.match(text)
        if m:
            admin_command(chat_id, text[m.end():].strip(), msg_id)
        return

    if is_group:
        res = analyze(chat_id, f"{sender.get('first_name', 'User')}: {text}", True)
        if res:
            respond(chat_id, key, msg, sender, res)
    else:
        requests.post(f"{TG}/sendChatAction", json={"chat_id": chat_id, "action": "typing"}, timeout=10)
        res = analyze(chat_id, text, False)
        if res:
            respond(chat_id, key, msg, sender, res)
        else:
            send(chat_id, ERROR_REPLY)


def poll():
    offset = 0
    while True:
        try:
            r = requests.get(f"{TG}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=40)
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message")
                if msg:
                    try:
                        handle(msg)
                    except Exception as e:
                        print("Handle error:", e)
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
