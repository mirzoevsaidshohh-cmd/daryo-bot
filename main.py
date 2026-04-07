import json
import re
import time
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime
import os
from flask import Flask
from threading import Thread

# ---------- CONFIG ----------

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("TOKEN ёфт нашуд")

BASE_URL = f"https://api.telegram.org/bot{TOKEN}/"
DB_PATH = "daryo_telegram_bot.db"

last_update_id = None
chat_states = {}
temp_context = {}

# ---------- FAKE WEB SERVER ----------

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running 🚀"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---------- DATABASE ----------

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS boxes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        source TEXT,
        box_name TEXT,
        pvz_name TEXT,
        date_value TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        box_id INTEGER,
        track_code TEXT,
        is_duplicate INTEGER,
        is_invalid INTEGER
    )
    """)

    conn.commit()
    conn.close()

# ---------- TELEGRAM ----------

def http_get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode())

def http_post(url, data):
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=encoded)
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

def send_message(chat_id, text):
    http_post(BASE_URL + "sendMessage", {
        "chat_id": chat_id,
        "text": text
    })

# ---------- BOT LOGIC ----------

def process_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text == "/start":
        send_message(chat_id, "Салом 😊\nХуш омадед!")
    else:
        send_message(chat_id, "Қабул шуд ✅")

# ---------- MAIN LOOP ----------

def run_bot():
    global last_update_id

    init_db()
    print("Bot started...")

    while True:
        try:
            updates = http_get(BASE_URL + "getUpdates", {
                "timeout": 20,
                "offset": last_update_id + 1 if last_update_id else None
            })

            for update in updates.get("result", []):
                last_update_id = update["update_id"]

                if "message" in update:
                    process_message(update["message"])

        except Exception as e:
            print("Error:", e)
            time.sleep(3)

# ---------- RUN BOTH ----------

if __name__ == "__main__":
    Thread(target=run_web).start()
    run_bot()
