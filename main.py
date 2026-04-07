import json
import re
import time
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import os

TOKEN = "8793536113:AAHs32IIkY0mtuuM3aGeZKti9WwyNIWTXlo"
BASE_URL = f"https://api.telegram.org/bot{TOKEN}/"
DB_PATH = "daryo_telegram_bot.db"

last_update_id = None
chat_states = {}
temp_context = {}


class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()


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
        chat_id INTEGER NOT NULL,
        source TEXT NOT NULL,
        box_name TEXT NOT NULL,
        pvz_name TEXT,
        date_value TEXT,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        box_id INTEGER NOT NULL,
        track_code TEXT NOT NULL,
        is_duplicate INTEGER DEFAULT 0,
        is_invalid INTEGER DEFAULT 0,
        FOREIGN KEY(box_id) REFERENCES boxes(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS comparisons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        box_name TEXT NOT NULL,
        pvz_name TEXT,
        warehouse_count INTEGER NOT NULL,
        pvz_count INTEGER NOT NULL,
        matched_count INTEGER NOT NULL,
        warehouse_only_count INTEGER NOT NULL,
        pvz_only_count INTEGER NOT NULL,
        compared_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


def http_get(url, params=None):
    if params:
        params = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def http_post(url, data):
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def send_chat_action(chat_id, action="typing"):
    try:
        http_post(BASE_URL + "sendChatAction", {
            "chat_id": chat_id,
            "action": action
        })
    except Exception:
        pass


def send_message(chat_id, text, keyboard=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)
    http_post(BASE_URL + "sendMessage", payload)


def split_message(text, limit=3500):
    if len(text) <= limit:
        return [text]

    parts = []
    current = ""

    for line in text.splitlines(True):
        if len(current) + len(line) > limit:
            if current:
                parts.append(current)
                current = ""
        current += line

    if current:
        parts.append(current)

    return parts


def send_long_message(chat_id, text, keyboard=None):
    parts = split_message(text)
    for i, part in enumerate(parts):
        send_message(chat_id, part, keyboard=keyboard if i == len(parts) - 1 else None)


def build_keyboard():
    return {
        "keyboard": [
            [{"text": "📦 Склад"}, {"text": "🏪 ПВЗ"}],
            [{"text": "🔍 Проверка"}, {"text": "📊 Статус"}],
            [{"text": "🗂 Коробкаҳо"}, {"text": "🔎 Ҷустуҷӯи трек"}],
            [{"text": "🧹 Очистить"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }


def get_updates():
    global last_update_id
    params = {
    "timeout": 20,
    "limit": 1
    }
    if last_update_id is not None:
        params["offset"] = last_update_id + 1
    return http_get(BASE_URL + "getUpdates", params)


def skip_old_updates():
    global last_update_id
    try:
        data = http_get(BASE_URL + "getUpdates", {"timeout": 1})
        results = data.get("result", [])
        if results:
            last_update_id = results[-1]["update_id"]
            print("Old updates skipped up to:", last_update_id)
    except Exception as e:
        print("Skip old updates error:", e)


def is_box_line(line):
    line = line.strip().lower()
    patterns = [
        r"^коробка\s*#?\s*\d+.*$",
        r"^каробкаи\s*#?\s*\d+.*$",
        r"^box\s*#?\s*\d+.*$",
    ]
    return any(re.fullmatch(p, line) for p in patterns)


def is_date_line(line):
    return bool(re.fullmatch(r"\d{2}\.\d{2}\.\d{2,4}", line.strip()))


def is_track_like(line):
    line = line.strip()
    if not line or " " in line:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9\-]{8,35}", line))


def normalize_box_name(raw):
    return " ".join(raw.strip().split())


def parse_input_block(text):
    lines = [x.strip() for x in text.splitlines()]
    lines = [x for x in lines if x]

    box_name = None
    date_value = None
    tracks = []
    invalid = []

    idx = 0

    if idx < len(lines) and is_box_line(lines[idx]):
        box_name = normalize_box_name(lines[idx])
        idx += 1

    if idx < len(lines) and is_date_line(lines[idx]):
        date_value = lines[idx]
        idx += 1

    seen = set()
    duplicates = set()

    for line in lines[idx:]:
        if is_track_like(line):
            track = line.upper()
            if track in seen:
                duplicates.add(track)
            seen.add(track)
            tracks.append(track)
        else:
            invalid.append(line)

    return {
        "box_name": box_name or "Без названия коробки",
        "date_value": date_value,
        "tracks": sorted(set(tracks)),
        "duplicates": sorted(duplicates),
        "invalid": invalid
    }


def save_box(chat_id, source, parsed, pvz_name=None):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO boxes (chat_id, source, box_name, pvz_name, date_value, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        chat_id,
        source,
        parsed["box_name"],
        pvz_name,
        parsed["date_value"],
        datetime.now().isoformat(timespec="seconds")
    ))
    box_id = cur.lastrowid

    duplicate_set = set(parsed["duplicates"])

    for track in parsed["tracks"]:
        cur.execute("""
        INSERT INTO tracks (box_id, track_code, is_duplicate, is_invalid)
        VALUES (?, ?, ?, 0)
        """, (box_id, track, 1 if track in duplicate_set else 0))

    for bad in parsed["invalid"]:
        cur.execute("""
        INSERT INTO tracks (box_id, track_code, is_duplicate, is_invalid)
        VALUES (?, ?, 0, 1)
        """, (box_id, bad))

    conn.commit()
    conn.close()


def get_box_tracks(box_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
    SELECT track_code, is_duplicate, is_invalid
    FROM tracks
    WHERE box_id = ?
    """, (box_id,))
    rows = cur.fetchall()
    conn.close()

    normal = []
    duplicates = []
    invalid = []

    for track_code, is_duplicate, is_invalid in rows:
        if is_invalid:
            invalid.append(track_code)
        else:
            normal.append(track_code)
            if is_duplicate:
                duplicates.append(track_code)

    return {
        "normal": sorted(set(normal)),
        "duplicates": sorted(set(duplicates)),
        "invalid": invalid
    }


def get_all_box_names(chat_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
    SELECT DISTINCT box_name
    FROM boxes
    WHERE chat_id = ?
    ORDER BY id DESC
    """, (chat_id,))
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


def get_latest_box_by_name(chat_id, source, box_name):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
    SELECT id, box_name, pvz_name, date_value
    FROM boxes
    WHERE chat_id = ? AND source = ? AND box_name = ?
    ORDER BY id DESC
    LIMIT 1
    """, (chat_id, source, box_name))
    row = cur.fetchone()
    conn.close()
    return row


def save_comparison(chat_id, box_name, pvz_name, warehouse_count, pvz_count, matched_count, warehouse_only_count, pvz_only_count):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO comparisons
    (chat_id, box_name, pvz_name, warehouse_count, pvz_count, matched_count, warehouse_only_count, pvz_only_count, compared_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        chat_id, box_name, pvz_name,
        warehouse_count, pvz_count, matched_count,
        warehouse_only_count, pvz_only_count,
        datetime.now().isoformat(timespec="seconds")
    ))
    conn.commit()
    conn.close()


def compare_box(chat_id, box_name):
    wh = get_latest_box_by_name(chat_id, "warehouse", box_name)
    pvz = get_latest_box_by_name(chat_id, "pvz", box_name)

    if not wh:
        return "❌ Барои ин коробка маълумоти склад нест."
    if not pvz:
        return "❌ Барои ин коробка маълумоти ПВЗ нест."

    wh_tracks_info = get_box_tracks(wh[0])
    pvz_tracks_info = get_box_tracks(pvz[0])

    wh_set = set(wh_tracks_info["normal"])
    pvz_set = set(pvz_tracks_info["normal"])

    matched = sorted(wh_set & pvz_set)
    warehouse_only = sorted(wh_set - pvz_set)
    pvz_only = sorted(pvz_set - wh_set)
    duplicates = sorted(set(wh_tracks_info["duplicates"] + pvz_tracks_info["duplicates"]))
    invalid = wh_tracks_info["invalid"] + pvz_tracks_info["invalid"]

    pvz_name = pvz[2] or "ПВЗ"
    date_value = pvz[3] or wh[3] or "не указана"

    save_comparison(
        chat_id,
        box_name,
        pvz_name,
        len(wh_set),
        len(pvz_set),
        len(matched),
        len(warehouse_only),
        len(pvz_only)
    )

    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📦 Коробка: {box_name}")
    lines.append(f"🏪 ПВЗ: {pvz_name}")
    lines.append(f"📅 {date_value}")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"✅ Дар ҳарду ҳаст ({len(matched)}):")
    lines.append("\n".join(matched) if matched else "нест")
    lines.append("")
    lines.append(f"❌ Дар склад ҳаст, дар ПВЗ нест ({len(warehouse_only)}):")
    lines.append("\n".join(warehouse_only) if warehouse_only else "нест")
    lines.append("")
    lines.append(f"⚠️ Дар ПВЗ ҳаст, дар склад нест ({len(pvz_only)}):")
    lines.append("\n".join(pvz_only) if pvz_only else "нест")
    lines.append("")
    lines.append(f"🔁 Дубликат ({len(duplicates)}):")
    lines.append("\n".join(duplicates) if duplicates else "нест")
    lines.append("")
    lines.append(f"🚫 Хато ({len(invalid)}):")
    lines.append("\n".join(invalid) if invalid else "нест")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("📊 Summary")
    lines.append("")
    lines.append(f"Склад: {len(wh_set)}")
    lines.append(f"ПВЗ: {len(pvz_set)}")
    lines.append(f"Мувофиқ: {len(matched)}")
    lines.append(f"Разница: {len(warehouse_only) + len(pvz_only)}")
    lines.append("━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


def compare_latest_common_box(chat_id):
    names = get_all_box_names(chat_id)
    if not names:
        return "❌ Ҳоло коробка сабт нашудааст."

    for box_name in names:
        wh = get_latest_box_by_name(chat_id, "warehouse", box_name)
        pvz = get_latest_box_by_name(chat_id, "pvz", box_name)
        if wh and pvz:
            return compare_box(chat_id, box_name)

    return "❌ Коробкаи якхела байни склад ва ПВЗ ёфт нашуд."


def status_text(chat_id):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM boxes WHERE chat_id = ? AND source = 'warehouse'", (chat_id,))
    warehouse_boxes = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM boxes WHERE chat_id = ? AND source = 'pvz'", (chat_id,))
    pvz_boxes = cur.fetchone()[0]

    cur.execute("""
    SELECT COUNT(*)
    FROM tracks t
    JOIN boxes b ON b.id = t.box_id
    WHERE b.chat_id = ? AND t.is_invalid = 0
    """, (chat_id,))
    total_tracks = cur.fetchone()[0]

    cur.execute("""
    SELECT box_name
    FROM boxes
    WHERE chat_id = ?
    ORDER BY id DESC
    LIMIT 1
    """, (chat_id,))
    last_box_row = cur.fetchone()

    cur.execute("""
    SELECT compared_at
    FROM comparisons
    WHERE chat_id = ?
    ORDER BY id DESC
    LIMIT 1
    """, (chat_id,))
    last_comp_row = cur.fetchone()

    conn.close()

    last_box = last_box_row[0] if last_box_row else "нест"
    last_comp = last_comp_row[0] if last_comp_row else "нест"

    return (
        "📊 Статус\n\n"
        f"📦 Коробкаҳо дар склад: {warehouse_boxes}\n"
        f"🏪 Коробкаҳо дар ПВЗ: {pvz_boxes}\n"
        f"🔢 Ҳама трекҳо: {total_tracks}\n\n"
        f"🗂 Охирин коробка:\n{last_box}\n\n"
        f"🕒 Охирин санҷиш:\n{last_comp}"
    )


def boxes_text(chat_id):
    names = get_all_box_names(chat_id)
    if not names:
        return "🗂 Ҳоло коробка нест."
    return "🗂 Рӯйхати коробкаҳо\n\n" + "\n".join(f"• {name}" for name in names)


def search_track(chat_id, track_code):
    track_code = track_code.strip().upper()

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
    SELECT b.box_name, b.source, b.pvz_name, b.date_value
    FROM tracks t
    JOIN boxes b ON b.id = t.box_id
    WHERE b.chat_id = ? AND UPPER(t.track_code) = ?
    ORDER BY b.id DESC
    LIMIT 1
    """, (chat_id, track_code))

    row = cur.fetchone()
    conn.close()

    if not row:
        return "❌ Ин трек ёфт нашуд."

    box_name, source, pvz_name, date_value = row
    source_text = "Склад" if source == "warehouse" else "ПВЗ"

    return (
        f"🔎 Трек: {track_code}\n"
        f"📦 Коробка: {box_name}\n"
        f"📍 Манбаъ: {source_text}\n"
        f"🏪 ПВЗ: {pvz_name or 'не указано'}\n"
        f"📅 Охирин сабт: {date_value or 'не указана'}"
    )


def clear_all(chat_id):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("SELECT id FROM boxes WHERE chat_id = ?", (chat_id,))
    box_ids = [r[0] for r in cur.fetchall()]

    for box_id in box_ids:
        cur.execute("DELETE FROM tracks WHERE box_id = ?", (box_id,))

    cur.execute("DELETE FROM comparisons WHERE chat_id = ?", (chat_id,))
    cur.execute("DELETE FROM boxes WHERE chat_id = ?", (chat_id,))

    conn.commit()
    conn.close()


def send_welcome(chat_id, user):
    keyboard = build_keyboard()
    username = user.get("username")
    first_name = user.get("first_name", "дӯст")
    display_name = f"@{username}" if username else first_name

    send_chat_action(chat_id, "typing")
    time.sleep(0.5)
    send_message(chat_id, "Салом 😊")

    send_chat_action(chat_id, "typing")
    time.sleep(0.5)
    send_message(chat_id, f"Хуш омадед, {display_name}!")

    send_chat_action(chat_id, "typing")
    time.sleep(0.5)
    send_message(chat_id, "Тугмачаро интихоб кунед 👇", keyboard=keyboard)


def process_box_input(chat_id, source, text):
    parsed = parse_input_block(text)

    if not parsed["tracks"] and not parsed["invalid"]:
        send_message(chat_id, "❌ Маълумот хонда нашуд.\nЛутфан форматро тафтиш кунед.", keyboard=build_keyboard())
        return

    pvz_name = "ПВЗ" if source == "pvz" else None
    save_box(chat_id, source, parsed, pvz_name=pvz_name)

    source_text = "Склад" if source == "warehouse" else "ПВЗ"

    msg = (
        "✅ Қабул шуд\n\n"
        f"📍 Манбаъ: {source_text}\n"
        f"📦 Коробка: {parsed['box_name']}\n"
        f"📅 Сана: {parsed['date_value'] or 'не указана'}\n"
        f"📊 Трекҳо: {len(parsed['tracks'])}\n"
        f"🔁 Дубликат: {len(parsed['duplicates'])}\n"
        f"🚫 Хато: {len(parsed['invalid'])}"
    )
    send_message(chat_id, msg, keyboard=build_keyboard())


def process_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()
    user = message.get("from", {})

    if chat_id not in chat_states:
        chat_states[chat_id] = None
    if chat_id not in temp_context:
        temp_context[chat_id] = {}

    keyboard = build_keyboard()

    if text == "/start":
        chat_states[chat_id] = None
        send_welcome(chat_id, user)
        return

    if text == "📦 Склад":
        chat_states[chat_id] = "warehouse_input"
        send_message(
            chat_id,
            "📦 Режими склад фаъол шуд\n\nМатнро фиристед:\nКоробка + трекҳо",
            keyboard=keyboard
        )
        return

    if text == "🏪 ПВЗ":
        chat_states[chat_id] = "pvz_input"
        send_message(
            chat_id,
            "🏪 Режими ПВЗ фаъол шуд\n\nМатнро фиристед:\nКаробка + сана + трекҳо",
            keyboard=keyboard
        )
        return

    if text == "🔍 Проверка":
        result = compare_latest_common_box(chat_id)
        send_long_message(chat_id, result, keyboard=keyboard)
        return

    if text == "📊 Статус":
        send_message(chat_id, status_text(chat_id), keyboard=keyboard)
        return

    if text == "🗂 Коробкаҳо":
        send_long_message(chat_id, boxes_text(chat_id), keyboard=keyboard)
        return

    if text == "🔎 Ҷустуҷӯи трек":
        chat_states[chat_id] = "search_track"
        send_message(chat_id, "🔎 Трекро фиристед.", keyboard=keyboard)
        return

    if text == "🧹 Очистить":
        clear_all(chat_id)
        chat_states[chat_id] = None
        send_message(chat_id, "🧹 Ҳама маълумот тоза шуд.", keyboard=keyboard)
        return

    if chat_states[chat_id] == "warehouse_input":
        chat_states[chat_id] = None
        process_box_input(chat_id, "warehouse", text)
        return

    if chat_states[chat_id] == "pvz_input":
        chat_states[chat_id] = None
        process_box_input(chat_id, "pvz", text)
        return

    if chat_states[chat_id] == "search_track":
        chat_states[chat_id] = None
        result = search_track(chat_id, text)
        send_message(chat_id, result, keyboard=keyboard)
        return

    send_message(chat_id, "Лутфан тугмачаро интихоб кунед.", keyboard=keyboard)


def main():
    global last_update_id

    init_db()
    skip_old_updates()
    print("Telegram bot started...")

    while True:
        try:
            updates = get_updates()

            if not updates or "result" not in updates:
                time.sleep(1)
                continue

            for update in updates["result"]:
    try:
        update_id = update["update_id"]

        # 👇 skip duplicate updates
        if last_update_id is not None and update_id <= last_update_id:
            continue

        last_update_id = update_id

        if "message" in update:
            process_message(update["message"])
                except Exception as e:
                    print("Message error:", e)

        except Exception as e:
            print("Main error:", e)
            time.sleep(3)


if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    main()
