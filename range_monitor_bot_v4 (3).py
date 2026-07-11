# ══════════════════════════════════════════════════════════════════════
#  🛰  NUMEX Range Monitor Bot
#  ----------------------------------------------------------------
#  Polls VoltX /liveaccess every 7s and posts new active ranges
#  to the configured Telegram channel.
#  Install : pip install pyTelegramBotAPI requests phonenumbers
#  Run     : python range_monitor_bot.py
# ══════════════════════════════════════════════════════════════════════

import json
import os
import time
import logging
import requests
import telebot
import phonenumbers
from phonenumbers import geocoder
from telebot import types

# ── CONFIG ────────────────────────────────────────────────────
RANGE_BOT_TOKEN    = "8693275949:AAFeljMV73Z4ZoSFrX3i7UjLMruLkT-ttbI"
VOLTX_API_KEY      = "MCYMM2QR285"
VOLTX_BASE_URL     = "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
RANGE_CHANNEL_ID   = -1004295896372   
NUMEX_BOT_USERNAME = "numifyotp_bot"

POLL_INTERVAL = 7         # seconds
SEEN_FILE     = "range_monitor_seen.json"
SEEN_KEEP     = 2000      # last N keys kept

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("range-monitor")

bot = telebot.TeleBot(RANGE_BOT_TOKEN, parse_mode="HTML")
VOLTX_HEADERS = {"mauthapi": VOLTX_API_KEY, "Content-Type": "application/json"}

COUNTRY_PREFIX = {
    "1":   ("United States", "🇺🇸"),
    "44":  ("United Kingdom", "🇬🇧"),
    "49":  ("Germany", "🇩🇪"),
    "33":  ("France", "🇫🇷"),
    "34":  ("Spain", "🇪🇸"),
    "39":  ("Italy", "🇮🇹"),
    "31":  ("Netherlands", "🇳🇱"),
    "46":  ("Sweden", "🇸🇪"),
    "358": ("Finland", "🇫🇮"),
    "48":  ("Poland", "🇵🇱"),
    "380": ("Ukraine", "🇺🇦"),
    "7":   ("Russia", "🇷🇺"),
    "90":  ("Turkey", "🇹🇷"),
    "91":  ("India", "🇮🇳"),
    "62":  ("Indonesia", "🇮🇩"),
    "60":  ("Malaysia", "🇲🇾"),
    "65":  ("Singapore", "🇸🇬"),
    "63":  ("Philippines", "🇵🇭"),
    "66":  ("Thailand", "🇹🇭"),
    "84":  ("Vietnam", "🇻🇳"),
    "86":  ("China", "🇨🇳"),
    "81":  ("Japan", "🇯🇵"),
    "61":  ("Australia", "🇦🇺"),
    "64":  ("New Zealand", "🇳🇿"),
    "92":  ("Pakistan", "🇵🇰"),
    "94":  ("Sri Lanka", "🇱🇰"),
    "977": ("Nepal", "🇳🇵"),
    "880": ("Bangladesh", "🇧🇩"),
    "261": ("Madagascar", "🇲🇬"),
    "55":  ("Brazil", "🇧🇷"),
    "52":  ("Mexico", "🇲🇽"),
    "1":   ("USA / Canada", "🇺🇸"),
}

def lookup_country(range_id: str) -> tuple[str, str]:
    digits = "".join(c for c in str(range_id) if c.isdigit())
    try:
        parsed = phonenumbers.parse(f"+{digits}", None)
        region = phonenumbers.region_code_for_country_code(parsed.country_code) or ""
        name = geocoder.description_for_number(parsed, "en")
        if not name and region:
            name = geocoder.country_name_for_number(parsed, "en")
        if name and len(region) == 2:
            flag = "".join(chr(127397 + ord(char)) for char in region)
            return name, flag
    except Exception:
        pass
    for plen in (4, 3, 2, 1):
        pref = digits[:plen]
        if pref in COUNTRY_PREFIX:
            return COUNTRY_PREFIX[pref]
    return ("Unknown", "🌐")


def load_seen() -> list[str]:
    if not os.path.exists(SEEN_FILE):
        return []
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_seen(seen: list[str]):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(seen[-SEEN_KEEP:], f)
    except Exception as e:
        log.warning(f"save seen failed: {e}")


def fetch_live() -> list[dict]:
    try:
        r = requests.get(f"{VOLTX_BASE_URL}/liveaccess",
                         headers=VOLTX_HEADERS, timeout=15)
        j = r.json()
        if j.get("meta", {}).get("code") == 200:
            return (j.get("data") or {}).get("services") or []
    except Exception as e:
        log.warning(f"liveaccess err: {e}")
    return []


def _html_escape(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def post_range(service: str, range_id: str):
    digits = "".join(c for c in str(range_id) if c.isdigit())
    country, flag = lookup_country(digits)
    range_str = f"{digits}XXX"
    sep = "━━━━━━━━━━━━━━"
    # Clean plain-text card with separators (no blockquote)
    text = (
        f"<b>⚡️ New Active Range ⚡️</b>\n"
        f"{sep}\n"
        f"🌐 Country : {flag} <b>{_html_escape(country)}</b>\n"
        f"📶 Range   : <code>{_html_escape(range_str)}</code> 🔥\n"
        f"🛰 Service : <b>{_html_escape(service)}</b>\n"
        f"{sep}"
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.row(
        types.InlineKeyboardButton(
            "📲 Get Number Same Range",
            url=f"https://t.me/{NUMEX_BOT_USERNAME}?start=range_{digits}",
        ),
    )
    kb.row(
        types.InlineKeyboardButton(
            "📱 Number Panel",
            url=f"https://t.me/{NUMEX_BOT_USERNAME}",
        ),
    )
    try:
        bot.send_message(RANGE_CHANNEL_ID, text, reply_markup=kb)
        log.info(f"posted: {service} | {digits}")
    except Exception as e:
        log.warning(f"post failed: {e}")


# ── Allowed services filter ────────────────────────────────────
ALLOWED_SERVICES = {
    "facebook", "instagram", "whatsapp", "telegram",
    "chatgpt", "discord", "twilio", "1xbet"
}

def is_allowed_service(sid: str) -> bool:
    return sid.strip().lower() in ALLOWED_SERVICES


def main():
    log.info("Range Monitor started")
    seen = load_seen()
    seen_set = set(seen)
    while True:
        try:
            services = fetch_live()
            new_added = False
            for svc in services:
                sid = str(svc.get("sid") or "")
                if not is_allowed_service(sid):
                    continue  # ⛔ skip unwanted services
                ranges = svc.get("ranges") or []
                for rng in ranges:
                    key = f"{sid}|{rng}"
                    if key in seen_set:
                        continue
                    post_range(sid, rng)
                    seen_set.add(key)
                    seen.append(key)
                    new_added = True
                    time.sleep(1)  # avoid flood
            if new_added:
                save_seen(seen)
        except Exception as e:
            log.warning(f"loop err: {e}")
        time.sleep(POLL_INTERVAL)


def _run_polling():
    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
    except Exception as e:
        log.warning(f"polling err: {e}")


if __name__ == "__main__":
    import threading
    threading.Thread(target=_run_polling, daemon=True).start()
    main()
