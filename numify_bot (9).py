# ══════════════════════════════════════════════════════════════════════
#  🌟  NUMIFY  ✦  PREMIUM  NUMBER  PANEL  •  v7  (FULL: MEMBER + ADMIN + LIVE)  🌟
#  ----------------------------------------------------------------
#  ✦ English only • Member-side complete
#  ✦ Buttons: NUMBER • PROFILE • REFER • SUPPORT • VIEW RANGE
#             2FA  • WITHDRAWAL • ADMIN PANEL (admin only)
#  ✦ Anti-Flood (5 taps / 5 sec → 5 min block)
#  ✦ Force-join verification for OTP and range channels
#  ✦ Premium Number Card (NEXORA ELITE frame)
#  ✦ 2FA TOTP generator with 30s countdown
#  ✦ Withdrawal (Binance / bKash)
#  ✦ Balance system + Referral rewards
#  ✦ OTP auto-forward (last 3-4 digits match) + balance credit
#  ✦ New-user notification to admin DM
#  ✦ Unknown-command guard
#  ✦ Telegram menu button: 🚀 Start → /start
#
#  Install : pip install pyTelegramBotAPI pyotp phonenumbers
#  Run     : python nexora_full_bot_v6.py
# ══════════════════════════════════════════════════════════════════════

# ╔══════════════════════════════════════════════════════════╗
# ║          🔧  EDIT THIS SECTION ONLY                       ║
# ╚══════════════════════════════════════════════════════════╝
NUMEX_BOT_TOKEN    = "8617608815:AAECGCMlwjTzkHCbrqz9CKXIOWHH-xJNRTE"
NUMEX_BOT_USERNAME = "numifyotp_bot"
API_TOKEN          = NUMEX_BOT_TOKEN       # backward compat alias
ADMIN_ID           = 7387463636
ADMIN_USERNAME     = "NEXORA_X_SHAMIM"       # without @
BOT_USERNAME       = NUMEX_BOT_USERNAME    # without @
BOT_NAME           = "NUMIFY"
TRAFFIC_CHANNEL_LINK = "https://t.me/numifyotp"
OTP_CHAT_ID        = -1003986148517        # OTP forward group/channel id
OTP_GROUP_LINK     = "https://t.me/+8JQqOqqo3MRmMDY1"
RANGE_GROUP_LINK   = "https://t.me/+cW1eTB60MVwzNTFl"
CURRENCY           = "৳"
MIN_WITHDRAW       = 1
MINIMUM_WITHDRAWAL = MIN_WITHDRAW
DEFAULT_OTP_RATE   = 0.20                  # ৳ per OTP

# ── VoltX (live number provider) ───────────────────────────────
VOLTX_API_KEY     = "MCYMM2QR285"
VOLTX_BASE_URL    = "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
RANGE_CHANNEL_ID  = -1004295896372          # range monitor channel (private)
RANGE_CHANNEL_PUBLIC_LINK = "https://t.me/+cW1eTB60MVwzNTFl"
# ══════════════════════════════════════════════════════════════════════

import re
import time
import json
import sqlite3
import logging
import threading

import requests
from contextlib import contextmanager
from collections import defaultdict, deque
from datetime import datetime, timezone

import telebot
import phonenumbers
from phonenumbers import geocoder
from telebot import types
from telebot.apihelper import ApiTelegramException

try:
    import pyotp
except ImportError:
    pyotp = None

logging.basicConfig(
    filename="nexora.log", level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("nexora")

import os

# ── Network resilience: retry on transient errors instead of crashing ──
telebot.apihelper.CONNECT_TIMEOUT = 15      # seconds to establish connection
telebot.apihelper.READ_TIMEOUT    = 30      # seconds to wait for Telegram's response
telebot.apihelper.RETRY_ON_ERROR  = True    # auto-retry a failed API call
telebot.apihelper.MAX_RETRIES     = 3       # retry attempts before giving up


class BotExceptionHandler(telebot.ExceptionHandler):
    """Catches any exception raised inside a message/callback handler
    (e.g. a Telegram ReadTimeout while sending a message) so a single
    failed request logs an error instead of killing the whole polling
    loop and taking the bot offline."""
    def handle(self, exception):
        log.error(f"Handler exception (auto-recovered): {exception}", exc_info=True)
        return True  # True = treat as handled, do not re-raise / crash polling


bot = telebot.TeleBot(API_TOKEN, parse_mode="Markdown", exception_handler=BotExceptionHandler())
# Use the Railway volume mount path so the DB survives redeploys.
# Set DB_PATH env var in Railway to your volume's mount path + filename,
# e.g. /app/data/nexora.db  (mount path must match your Volume settings)
DB_PATH = os.environ.get("DB_PATH", "/app/data/nexora.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
db_lock = threading.Lock()
user_temp: dict[int, dict] = {}

# ── In-memory caches (fast, fewer DB hits) ─────────────────────
LANG_CACHE: dict[int, str] = {}
ADMIN_CACHE: set[int] = set()
FLOOD: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
FLOOD_BAN: dict[int, float] = {}
FLOOD_MAX  = 5      # max actions
FLOOD_WIN  = 5      # within seconds
FLOOD_LOCK = 300    # 5 min lock


def utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ╔══════════════════════════════════════════════════════════╗
# ║                       DATABASE                            ║
# ╚══════════════════════════════════════════════════════════╝
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    try:
        with db_lock:
            yield conn
            conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        c = conn.cursor()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT,
            username TEXT,
            taken INTEGER DEFAULT 0,
            vip INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            ref_count INTEGER DEFAULT 0,
            ref_by INTEGER DEFAULT 0,
            lang TEXT DEFAULT 'en',
            balance REAL DEFAULT 0,
            otp_count INTEGER DEFAULT 0,
            special_rate INTEGER DEFAULT 0,
            flood_ban_until INTEGER DEFAULT 0,
            joined_at TEXT,
            last_taken TEXT
        );
        CREATE TABLE IF NOT EXISTS categories (
            name TEXT PRIMARY KEY,
            emoji TEXT DEFAULT '📞'
        );
        CREATE TABLE IF NOT EXISTS countries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cat TEXT, name TEXT, emoji TEXT DEFAULT '🌐',
            UNIQUE(cat, name)
        );
        CREATE TABLE IF NOT EXISTS numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            num TEXT UNIQUE, cat TEXT, country TEXT,
            used INTEGER DEFAULT 0,
            taken_by INTEGER DEFAULT 0,
            taken_at TEXT
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, num TEXT, cat TEXT, country TEXT, taken_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS sub_admins (
            uid INTEGER PRIMARY KEY, added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS otp_rates (
            country TEXT PRIMARY KEY, rate REAL
        );
        CREATE TABLE IF NOT EXISTS live_numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER,
            number TEXT,
            range_id TEXT,
            country TEXT,
            operator TEXT,
            taken_at TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_live_num ON live_numbers(number);
        CREATE INDEX IF NOT EXISTS idx_live_uid ON live_numbers(uid);
        CREATE TABLE IF NOT EXISTS live_otp_seen (
            otp_id TEXT PRIMARY KEY,
            seen_at TEXT
        );
        CREATE TABLE IF NOT EXISTS api_categories (
            name TEXT PRIMARY KEY,
            emoji TEXT DEFAULT '📲'
        );
        CREATE TABLE IF NOT EXISTS api_countries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cat TEXT, name TEXT, emoji TEXT DEFAULT '🌐',
            UNIQUE(cat, name)
        );
        CREATE TABLE IF NOT EXISTS api_ranges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cat TEXT, country TEXT, range_id TEXT,
            added_at TEXT,
            UNIQUE(cat, country, range_id)
        );
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, username TEXT, name TEXT,
            amount REAL, method TEXT, account TEXT,
            status TEXT DEFAULT 'pending',
            requested_at TEXT, processed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_hist_uid  ON history(uid);
        CREATE INDEX IF NOT EXISTS idx_num_cat   ON numbers(cat, country);
        CREATE INDEX IF NOT EXISTS idx_wd_status ON withdrawals(status);
        """)

        # Ensure "credited" column exists so a number gets paid only once
        for _tbl in ("numbers", "live_numbers"):
            try:
                conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN credited INTEGER DEFAULT 0")
            except Exception:
                pass

        for k, v in [
            ("cooldown", "30"), ("maintenance", "off"),
            ("daily_limit", "20"), ("ref_target", "10"),
            ("per_request", "2"), ("min_withdraw", str(MIN_WITHDRAW)),
            ("live_otp_rate", str(DEFAULT_OTP_RATE)),
        ]:
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        conn.execute("UPDATE settings SET value=? WHERE key='min_withdraw' AND value='100'", (str(MIN_WITHDRAW),))
        conn.execute("UPDATE settings SET value=? WHERE key='live_otp_rate' AND value='1.0'", (str(DEFAULT_OTP_RATE),))

        # Seed default permanent categories (admin may delete if desired)
        for _cn, _ce in [("Facebook","📲"),("Instagram","📲"),("WhatsApp","📲"),("Telegram","📲")]:
            conn.execute("INSERT OR IGNORE INTO categories(name,emoji) VALUES(?,?)", (_cn, _ce))
            conn.execute("INSERT OR IGNORE INTO api_categories(name,emoji) VALUES(?,?)", (_cn, _ce))



def setting(key: str, default: str = "") -> str:
    with db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


# ╔══════════════════════════════════════════════════════════╗
# ║                       i18n (English only)                 ║
# ╚══════════════════════════════════════════════════════════╝
T = {
    "btn_number":   "📱 𝗡𝗨𝗠𝗕𝗘𝗥",
    "btn_profile":  "👤 𝗣𝗥𝗢𝗙𝗜𝗟𝗘",
    "btn_refer":    "🎁 𝗥𝗘𝗙𝗘𝗥",
    "btn_support":  "💬 𝗦𝗨𝗣𝗣𝗢𝗥𝗧",
    "btn_2fa":      "🔑 𝟮𝗙𝗔",
    "btn_withdraw": "💰 𝗪𝗜𝗧𝗛𝗗𝗥𝗔𝗪𝗔𝗟",
    "btn_admin":    "⚙️ 𝗔𝗗𝗠𝗜𝗡 𝗣𝗔𝗡𝗘𝗟",
    "btn_back":     "🔙 𝗕𝗔𝗖𝗞",
}
BTN_KEYS = {v: k for k, v in T.items()}

def btn_is(text: str, key: str) -> bool:
    return BTN_KEYS.get(text or "") == key


def md_escape(value) -> str:
    """Escape dynamic text for Telegram legacy Markdown."""
    text = str(value if value is not None else "—")
    return re.sub(r"([_*`\[])", r"\\\1", text)


def md_code(value) -> str:
    """Safe inline-code wrapper for IDs/accounts in Telegram Markdown."""
    return "`" + str(value if value is not None else "—").replace("`", "'") + "`"


def user_label(username=None, uid=None, name=None) -> str:
    """Show @username when available; otherwise fall back to name + ID / ID."""
    username = (username or "").strip()
    if username:
        return "@" + md_escape(username.lstrip("@"))
    if name:
        return f"{md_escape(name)} ({md_code(uid)})" if uid is not None else md_escape(name)
    return md_code(uid) if uid is not None else "—"


# ╔══════════════════════════════════════════════════════════╗
# ║                  Helper / Guards                          ║
# ╚══════════════════════════════════════════════════════════╝
def is_admin(uid: int) -> bool:
    if uid == ADMIN_ID or uid in ADMIN_CACHE:
        return True
    with db() as conn:
        r = conn.execute("SELECT 1 FROM sub_admins WHERE uid=?", (uid,)).fetchone()
    if r:
        ADMIN_CACHE.add(uid)
        return True
    return False


def is_banned(uid: int) -> bool:
    with db() as conn:
        r = conn.execute("SELECT banned FROM users WHERE id=?", (uid,)).fetchone()
    return bool(r and r["banned"])


def lang_of(uid: int) -> str:
    if uid in LANG_CACHE:
        return LANG_CACHE[uid]
    with db() as conn:
        r = conn.execute("SELECT lang FROM users WHERE id=?", (uid,)).fetchone()
    lng = (r["lang"] if r and r["lang"] else "en")
    LANG_CACHE[uid] = lng
    return lng


def anti_flood(uid: int) -> bool:
    """Return True if user is currently flood-blocked (and silently drop)."""
    now = time.time()
    until = FLOOD_BAN.get(uid, 0)
    if until > now:
        return True
    q = FLOOD[uid]
    q.append(now)
    recent = [t for t in q if now - t <= FLOOD_WIN]
    if len(recent) > FLOOD_MAX:
        FLOOD_BAN[uid] = now + FLOOD_LOCK
        try:
            bot.send_message(uid, "🛑 *Too many taps!*\nYou are temporarily blocked for 5 minutes.\nPlease slow down.")
        except Exception:
            pass
        return True
    return False


def register_user(m) -> bool:
    """Create user if new. Return True if newly created."""
    uid = m.from_user.id
    name = (m.from_user.first_name or "User")[:64]
    uname = (m.from_user.username or "")[:64]
    with db() as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
        if existing:
            conn.execute("UPDATE users SET name=?, username=? WHERE id=?", (name, uname, uid))
            return False
        conn.execute(
            "INSERT INTO users(id,name,username,lang,joined_at) VALUES(?,?,?,?,?)",
            (uid, name, uname, "en", utcnow_str()),
        )
    return True


def notify_admin_new_user(m):
    try:
        u = m.from_user
        txt = (
            "👤 *New User!*\n"
            f"📛 Name: {md_code(u.first_name or '-')}\n"
            f"🆔 ID: {md_code(u.id)}\n"
            f"📛 Username: {user_label(u.username, u.id)}\n"
            f"📅 Joined: {md_code(utcnow_str())}"
        )
        bot.send_message(ADMIN_ID, txt)
    except Exception as e:
        log.warning(f"notify admin failed: {e}")


def phone_country(number: str, fallback: str = "") -> tuple[str, str]:
    """Return the English country name and flag detected from an international number."""
    try:
        parsed = phonenumbers.parse(str(number), None)
        region = phonenumbers.region_code_for_number(parsed) or ""
        name = geocoder.description_for_number(parsed, "en") or fallback or "Unknown"
        flag = "".join(chr(127397 + ord(char)) for char in region) if len(region) == 2 else "🌐"
        return name, flag
    except Exception:
        return fallback or "Unknown", country_flag(fallback)


def force_join_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📢 Join OTP Group", url=OTP_GROUP_LINK))
    kb.add(types.InlineKeyboardButton("NUMIFY CHANEL", url=TRAFFIC_CHANNEL_LINK))
    kb.add(types.InlineKeyboardButton("✅ Verify", callback_data="verify_join"))
    return kb


def has_joined_required_channels(uid: int) -> bool:
    try:
        otp_member = bot.get_chat_member(OTP_CHAT_ID, uid)
        allowed = {"creator", "administrator", "member", "restricted"}
        return otp_member.status in allowed
    except Exception as e:
        log.warning(f"force join check failed for {uid}: {e}")
        return False


# ╔══════════════════════════════════════════════════════════╗
# ║                  Keyboards                                ║
# ╚══════════════════════════════════════════════════════════╝
def main_menu(uid: int) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=2)
    kb.add(T["btn_number"], T["btn_profile"])
    kb.add(T["btn_refer"], T["btn_support"])
    kb.add(T["btn_2fa"], T["btn_withdraw"])
    if is_admin(uid):
        kb.add(T["btn_admin"])
    return kb



_BOLD_SANS_MAP = {}
def _build_bold_map():
    for i,ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        _BOLD_SANS_MAP[ch] = chr(0x1D5D4 + i)
    for i,ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
        _BOLD_SANS_MAP[ch] = chr(0x1D5EE + i)
    for i,ch in enumerate("0123456789"):
        _BOLD_SANS_MAP[ch] = chr(0x1D7EC + i)
_build_bold_map()
def bold_sans(text: str) -> str:
    return "".join(_BOLD_SANS_MAP.get(c, c) for c in (text or ""))

def safe_user_emoji(emoji: str) -> str:
    """Hide admin-only API hint emojis from members."""
    if not emoji or emoji in ("📡", "📶", "🛰", "🛰️"):
        return "📲"
    return emoji

def category_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    with db() as conn:
        rows = conn.execute("SELECT name, emoji FROM categories ORDER BY name").fetchall()
        api_rows = conn.execute("SELECT name, emoji FROM api_categories ORDER BY name").fetchall()
    # Build dedup map preferring API (so range-backed cats win) but never reveal API hints
    seen = {}
    for r in rows:
        seen[r['name']] = (safe_user_emoji(r['emoji']), f"cat_{r['name']}")
    for r in api_rows:
        seen[r['name']] = (safe_user_emoji(r['emoji']), f"acat_{r['name']}")
    for name in sorted(seen.keys()):
        emo, cb = seen[name]
        kb.add(types.InlineKeyboardButton(f"{emo} {bold_sans(name)}", callback_data=cb))
    kb.add(types.InlineKeyboardButton("🏠 Home", callback_data="home"))
    return kb


def country_kb(cat: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    with db() as conn:
        rows = conn.execute("""
            SELECT c.name, c.emoji,
                (SELECT COUNT(*) FROM numbers n WHERE n.cat=c.cat AND n.country=c.name AND n.used=0) AS stock
            FROM countries c WHERE c.cat=? ORDER BY c.name
        """, (cat,)).fetchall()
    if not rows:
        kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="pick"))
        return kb
    btns = [types.InlineKeyboardButton(
        f"{r['emoji']} {bold_sans(r['name'])}  •  {r['stock']}",
        callback_data=f"cn_{cat}|{r['name']}"
    ) for r in rows]
    kb.add(*btns)
    kb.add(types.InlineKeyboardButton("🔙 Categories", callback_data="pick"))
    return kb


def number_kb(cat: str, country: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔄 Change Number", callback_data=f"chg_{cat}|{country}"),
        types.InlineKeyboardButton("🔔 View OTP", url=OTP_GROUP_LINK),
    )
    kb.add(types.InlineKeyboardButton("🔙 Other Category", callback_data="pick"))
    return kb


# ╔══════════════════════════════════════════════════════════╗
# ║                  Number Card                              ║
# ╚══════════════════════════════════════════════════════════╝
NUM_BULLET = ["➊","➋","➌","➍","➎","➏","➐","➑","➒","➓"]

def number_card(cat: str, country: str, country_emoji: str, nums: list[str]) -> str:
    if nums:
        country, country_emoji = phone_country(nums[0], country)
    title = f"{country_emoji} {cat}⚡ ┊ {country} ✅"
    lines = [f"╔══ ✦ 𝗡𝗨𝗠𝗜𝗙𝗬 ✦ ══╗",
             f"",
             f"{title}",
             f""]
    for i, n in enumerate(nums):
        bullet = NUM_BULLET[i] if i < len(NUM_BULLET) else f"({i+1})"
        lines.append(f"{bullet} 📱 `{n}`")
    lines.append("")
    lines.append("🔔 Waiting for Otp....")
    lines.append("╚══════════════════════╝")
    return "\n".join(lines)


# ╔══════════════════════════════════════════════════════════╗
# ║                  /start  •  Welcome                       ║
# ╚══════════════════════════════════════════════════════════╝
@bot.message_handler(commands=["start"])
def cmd_start(m):
    if anti_flood(m.from_user.id):
        return
    new_user = register_user(m)

    # Referral parsing /start ref_12345
    # Range deep link /start range_26134
    try:
        parts = (m.text or "").split(maxsplit=1)
        if len(parts) == 2:
            param = parts[1]
            if param.startswith("ref_"):
                ref_id = int(param.split("_", 1)[1])
                if ref_id != m.from_user.id and new_user:
                    try:
                        target = int(setting("ref_target", "10") or "10")
                    except Exception:
                        target = 10
                    with db() as conn:
                        cur = conn.execute("UPDATE users SET ref_by=? WHERE id=? AND ref_by=0",
                                           (ref_id, m.from_user.id))
                        if cur.rowcount:
                            conn.execute("UPDATE users SET ref_count=ref_count+1 WHERE id=?", (ref_id,))
                            ref_user = conn.execute("SELECT ref_count, vip FROM users WHERE id=?", (ref_id,)).fetchone()
                            if ref_user:
                                try:
                                    bot.send_message(
                                        ref_id,
                                        f"🎉 New referral joined!\n👥 Referrals: *{ref_user['ref_count']}/{target}*"
                                    )
                                except Exception: pass
                                if ref_user["ref_count"] >= target and not ref_user["vip"]:
                                    try:
                                        bot.send_message(
                                            ref_id,
                                            f"🏆 You've reached *{target} referrals*!\n"
                                            f"📩 Contact admin @{ADMIN_USERNAME} to claim your *VIP* ⭐"
                                        )
                                    except Exception: pass
            elif param.startswith("range_"):
                range_id = param.split("_", 1)[1]
                user_temp[m.from_user.id] = {"pending_range": range_id}
    except Exception:
        pass

    if new_user:
        notify_admin_new_user(m)

    if is_banned(m.from_user.id):
        bot.send_message(m.chat.id, "🚫 You are banned!")
        return

    if new_user:
        text = (
            "╔══ ✦ 𝗡𝗨𝗠𝗜𝗙𝗬 ✦ ══╗\n\n"
            "👋 Hey there! Welcome to <b>NUMIFY</b> 🎉\n"
            "Your friendly hub for fresh numbers & instant OTPs ⚡\n\n"
            "🤝 Join our community below to unlock the bot,\n"
            "then tap <b>✅ Verify</b> to jump in!"
        )
        bot.send_message(m.chat.id, text, reply_markup=force_join_kb(), parse_mode="HTML")
        return

    # Auto-fetch only if came from range deep link
    pending = user_temp.get(m.from_user.id, {}).pop("pending_range", None)
    if pending:
        _do_live_fetch(m.chat.id, m.from_user.id, pending)
        return

    # Existing user pressing /start -> just open the menu (no welcome flow).
    bot.send_message(m.chat.id, "🏠 Main Menu", reply_markup=main_menu(m.from_user.id))
    return


@bot.callback_query_handler(func=lambda cq: cq.data == "verify_join")
def cb_verify_join(cq):
    try:
        bot.delete_message(cq.message.chat.id, cq.message.message_id)
    except Exception:
        pass
    bot.send_message(cq.message.chat.id, "🏠 Main Menu", reply_markup=main_menu(cq.from_user.id))
    bot.answer_callback_query(cq.id, "✅ Verified")


@bot.message_handler(commands=["cancel"])
def cmd_cancel(m):
    user_temp.pop(m.from_user.id, None)
    bot.clear_step_handler_by_chat_id(m.chat.id)
    bot.send_message(m.chat.id, "❌ Cancelled.", reply_markup=main_menu(m.from_user.id))


# ╔══════════════════════════════════════════════════════════╗
# ║                  📱 NUMBER flow                           ║
# ╚══════════════════════════════════════════════════════════╝
@bot.message_handler(func=lambda m: btn_is(m.text, "btn_number"))
def h_number(m):
    if anti_flood(m.from_user.id): return
    if is_banned(m.from_user.id):
        return bot.send_message(m.chat.id, "🚫 You are banned!")
    if setting("maintenance", "off") == "on" and not is_admin(m.from_user.id):
        return bot.send_message(m.chat.id, "🛠️ Bot is under maintenance!")
    bot.send_message(m.chat.id, "▼ *𝗣𝗶𝗰𝗸 𝗮 𝗖𝗮𝘁𝗲𝗴𝗼𝗿𝘆*", reply_markup=category_kb())


@bot.callback_query_handler(func=lambda cq: cq.data == "pick")
def cb_pick(cq):
    bot.edit_message_text("▼ *𝗣𝗶𝗰𝗸 𝗮 𝗖𝗮𝘁𝗲𝗴𝗼𝗿𝘆*", cq.message.chat.id,
                          cq.message.message_id, reply_markup=category_kb(),
                          parse_mode="Markdown")
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda cq: cq.data == "home")
def cb_home(cq):
    try:
        bot.delete_message(cq.message.chat.id, cq.message.message_id)
    except Exception:
        pass
    bot.send_message(cq.message.chat.id, "🏠 Main Menu",
                     reply_markup=main_menu(cq.from_user.id))
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("cat_"))
def cb_cat(cq):
    cat = cq.data[4:]
    bot.edit_message_text(f"🌍 *Pick a country* — _{cat}_", cq.message.chat.id,
                          cq.message.message_id, reply_markup=country_kb(cat),
                          parse_mode="Markdown")
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("cn_"))
def cb_country(cq):
    try:
        cat, country = cq.data[3:].split("|", 1)
    except Exception:
        return bot.answer_callback_query(cq.id, "⚠️ Bad data")
    _send_numbers(cq, cat, country, edit=True)


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("chg_"))
def cb_change(cq):
    try:
        cat, country = cq.data[4:].split("|", 1)
    except Exception:
        return bot.answer_callback_query(cq.id, "⚠️ Bad data")
    _send_numbers(cq, cat, country, edit=True)


def _send_numbers(cq, cat: str, country: str, edit=False):
    uid = cq.from_user.id
    chat_id = cq.message.chat.id
    mid = cq.message.message_id

    try:
        per_req = int(setting("per_request", "2") or "2")
    except Exception:
        per_req = 2

    with db() as conn:
        emoji = "🌐"
        r = conn.execute("SELECT emoji FROM countries WHERE cat=? AND name=?", (cat, country)).fetchone()
        if r: emoji = r["emoji"]

        rows = conn.execute(
            "SELECT id, num FROM numbers WHERE cat=? AND country=? AND used=0 LIMIT ?",
            (cat, country, per_req),
        ).fetchall()
        if not rows:
            try:
                bot.edit_message_text(
                    f"❌ No numbers available for *{country}*.",
                    chat_id, mid, parse_mode="Markdown",
                    reply_markup=country_kb(cat),
                )
            except Exception:
                bot.send_message(chat_id, f"❌ No numbers available for *{country}*.")
            return bot.answer_callback_query(cq.id)

        ids = [r["id"] for r in rows]
        nums = [r["num"] for r in rows]
        conn.execute(
            f"UPDATE numbers SET used=1, taken_by=?, taken_at=? WHERE id IN ({','.join('?'*len(ids))})",
            (uid, utcnow_str(), *ids),
        )
        for n in nums:
            conn.execute(
                "INSERT INTO history(uid,num,cat,country,taken_at) VALUES(?,?,?,?,?)",
                (uid, n, cat, country, utcnow_str()),
            )
        conn.execute("UPDATE users SET taken=taken+?, last_taken=? WHERE id=?",
                     (len(nums), utcnow_str(), uid))

    txt = number_card(cat, country, emoji, nums)
    # Always delete the old card and send a fresh card at the BOTTOM,
    # so the new number sits below previous OTPs and is easy to copy.
    try:
        bot.delete_message(chat_id, mid)
    except Exception:
        pass
    try:
        bot.send_message(chat_id, txt,
                         reply_markup=number_kb(cat, country),
                         parse_mode="Markdown")
    except Exception:
        bot.send_message(chat_id, txt, reply_markup=number_kb(cat, country))
    bot.answer_callback_query(cq.id, "✅ Numbers assigned")


# ╔══════════════════════════════════════════════════════════╗
# ║                  👤 PROFILE                               ║
# ╚══════════════════════════════════════════════════════════╝
@bot.message_handler(func=lambda m: btn_is(m.text, "btn_profile"))
def h_profile(m):
    if anti_flood(m.from_user.id): return
    uid = m.from_user.id
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return bot.send_message(m.chat.id, "Please /start first.")
    try:
        ref_target = int(setting("ref_target", "10") or "10")
    except Exception:
        ref_target = 10
    vip_badge = " 👑 VIP" if u["vip"] else ""
    special_badge = " ✨ Special" if u["special_rate"] else ""
    # Referral status line
    if u["ref_count"] >= ref_target:
        ref_line = f"🏆 {ref_target} referrals reached! Contact admin @{ADMIN_USERNAME} to claim VIP ⭐"
    else:
        ref_line = f"💡 Reach {ref_target} referrals → contact admin for VIP"
    # Special rate line
    spec_line = f"✨ *Special Rate* ✅ Active — +{CURRENCY}0.20/OTP" if u["special_rate"] else ""
    txt = (
        "👤 *Profile*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📛 Name: {md_code(u['name'])}{vip_badge}{special_badge}\n"
        f"🆔 ID: {md_code(u['id'])}\n"
        f"📛 Username: {user_label(u['username'], u['id'])}\n"
        f"💰 Balance: *{CURRENCY}{u['balance']:.2f}*\n"
        f"⭐ VIP: {'✅ Yes' if u['vip'] else '❌ No'}\n"
        f"✨ Special Rate: {'✅ Yes (+৳0.20/OTP)' if u['special_rate'] else '❌ No'}\n"
        f"🎁 Refer: {u['ref_count']}/{ref_target}\n"
        f"📩 OTPs received: {u['otp_count']}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{ref_line}"
        + (f"\n{spec_line}" if spec_line else "")
    )
    bot.send_message(m.chat.id, txt)


# ╔══════════════════════════════════════════════════════════╗
# ║                  🎁 REFER                                 ║
# ╚══════════════════════════════════════════════════════════╝
@bot.message_handler(func=lambda m: btn_is(m.text, "btn_refer"))
def h_refer(m):
    if anti_flood(m.from_user.id): return
    uid = m.from_user.id
    with db() as conn:
        u = conn.execute("SELECT ref_count FROM users WHERE id=?", (uid,)).fetchone()
    cnt = u["ref_count"] if u else 0
    try:
        target = int(setting("ref_target", "10") or "10")
    except Exception:
        target = 10
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    if cnt >= target:
        extra = f"🏆 You've reached *{target} referrals*!\n📩 Contact admin @{ADMIN_USERNAME} to claim *VIP* ⭐"
    else:
        extra = f"💡 Reach *{target} referrals* to qualify for *VIP* ⭐"
    txt = (
        "╔══ 🎁 𝗥𝗘𝗙𝗘𝗥𝗥𝗔𝗟 𝗣𝗥𝗢𝗚𝗥𝗔𝗠 ══╗\n\n"
        "🔥 *Refer 10 Friends & Earn*\n"
        "*+৳0.20 BONUS on Every OTP!* 💰\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"👥 Referrals: *{cnt}/{target}*\n"
        f"🎯 {target} referrals = *VIP* ⭐ (contact admin)\n"
        f"{extra}\n\n"
        "📋 *How it works:*\n"
        "• Share your link below\n"
        "• When a friend starts the bot through your link, it counts instantly ✅\n\n"
        "🔗 *Your Unique Link:*\n"
        f"`{link}`\n\n"
        "╚══════════════════════╝"
    )
    kb = types.InlineKeyboardMarkup()
    share = f"https://t.me/share/url?url={link}&text=Join%20{BOT_NAME}%20and%20earn%20from%20OTPs!"
    kb.add(types.InlineKeyboardButton("📤 Share", url=share))
    bot.send_message(m.chat.id, txt, reply_markup=kb)


# ╔══════════════════════════════════════════════════════════╗
# ║                  💬 SUPPORT                               ║
# ╚══════════════════════════════════════════════════════════╝
@bot.message_handler(func=lambda m: btn_is(m.text, "btn_support"))
def h_support(m):
    if anti_flood(m.from_user.id): return
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("👑 Message Developer", url=f"https://t.me/{ADMIN_USERNAME}"))
    bot.send_message(m.chat.id, "💬 *Support*\nContact the developer for any issue.", reply_markup=kb)





# ╔══════════════════════════════════════════════════════════╗
# ║                  🔑 2FA  (TOTP)                           ║
# ╚══════════════════════════════════════════════════════════╝
@bot.message_handler(func=lambda m: btn_is(m.text, "btn_2fa"))
def h_2fa(m):
    if anti_flood(m.from_user.id): return
    if pyotp is None:
        return bot.send_message(m.chat.id, "⚠️ 2FA module not installed.\nRun: `pip install pyotp`")
    msg = bot.send_message(
        m.chat.id,
        "🔑 *2FA Code Generator*\n\n"
        "Send your *secret key* (Base32) and I'll show the 6-digit code with a 30s countdown.\n\n"
        "_Type /cancel to exit._",
    )
    bot.register_next_step_handler(msg, _do_2fa)


def _do_2fa(m):
    if (m.text or "").strip().lower() in ("/cancel", "cancel"):
        return bot.send_message(m.chat.id, "❌ Cancelled.")
    key = re.sub(r"\s+", "", (m.text or "")).upper()
    if not key or not re.fullmatch(r"[A-Z2-7=]+", key):
        return bot.send_message(m.chat.id, "❌ Invalid key. Send a valid Base32 secret.")
    try:
        totp = pyotp.TOTP(key)
        code = totp.now()
        remaining = 30 - int(time.time()) % 30
    except Exception:
        return bot.send_message(m.chat.id, "❌ Invalid secret key.")

    sent = bot.send_message(
        m.chat.id,
        f"🔑 *2FA Code*\n\n`{code}`\n\n⏱ Valid for *{remaining}s*",
    )
    # Live countdown (limited updates to avoid Telegram rate limits)
    def _tick():
        nonlocal_left = remaining
        last_code = code
        try:
            while True:
                time.sleep(5)
                left = 30 - int(time.time()) % 30
                cur = pyotp.TOTP(key).now()
                if cur != last_code:
                    last_code = cur
                bot.edit_message_text(
                    f"🔑 *2FA Code*\n\n`{last_code}`\n\n⏱ Valid for *{left}s*",
                    m.chat.id, sent.message_id, parse_mode="Markdown",
                )
                if left <= 5:
                    break
        except Exception:
            pass
    threading.Thread(target=_tick, daemon=True).start()


# ╔══════════════════════════════════════════════════════════╗
# ║                  💰 WITHDRAWAL                            ║
# ╚══════════════════════════════════════════════════════════╝
WD_METHODS = [("₿ Binance ID", "Binance"),
              ("💚 bKash", "bKash")]


@bot.message_handler(func=lambda m: btn_is(m.text, "btn_withdraw"))
def h_withdraw(m):
    if anti_flood(m.from_user.id): return
    uid = m.from_user.id
    with db() as conn:
        u = conn.execute("SELECT balance FROM users WHERE id=?", (uid,)).fetchone()
        pending = conn.execute(
            "SELECT 1 FROM withdrawals WHERE uid=? AND status='pending' LIMIT 1", (uid,)
        ).fetchone()
    bal = u["balance"] if u else 0.0
    try:
        min_wd = float(setting("min_withdraw", str(MIN_WITHDRAW)) or MIN_WITHDRAW)
    except Exception:
        min_wd = MIN_WITHDRAW

    if pending:
        return bot.send_message(
            m.chat.id,
            f"⏳ You already have a *pending withdrawal*.\nPlease wait until it is processed.",
        )

    if bal < min_wd:
        need = min_wd - bal
        return bot.send_message(
            m.chat.id,
            f"💰 *Balance:* {CURRENCY}{bal:.2f}\n"
            f"⚠️ Minimum withdrawal: *{CURRENCY}{min_wd:.2f}*\n"
            f"You need *{CURRENCY}{need:.2f}* more to withdraw.",
        )

    kb = types.InlineKeyboardMarkup(row_width=2)
    for label, key in WD_METHODS:
        kb.add(types.InlineKeyboardButton(label, callback_data=f"wd_m_{key}"))
    kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data="wd_cancel"))
    bot.send_message(
        m.chat.id,
        f"💰 *Withdrawal*\nBalance: *{CURRENCY}{bal:.2f}*\nMin: {CURRENCY}{min_wd:.2f}\n\n"
        "Choose payment method:",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda cq: cq.data == "wd_cancel")
def cb_wd_cancel(cq):
    user_temp.pop(cq.from_user.id, None)
    try: bot.delete_message(cq.message.chat.id, cq.message.message_id)
    except Exception: pass
    bot.answer_callback_query(cq.id, "Cancelled")


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("wd_m_"))
def cb_wd_method(cq):
    method = cq.data[5:]
    user_temp[cq.from_user.id] = {"wd_method": method}
    try: bot.delete_message(cq.message.chat.id, cq.message.message_id)
    except Exception: pass
    msg = bot.send_message(cq.message.chat.id,
                           f"💳 Method: *{method}*\nSend your *account number / ID*:")
    bot.register_next_step_handler(msg, _wd_account)
    bot.answer_callback_query(cq.id)


def _wd_account(m):
    if (m.text or "").startswith("/"):
        return bot.send_message(m.chat.id, "❌ Cancelled.")
    acc = (m.text or "").strip()[:120]
    if not acc:
        return bot.send_message(m.chat.id, "❌ Invalid account.")
    user_temp.setdefault(m.from_user.id, {})["wd_account"] = acc
    msg = bot.send_message(m.chat.id, f"💰 Send the *amount* to withdraw ({CURRENCY}):")
    bot.register_next_step_handler(msg, _wd_amount)


def _wd_amount(m):
    uid = m.from_user.id
    try:
        amount = float((m.text or "").strip())
    except Exception:
        return bot.send_message(m.chat.id, "❌ Invalid amount.")
    with db() as conn:
        u = conn.execute("SELECT balance FROM users WHERE id=?", (uid,)).fetchone()
    bal = u["balance"] if u else 0
    try:
        min_wd = float(setting("min_withdraw", str(MIN_WITHDRAW)))
    except Exception:
        min_wd = MIN_WITHDRAW
    if amount < min_wd:
        return bot.send_message(m.chat.id, f"⚠️ Minimum is {CURRENCY}{min_wd:.2f}")
    if amount > bal:
        return bot.send_message(m.chat.id, f"⚠️ Insufficient balance ({CURRENCY}{bal:.2f}).")
    data = user_temp.get(uid, {})
    data["wd_amount"] = amount
    user_temp[uid] = data
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("✅ Confirm", callback_data="wd_ok"),
           types.InlineKeyboardButton("❌ Cancel", callback_data="wd_cancel"))
    bot.send_message(
        m.chat.id,
        "🧾 *Confirm withdrawal*\n"
        f"Method: *{md_escape(data.get('wd_method'))}*\n"
        f"Account: {md_code(data.get('wd_account'))}\n"
        f"Amount: *{CURRENCY}{amount:.2f}*",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda cq: cq.data == "wd_ok")
def cb_wd_ok(cq):
    uid = cq.from_user.id
    data = user_temp.get(uid)
    if not data:
        return bot.answer_callback_query(cq.id, "Session expired")
    method = data.get("wd_method"); acc = data.get("wd_account"); amount = data.get("wd_amount")
    with db() as conn:
        u = conn.execute("SELECT balance, username, name FROM users WHERE id=?", (uid,)).fetchone()
        pend = conn.execute("SELECT 1 FROM withdrawals WHERE uid=? AND status='pending'", (uid,)).fetchone()
        if pend:
            try: bot.delete_message(cq.message.chat.id, cq.message.message_id)
            except Exception: pass
            return bot.answer_callback_query(cq.id, "Already pending", show_alert=True)
        if not u or u["balance"] < amount:
            return bot.answer_callback_query(cq.id, "Insufficient balance", show_alert=True)
        conn.execute(
            "INSERT INTO withdrawals(uid,username,name,amount,method,account,status,requested_at) "
            "VALUES(?,?,?,?,?,?, 'pending', ?)",
            (uid, u["username"], u["name"], amount, method, acc, utcnow_str()),
        )
    user_temp.pop(uid, None)
    try: bot.delete_message(cq.message.chat.id, cq.message.message_id)
    except Exception: pass
    bot.send_message(cq.message.chat.id,
                     "✅ *Withdrawal request submitted!*\nAdmin will review it shortly.")
    try:
        bot.send_message(
            ADMIN_ID,
            "💰 *New Withdrawal Request*\n"
            f"👤 {user_label(u['username'], uid, u['name'])}\n"
            f"🆔 {md_code(uid)}\n"
            f"💳 {md_escape(method)} • {md_code(acc)}\n"
            f"💵 *{CURRENCY}{amount:.2f}*"
        )
    except Exception:
        pass
    bot.answer_callback_query(cq.id, "Submitted")



# ╔══════════════════════════════════════════════════════════╗
# ║                  ⚙️  ADMIN PANEL                          ║
# ╚══════════════════════════════════════════════════════════╝
# ── Admin button labels ──
A_NUM   = "📱 NUMBER MANAGE"
A_USR   = "👥 USER MANAGE"
A_SET   = "⚙️ SETTINGS"
A_FIN   = "💰 FINANCE"
A_STA   = "📊 STATISTICS"
A_BC    = "📢 BROADCAST"
A_SUB   = "🛡️ SUB-ADMINS"
A_API   = "📡 ADD NUMBER API"
A_BACK  = "🔙 BACK"

# API Manage (VoltX live ranges)
AP_ADD_CAT = "📡➕ ADD CATEGORY"
AP_DEL_CAT = "📡🗑️ DEL CATEGORY"
AP_ADD_CN  = "📡🌍 ADD COUNTRY"
AP_DEL_CN  = "📡🗑️ DEL COUNTRY"
AP_ADD_RG  = "📡📥 ADD RANGE"
AP_DEL_RG  = "📡🗑️ DEL RANGE"
AP_BACK    = "🔙 Admin Panel"

# Number Manage
N_ADD_CAT = "➕ ADD CATEGORY"
N_DEL_CAT = "🗑️ DEL CATEGORY"
N_ADD_CN  = "🌍 ADD COUNTRY"
N_DEL_CN  = "🗑️ DEL COUNTRY"
N_ADD_NUM = "📥 ADD NUMBERS"
N_DEL_NUM = "🗑️ DEL NUMBERS"
N_BACK    = "🔙 Admin Panel"

# User Manage
U_LIST   = "📋 USERS"
U_BAN    = "🚫 BAN"
U_UNBAN  = "✅ UNBAN"
U_VIPG   = "⭐ VIP GIVE"
U_VIPR   = "💔 VIP REMOVE"
U_SPEC   = "✨ SPECIAL RATE USERS"
U_BACK   = "🔙 Admin Panel"

# Settings
S_COOL   = "⏱️ COOLDOWN"
S_LIMIT  = "🎚️ DAILY LIMIT"
S_PER    = "📱 PER REQUEST"
S_MAINT  = "🛠️ MAINTENANCE"
S_MINWD  = "💳 MIN WITHDRAW"
S_RATE   = "⚙️ OTP RATE"
S_LIVE   = "⚡ LIVE OTP RATE"
S_BACK   = "🔙 Admin Panel"

# Finance
F_REQ    = "💰 WITHDRAWAL REQUESTS"
F_HIST   = "📊 WITHDRAWAL HISTORY"
F_BACK   = "🔙 Admin Panel"

# Stats
ST_DASH  = "📊 DASHBOARD"
ST_TOPU  = "🏆 TOP OTP USERS"
ST_TOPC  = "🌍 TOP COUNTRIES"
ST_BACK  = "🔙 Admin Panel"

ADMIN_BTNS = {A_NUM, A_USR, A_SET, A_FIN, A_STA, A_BC, A_SUB, A_API, A_BACK,
              N_ADD_CAT, N_DEL_CAT, N_ADD_CN, N_DEL_CN, N_ADD_NUM, N_DEL_NUM, N_BACK,
              U_LIST, U_BAN, U_UNBAN, U_VIPG, U_VIPR, U_SPEC, U_BACK,
              S_COOL, S_LIMIT, S_PER, S_MAINT, S_MINWD, S_RATE, S_LIVE, S_BACK,
              F_REQ, F_HIST, F_BACK,
              ST_DASH, ST_TOPU, ST_TOPC, ST_BACK,
              AP_ADD_CAT, AP_DEL_CAT, AP_ADD_CN, AP_DEL_CN, AP_ADD_RG, AP_DEL_RG, AP_BACK}


def admin_menu_kb() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=2)
    kb.row(A_NUM, A_USR)
    kb.row(A_SET, A_FIN)
    kb.row(A_STA, A_BC)
    kb.row(A_SUB, A_API)
    kb.row(A_BACK)
    return kb

def api_menu_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=2)
    kb.row(AP_ADD_CAT, AP_DEL_CAT)
    kb.row(AP_ADD_CN,  AP_DEL_CN)
    kb.row(AP_ADD_RG,  AP_DEL_RG)
    kb.row(AP_BACK)
    return kb

def num_menu_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=2)
    kb.row(N_ADD_CAT, N_DEL_CAT)
    kb.row(N_ADD_CN,  N_DEL_CN)
    kb.row(N_ADD_NUM, N_DEL_NUM)
    kb.row(N_BACK)
    return kb

def usr_menu_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=2)
    kb.row(U_LIST)
    kb.row(U_BAN, U_UNBAN)
    kb.row(U_VIPG, U_VIPR)
    kb.row(U_SPEC)
    kb.row(U_BACK)
    return kb

def set_menu_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=2)
    kb.row(S_COOL, S_LIMIT)
    kb.row(S_PER,  S_MAINT)
    kb.row(S_MINWD, S_RATE)
    kb.row(S_LIVE)
    kb.row(S_BACK)
    return kb

def fin_menu_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=1)
    kb.row(F_REQ)
    kb.row(F_HIST)
    kb.row(F_BACK)
    return kb

def stats_menu_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=2)
    kb.row(ST_DASH)
    kb.row(ST_TOPU, ST_TOPC)
    kb.row(ST_BACK)
    return kb


def _admin_only(m):
    return is_admin(m.from_user.id)

def _main_admin_only(m):
    return m.from_user.id == ADMIN_ID


# ── Override placeholder: open admin panel ──
@bot.message_handler(func=lambda m: btn_is(m.text, "btn_admin"))
def h_admin(m):
    if not is_admin(m.from_user.id):
        return
    bot.send_message(m.chat.id, "👑 *ADMIN PANEL*", reply_markup=admin_menu_kb())

# Generic back-to-main
@bot.message_handler(func=lambda m: m.text == A_BACK and _admin_only(m))
def adm_back_main(m):
    bot.send_message(m.chat.id, "🏠 Main Menu", reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: m.text in (N_BACK, U_BACK, S_BACK, F_BACK, ST_BACK, AP_BACK) and _admin_only(m))
def adm_back_panel(m):
    bot.send_message(m.chat.id, "👑 *ADMIN PANEL*", reply_markup=admin_menu_kb())


# ╔════════════ 📱 NUMBER MANAGE ════════════╗
@bot.message_handler(func=lambda m: m.text == A_NUM and _admin_only(m))
def adm_num(m):
    bot.send_message(m.chat.id, "📱 *NUMBER MANAGE*", reply_markup=num_menu_kb())

# Add Category
@bot.message_handler(func=lambda m: m.text == N_ADD_CAT and _admin_only(m))
def adm_add_cat(m):
    msg = bot.send_message(m.chat.id,
        "➕ *Add Category*\nSend: `<emoji> <Name>`\nExample: `📘 Facebook`\n\n/cancel to abort.")
    bot.register_next_step_handler(msg, _save_cat)

def _save_cat(m):
    if not is_admin(m.from_user.id): return
    txt = (m.text or "").strip()
    if txt.startswith("/cancel"):
        return bot.send_message(m.chat.id, "❌ Cancelled.", reply_markup=num_menu_kb())
    parts = txt.split(maxsplit=1)
    emoji = parts[0] if parts and len(parts[0]) <= 4 else "📞"
    name = parts[1].strip() if len(parts) > 1 else (parts[0] if parts else "")
    if not name:
        return bot.send_message(m.chat.id, "❌ Need a name!", reply_markup=num_menu_kb())
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO categories(name,emoji) VALUES(?,?)", (name, emoji))
    bot.send_message(m.chat.id, f"✅ Added: {emoji} *{name}*", reply_markup=num_menu_kb())

# Delete Category
@bot.message_handler(func=lambda m: m.text == N_DEL_CAT and _admin_only(m))
def adm_del_cat(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ No categories.", reply_markup=num_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"❌ {c['emoji']} {c['name']}",
            callback_data=f"adelcat|{c['name']}"))
    bot.send_message(m.chat.id, "🗑 Pick category to delete:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("adelcat|"))
def cb_adelcat(cq):
    if not is_admin(cq.from_user.id):
        return bot.answer_callback_query(cq.id, "🚫")
    cat = cq.data.split("|",1)[1]
    with db() as conn:
        conn.execute("DELETE FROM categories WHERE name=?", (cat,))
        conn.execute("DELETE FROM countries WHERE cat=?", (cat,))
        conn.execute("DELETE FROM numbers WHERE cat=?", (cat,))
    bot.edit_message_text(f"🗑 Deleted: *{cat}*", cq.message.chat.id, cq.message.message_id)
    bot.answer_callback_query(cq.id, "Deleted")

# Add Country
@bot.message_handler(func=lambda m: m.text == N_ADD_CN and _admin_only(m))
def adm_add_cn(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ Add a category first.", reply_markup=num_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"aaddcn|{c['name']}"))
    bot.send_message(m.chat.id, "🌍 Choose category to add country into:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("aaddcn|"))
def cb_aaddcn(cq):
    if not is_admin(cq.from_user.id): return
    cat = cq.data.split("|",1)[1]
    user_temp[cq.from_user.id] = {"add_cn_cat": cat}
    bot.answer_callback_query(cq.id)
    msg = bot.send_message(cq.message.chat.id,
        f"🌍 Adding country to *{cat}*.\nSend: `<flag> <Country>`\nExample: `🇧🇩 Bangladesh`\n\n/cancel")
    bot.register_next_step_handler(msg, _save_cn)

def _save_cn(m):
    if not is_admin(m.from_user.id): return
    txt = (m.text or "").strip()
    if txt.startswith("/cancel"):
        user_temp.pop(m.from_user.id, None)
        return bot.send_message(m.chat.id, "❌ Cancelled.", reply_markup=num_menu_kb())
    cat = user_temp.get(m.from_user.id, {}).get("add_cn_cat", "")
    parts = txt.split(maxsplit=1)
    first = parts[0] if parts else ""
    if len(first) <= 4 and len(parts) > 1:
        emoji, name = first, parts[1].strip()
    else:
        emoji, name = "🌐", txt
    if not name:
        return bot.send_message(m.chat.id, "❌ Need a name!", reply_markup=num_menu_kb())
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO countries(cat,name,emoji) VALUES(?,?,?)", (cat, name, emoji))
    user_temp.pop(m.from_user.id, None)
    bot.send_message(m.chat.id, f"✅ Added: {emoji} *{name}* → _{cat}_", reply_markup=num_menu_kb())

# Delete Country
@bot.message_handler(func=lambda m: m.text == N_DEL_CN and _admin_only(m))
def adm_del_cn(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ No categories.", reply_markup=num_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"adelcn1|{c['name']}"))
    bot.send_message(m.chat.id, "🗑 Choose category:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("adelcn1|"))
def cb_adelcn1(cq):
    if not is_admin(cq.from_user.id): return
    cat = cq.data.split("|",1)[1]
    with db() as conn:
        cns = conn.execute("SELECT name, emoji FROM countries WHERE cat=? ORDER BY name", (cat,)).fetchall()
    if not cns:
        return bot.answer_callback_query(cq.id, "No countries.", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in cns:
        kb.add(types.InlineKeyboardButton(f"❌ {c['emoji']} {c['name']}",
            callback_data=f"adelcn2|{cat}|{c['name']}"))
    bot.edit_message_text(f"🗑 *{cat}* — pick country to delete:",
                          cq.message.chat.id, cq.message.message_id, reply_markup=kb)
    bot.answer_callback_query(cq.id)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("adelcn2|"))
def cb_adelcn2(cq):
    if not is_admin(cq.from_user.id): return
    _, cat, country = cq.data.split("|", 2)
    with db() as conn:
        conn.execute("DELETE FROM countries WHERE cat=? AND name=?", (cat, country))
        conn.execute("DELETE FROM numbers WHERE cat=? AND country=?", (cat, country))
    bot.edit_message_text(f"🗑 Deleted: *{country}* from _{cat}_",
                          cq.message.chat.id, cq.message.message_id)
    bot.answer_callback_query(cq.id, "Deleted")

# Add Numbers (text or file)
@bot.message_handler(func=lambda m: m.text == N_ADD_NUM and _admin_only(m))
def adm_add_num(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ Add a category first.", reply_markup=num_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"aaddn1|{c['name']}"))
    bot.send_message(m.chat.id, "📥 Pick category:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("aaddn1|"))
def cb_aaddn1(cq):
    if not is_admin(cq.from_user.id): return
    cat = cq.data.split("|",1)[1]
    with db() as conn:
        cns = conn.execute("SELECT name, emoji FROM countries WHERE cat=? ORDER BY name", (cat,)).fetchall()
    if not cns:
        return bot.answer_callback_query(cq.id, "Add a country first.", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cns:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}",
               callback_data=f"aaddn2|{cat}|{c['name']}"))
    bot.edit_message_text(f"📥 *{cat}* — pick country:",
                          cq.message.chat.id, cq.message.message_id, reply_markup=kb)
    bot.answer_callback_query(cq.id)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("aaddn2|"))
def cb_aaddn2(cq):
    if not is_admin(cq.from_user.id): return
    _, cat, country = cq.data.split("|", 2)
    user_temp[cq.from_user.id] = {"add_n_cat": cat, "add_n_country": country}
    bot.answer_callback_query(cq.id)
    msg = bot.send_message(cq.message.chat.id,
        f"📥 *{cat} • {country}*\n"
        "Paste numbers (one per line, comma or space separated)\n"
        "OR upload a *.csv / .txt / .xlsx* file.\n\n/cancel")
    bot.register_next_step_handler(msg, _save_numbers)

def _save_numbers(m):
    if not is_admin(m.from_user.id): return
    if (m.text or "").startswith("/cancel"):
        user_temp.pop(m.from_user.id, None)
        return bot.send_message(m.chat.id, "❌ Cancelled.", reply_markup=num_menu_kb())
    data = user_temp.get(m.from_user.id, {})
    cat = data.get("add_n_cat", ""); country = data.get("add_n_country", "")
    if not cat or not country:
        return bot.send_message(m.chat.id, "❌ Session expired.", reply_markup=num_menu_kb())

    raw = ""
    # File upload?
    if m.content_type == "document" and m.document:
        try:
            f = bot.get_file(m.document.file_id)
            blob = bot.download_file(f.file_path)
            fname = (m.document.file_name or "").lower()
            if fname.endswith(".xlsx"):
                try:
                    import openpyxl, io
                    wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
                    pieces = []
                    for ws in wb.worksheets:
                        for row in ws.iter_rows(values_only=True):
                            for v in row:
                                if v is not None:
                                    pieces.append(str(v))
                    raw = "\n".join(pieces)
                except ImportError:
                    return bot.send_message(m.chat.id, "❌ Install openpyxl for .xlsx, or send .csv/.txt.",
                                            reply_markup=num_menu_kb())
            else:
                try: raw = blob.decode("utf-8", errors="ignore")
                except Exception: raw = ""
        except Exception as e:
            return bot.send_message(m.chat.id, f"❌ File error: {e}", reply_markup=num_menu_kb())
    else:
        raw = m.text or ""

    pieces = re.split(r"[\r\n,;\s]+", raw)
    added = 0; dup = 0
    with db() as conn:
        for p in pieces:
            num = re.sub(r"[^\d+]", "", p)
            if len(re.sub(r"\D","",num)) < 6: continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO numbers(num,cat,country,used) VALUES(?,?,?,0)",
                    (num, cat, country))
                if cur.rowcount: added += 1
                else: dup += 1
            except Exception:
                pass
    user_temp.pop(m.from_user.id, None)
    bot.send_message(m.chat.id,
        f"✅ Added *{added}* numbers → _{cat} • {country}_\n(skipped {dup} duplicates)",
        reply_markup=num_menu_kb())

# Allow document upload to also reach _save_numbers (next_step covers it)

# Delete Numbers (by country)
@bot.message_handler(func=lambda m: m.text == N_DEL_NUM and _admin_only(m))
def adm_del_num(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ No categories.", reply_markup=num_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}",
               callback_data=f"adeln1|{c['name']}"))
    bot.send_message(m.chat.id, "🗑 Pick category:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("adeln1|"))
def cb_adeln1(cq):
    if not is_admin(cq.from_user.id): return
    cat = cq.data.split("|",1)[1]
    with db() as conn:
        cns = conn.execute(
            "SELECT c.name, c.emoji, "
            " (SELECT COUNT(*) FROM numbers n WHERE n.cat=c.cat AND n.country=c.name AND n.used=0) AS cnt "
            "FROM countries c WHERE c.cat=? ORDER BY c.name", (cat,)).fetchall()
    if not cns:
        return bot.answer_callback_query(cq.id, "No countries.", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in cns:
        kb.add(types.InlineKeyboardButton(f"❌ {c['emoji']} {c['name']} ({c['cnt']})",
               callback_data=f"adeln2|{cat}|{c['name']}"))
    bot.edit_message_text(f"🗑 *{cat}* — pick country:",
                          cq.message.chat.id, cq.message.message_id, reply_markup=kb)
    bot.answer_callback_query(cq.id)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("adeln2|"))
def cb_adeln2(cq):
    if not is_admin(cq.from_user.id): return
    _, cat, country = cq.data.split("|", 2)
    with db() as conn:
        cur = conn.execute("DELETE FROM numbers WHERE cat=? AND country=? AND used=0", (cat, country))
        n = cur.rowcount
    bot.edit_message_text(f"🗑 Removed *{n}* unused numbers from *{country}*.",
                          cq.message.chat.id, cq.message.message_id)
    bot.answer_callback_query(cq.id, "Deleted")


# ╔════════════ 📡 ADD NUMBER API (VoltX live ranges) ════════════╗
@bot.message_handler(func=lambda m: m.text == A_API and _admin_only(m))
def adm_api(m):
    bot.send_message(m.chat.id, "📡 *ADD NUMBER API*\nManage live-number categories, countries & ranges.",
                     reply_markup=api_menu_kb())

# ── Add API Category ──
@bot.message_handler(func=lambda m: m.text == AP_ADD_CAT and _admin_only(m))
def adm_api_add_cat(m):
    msg = bot.send_message(m.chat.id,
        "📡➕ *Add API Category*\nSend: `<emoji> <Name>`\nExample: `📘 Facebook`\n\n/cancel")
    bot.register_next_step_handler(msg, _save_api_cat)

def _save_api_cat(m):
    if not is_admin(m.from_user.id): return
    txt = (m.text or "").strip()
    if txt.startswith("/cancel"):
        return bot.send_message(m.chat.id, "❌ Cancelled.", reply_markup=api_menu_kb())
    parts = txt.split(maxsplit=1)
    emoji = parts[0] if parts and len(parts[0]) <= 4 else "📡"
    name = parts[1].strip() if len(parts) > 1 else (parts[0] if parts else "")
    if not name:
        return bot.send_message(m.chat.id, "❌ Need a name!", reply_markup=api_menu_kb())
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO api_categories(name,emoji) VALUES(?,?)", (name, emoji))
    bot.send_message(m.chat.id, f"✅ Added: {emoji} *{name}*", reply_markup=api_menu_kb())

# ── Delete API Category ──
@bot.message_handler(func=lambda m: m.text == AP_DEL_CAT and _admin_only(m))
def adm_api_del_cat(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM api_categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ No API categories.", reply_markup=api_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"❌ {c['emoji']} {c['name']}",
            callback_data=f"apdelcat|{c['name']}"))
    bot.send_message(m.chat.id, "🗑 Pick API category to delete:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apdelcat|"))
def cb_apdelcat(cq):
    if not is_admin(cq.from_user.id):
        return bot.answer_callback_query(cq.id, "🚫")
    cat = cq.data.split("|",1)[1]
    with db() as conn:
        conn.execute("DELETE FROM api_categories WHERE name=?", (cat,))
        conn.execute("DELETE FROM api_countries WHERE cat=?", (cat,))
        conn.execute("DELETE FROM api_ranges WHERE cat=?", (cat,))
    bot.edit_message_text(f"🗑 Deleted API category: *{cat}*", cq.message.chat.id, cq.message.message_id)
    bot.answer_callback_query(cq.id, "Deleted")

# ── Add API Country ──
@bot.message_handler(func=lambda m: m.text == AP_ADD_CN and _admin_only(m))
def adm_api_add_cn(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM api_categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ Add an API category first.", reply_markup=api_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"apaddcn|{c['name']}"))
    bot.send_message(m.chat.id, "🌍 Choose API category:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apaddcn|"))
def cb_apaddcn(cq):
    if not is_admin(cq.from_user.id): return
    cat = cq.data.split("|",1)[1]
    user_temp[cq.from_user.id] = {"api_add_cn_cat": cat}
    bot.answer_callback_query(cq.id)
    msg = bot.send_message(cq.message.chat.id,
        f"🌍 Adding country to *{cat}*.\nSend: `<flag> <Country>`\nExample: `🇧🇩 Bangladesh`\n\n/cancel")
    bot.register_next_step_handler(msg, _save_api_cn)

def _save_api_cn(m):
    if not is_admin(m.from_user.id): return
    txt = (m.text or "").strip()
    if txt.startswith("/cancel"):
        user_temp.pop(m.from_user.id, None)
        return bot.send_message(m.chat.id, "❌ Cancelled.", reply_markup=api_menu_kb())
    cat = user_temp.get(m.from_user.id, {}).get("api_add_cn_cat", "")
    if not cat:
        return bot.send_message(m.chat.id, "❌ Session expired.", reply_markup=api_menu_kb())
    parts = txt.split(maxsplit=1)
    first = parts[0] if parts else ""
    if len(first) <= 4 and len(parts) > 1:
        emoji, name = first, parts[1].strip()
    else:
        emoji, name = country_flag(txt) or "🌐", txt
    if not name:
        return bot.send_message(m.chat.id, "❌ Need a name!", reply_markup=api_menu_kb())
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO api_countries(cat,name,emoji) VALUES(?,?,?)", (cat, name, emoji))
    user_temp.pop(m.from_user.id, None)
    bot.send_message(m.chat.id, f"✅ Added: {emoji} *{name}* → _{cat}_", reply_markup=api_menu_kb())

# ── Delete API Country ──
@bot.message_handler(func=lambda m: m.text == AP_DEL_CN and _admin_only(m))
def adm_api_del_cn(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM api_categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ No API categories.", reply_markup=api_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"apdelcn1|{c['name']}"))
    bot.send_message(m.chat.id, "🗑 Choose category:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apdelcn1|"))
def cb_apdelcn1(cq):
    if not is_admin(cq.from_user.id): return
    cat = cq.data.split("|",1)[1]
    with db() as conn:
        cns = conn.execute("SELECT name, emoji FROM api_countries WHERE cat=? ORDER BY name", (cat,)).fetchall()
    if not cns:
        return bot.answer_callback_query(cq.id, "No countries.", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in cns:
        kb.add(types.InlineKeyboardButton(f"❌ {c['emoji']} {c['name']}",
            callback_data=f"apdelcn2|{cat}|{c['name']}"))
    bot.edit_message_text(f"🗑 *{cat}* — pick country to delete:",
                          cq.message.chat.id, cq.message.message_id, reply_markup=kb)
    bot.answer_callback_query(cq.id)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apdelcn2|"))
def cb_apdelcn2(cq):
    if not is_admin(cq.from_user.id): return
    _, cat, country = cq.data.split("|", 2)
    with db() as conn:
        conn.execute("DELETE FROM api_countries WHERE cat=? AND name=?", (cat, country))
        conn.execute("DELETE FROM api_ranges WHERE cat=? AND country=?", (cat, country))
    bot.edit_message_text(f"🗑 Deleted: *{country}* from _{cat}_",
                          cq.message.chat.id, cq.message.message_id)
    bot.answer_callback_query(cq.id, "Deleted")

# ── Add Range ──
@bot.message_handler(func=lambda m: m.text == AP_ADD_RG and _admin_only(m))
def adm_api_add_rg(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM api_categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ Add a category first.", reply_markup=api_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"apaddrg1|{c['name']}"))
    bot.send_message(m.chat.id, "📥 Pick category:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apaddrg1|"))
def cb_apaddrg1(cq):
    if not is_admin(cq.from_user.id): return
    cat = cq.data.split("|",1)[1]
    with db() as conn:
        cns = conn.execute("SELECT name, emoji FROM api_countries WHERE cat=? ORDER BY name", (cat,)).fetchall()
    if not cns:
        return bot.answer_callback_query(cq.id, "Add a country first.", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cns:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}",
               callback_data=f"apaddrg2|{cat}|{c['name']}"))
    bot.edit_message_text(f"📥 *{cat}* — pick country:",
                          cq.message.chat.id, cq.message.message_id, reply_markup=kb)
    bot.answer_callback_query(cq.id)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apaddrg2|"))
def cb_apaddrg2(cq):
    if not is_admin(cq.from_user.id): return
    _, cat, country = cq.data.split("|", 2)
    user_temp[cq.from_user.id] = {"api_add_rg_cat": cat, "api_add_rg_country": country}
    bot.answer_callback_query(cq.id)
    msg = bot.send_message(cq.message.chat.id,
        f"📥 *{cat} • {country}*\n"
        "Send ranges (digits only, one per line, comma or space separated)\n"
        "Example: `26134`\n\n/cancel")
    bot.register_next_step_handler(msg, _save_api_ranges)

def _save_api_ranges(m):
    if not is_admin(m.from_user.id): return
    if (m.text or "").startswith("/cancel"):
        user_temp.pop(m.from_user.id, None)
        return bot.send_message(m.chat.id, "❌ Cancelled.", reply_markup=api_menu_kb())
    data = user_temp.get(m.from_user.id, {})
    cat = data.get("api_add_rg_cat", ""); country = data.get("api_add_rg_country", "")
    if not cat or not country:
        return bot.send_message(m.chat.id, "❌ Session expired.", reply_markup=api_menu_kb())
    pieces = re.split(r"[\r\n,;\s]+", m.text or "")
    added = 0; dup = 0
    now = utcnow_str()
    with db() as conn:
        for p in pieces:
            r = re.sub(r"\D", "", p)
            if len(r) < 3: continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO api_ranges(cat,country,range_id,added_at) VALUES(?,?,?,?)",
                (cat, country, r, now))
            if cur.rowcount: added += 1
            else: dup += 1
    user_temp.pop(m.from_user.id, None)
    bot.send_message(m.chat.id,
        f"✅ Added *{added}* ranges → _{cat} • {country}_\n(skipped {dup} duplicates)",
        reply_markup=api_menu_kb())

# ── Delete Range ──
@bot.message_handler(func=lambda m: m.text == AP_DEL_RG and _admin_only(m))
def adm_api_del_rg(m):
    with db() as conn:
        cats = conn.execute("SELECT name, emoji FROM api_categories ORDER BY name").fetchall()
    if not cats:
        return bot.send_message(m.chat.id, "⚠️ No categories.", reply_markup=api_menu_kb())
    kb = types.InlineKeyboardMarkup(row_width=2)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}",
               callback_data=f"apdelrg1|{c['name']}"))
    bot.send_message(m.chat.id, "🗑 Pick category:", reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apdelrg1|"))
def cb_apdelrg1(cq):
    if not is_admin(cq.from_user.id): return
    cat = cq.data.split("|",1)[1]
    with db() as conn:
        cns = conn.execute(
            "SELECT c.name, c.emoji, "
            " (SELECT COUNT(*) FROM api_ranges r WHERE r.cat=c.cat AND r.country=c.name) AS cnt "
            "FROM api_countries c WHERE c.cat=? ORDER BY c.name", (cat,)).fetchall()
    if not cns:
        return bot.answer_callback_query(cq.id, "No countries.", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in cns:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']} ({c['cnt']})",
               callback_data=f"apdelrg2|{cat}|{c['name']}"))
    bot.edit_message_text(f"🗑 *{cat}* — pick country:",
                          cq.message.chat.id, cq.message.message_id, reply_markup=kb)
    bot.answer_callback_query(cq.id)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apdelrg2|"))
def cb_apdelrg2(cq):
    if not is_admin(cq.from_user.id): return
    _, cat, country = cq.data.split("|", 2)
    with db() as conn:
        rgs = conn.execute(
            "SELECT id, range_id FROM api_ranges WHERE cat=? AND country=? ORDER BY range_id",
            (cat, country)).fetchall()
    if not rgs:
        return bot.answer_callback_query(cq.id, "No ranges.", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=2)
    for r in rgs:
        kb.add(types.InlineKeyboardButton(f"❌ {r['range_id']}",
               callback_data=f"apdelrg3|{r['id']}"))
    kb.add(types.InlineKeyboardButton("🗑 DELETE ALL", callback_data=f"apdelrgA|{cat}|{country}"))
    bot.edit_message_text(f"🗑 *{cat} • {country}* — pick range:",
                          cq.message.chat.id, cq.message.message_id, reply_markup=kb)
    bot.answer_callback_query(cq.id)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apdelrg3|"))
def cb_apdelrg3(cq):
    if not is_admin(cq.from_user.id): return
    rid = int(cq.data.split("|",1)[1])
    with db() as conn:
        conn.execute("DELETE FROM api_ranges WHERE id=?", (rid,))
    bot.answer_callback_query(cq.id, "Deleted")
    try:
        bot.edit_message_text("🗑 Range deleted.", cq.message.chat.id, cq.message.message_id)
    except Exception:
        pass

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("apdelrgA|"))
def cb_apdelrgA(cq):
    if not is_admin(cq.from_user.id): return
    _, cat, country = cq.data.split("|", 2)
    with db() as conn:
        cur = conn.execute("DELETE FROM api_ranges WHERE cat=? AND country=?", (cat, country))
        n = cur.rowcount
    bot.edit_message_text(f"🗑 Removed *{n}* ranges from *{country}*.",
                          cq.message.chat.id, cq.message.message_id)
    bot.answer_callback_query(cq.id, "Deleted")



# ╔════════════ 👥 USER MANAGE ════════════╗
@bot.message_handler(func=lambda m: m.text == A_USR and _admin_only(m))
def adm_usr(m):
    bot.send_message(m.chat.id, "👥 *USER MANAGE*", reply_markup=usr_menu_kb())

@bot.message_handler(func=lambda m: m.text == U_LIST and _admin_only(m))
def adm_users_list(m):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, username, balance, vip, banned FROM users "
            "ORDER BY joined_at DESC LIMIT 30").fetchall()
        total = conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    out = [f"👥 *Users — {total} total* (showing latest 30)", "━━━━━━━━━━━━━━━━━━━━"]
    for r in rows:
        tag = ("🚫" if r["banned"] else ("👑" if r["vip"] else "•"))
        out.append(f"{tag} {user_label(r['username'], r['id'])} • {CURRENCY}{r['balance']:.2f}")
    bot.send_message(m.chat.id, "\n".join(out), reply_markup=usr_menu_kb())

def _ask_id(m, prompt, fn):
    msg = bot.send_message(m.chat.id, prompt + "\n\n/cancel")
    bot.register_next_step_handler(msg, fn)

@bot.message_handler(func=lambda m: m.text == U_BAN and _admin_only(m))
def adm_ban(m): _ask_id(m, "🚫 Send user *ID* to BAN:", _do_ban)
def _do_ban(m):
    if not is_admin(m.from_user.id) or (m.text or "").startswith("/"): return
    try:
        tid = int(m.text.strip())
        with db() as conn: conn.execute("UPDATE users SET banned=1 WHERE id=?", (tid,))
        bot.send_message(m.chat.id, f"✅ Banned `{tid}`", reply_markup=usr_menu_kb())
    except Exception: bot.send_message(m.chat.id, "❌ Bad ID", reply_markup=usr_menu_kb())

@bot.message_handler(func=lambda m: m.text == U_UNBAN and _admin_only(m))
def adm_unban(m): _ask_id(m, "✅ Send user *ID* to UNBAN:", _do_unban)
def _do_unban(m):
    if not is_admin(m.from_user.id) or (m.text or "").startswith("/"): return
    try:
        tid = int(m.text.strip())
        with db() as conn: conn.execute("UPDATE users SET banned=0 WHERE id=?", (tid,))
        bot.send_message(m.chat.id, f"✅ Unbanned `{tid}`", reply_markup=usr_menu_kb())
    except Exception: bot.send_message(m.chat.id, "❌ Bad ID", reply_markup=usr_menu_kb())

@bot.message_handler(func=lambda m: m.text == U_VIPG and _admin_only(m))
def adm_vipg(m): _ask_id(m, "⭐ Send user *ID* to make VIP:", _do_vipg)
def _do_vipg(m):
    if not is_admin(m.from_user.id) or (m.text or "").startswith("/"): return
    try:
        tid = int(m.text.strip())
        with db() as conn: conn.execute("UPDATE users SET vip=1 WHERE id=?", (tid,))
        bot.send_message(m.chat.id, f"✅ VIP given to `{tid}`", reply_markup=usr_menu_kb())
        try: bot.send_message(tid, "⭐ *You are now VIP!*")
        except Exception: pass
    except Exception: bot.send_message(m.chat.id, "❌ Bad ID", reply_markup=usr_menu_kb())

@bot.message_handler(func=lambda m: m.text == U_VIPR and _admin_only(m))
def adm_vipr(m): _ask_id(m, "💔 Send user *ID* to REMOVE VIP:", _do_vipr)
def _do_vipr(m):
    if not is_admin(m.from_user.id) or (m.text or "").startswith("/"): return
    try:
        tid = int(m.text.strip())
        with db() as conn: conn.execute("UPDATE users SET vip=0 WHERE id=?", (tid,))
        bot.send_message(m.chat.id, f"✅ VIP removed from `{tid}`", reply_markup=usr_menu_kb())
    except Exception: bot.send_message(m.chat.id, "❌ Bad ID", reply_markup=usr_menu_kb())

# Special Rate Users
@bot.message_handler(func=lambda m: m.text == U_SPEC and _admin_only(m))
def adm_spec(m):
    with db() as conn:
        rows = conn.execute("SELECT id, name, username FROM users WHERE special_rate=1 ORDER BY id").fetchall()
    out = ["✨ *Special Rate Users* (+৳0.2/OTP)", "━━━━━━━━━━━━━━━━━━━━"]
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not rows:
        out.append("_(none yet)_")
    for r in rows:
        out.append(f"• {user_label(r['username'], r['id'], r['name'])}")
        kb.add(types.InlineKeyboardButton(f"❌ Remove {r['username'] or r['id']}",
               callback_data=f"specrm|{r['id']}"))
    kb.add(types.InlineKeyboardButton("➕ Add by Username", callback_data="specadd_uname"))
    kb.add(types.InlineKeyboardButton("➕ Add by User ID", callback_data="specadd_id"))
    bot.send_message(m.chat.id, "\n".join(out), reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data == "specadd_uname")
def cb_specadd_uname(cq):
    if not is_admin(cq.from_user.id): return
    bot.answer_callback_query(cq.id)
    msg = bot.send_message(cq.message.chat.id,
        "✨ *Username দাও* (@ সহ বা ছাড়া) — Special Rate দেওয়া হবে:\n\n/cancel")
    bot.register_next_step_handler(msg, _do_specadd_uname)

def _do_specadd_uname(m):
    if not is_admin(m.from_user.id) or (m.text or "").startswith("/"): return
    uname = (m.text or "").strip().lstrip("@")
    if not uname: return bot.send_message(m.chat.id, "❌ Empty.", reply_markup=usr_menu_kb())
    with db() as conn:
        cur = conn.execute("UPDATE users SET special_rate=1 WHERE username=?", (uname,))
        if cur.rowcount == 0:
            bot.send_message(m.chat.id,
                f"❌ @{uname} নামে কোনো user পাওয়া যায়নি।\nUser ID দিয়ে চেষ্টা করো।",
                reply_markup=usr_menu_kb())
        else:
            row = conn.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
            if row:
                try: bot.send_message(row["id"],
                    f"✨ তোমাকে *Special Rate* দেওয়া হয়েছে! এখন থেকে প্রতি OTP-তে +{CURRENCY}0.20 বেশি পাবে।"
                )
                except Exception: pass
            bot.send_message(m.chat.id, f"✅ @{uname} এখন +{CURRENCY}0.2/OTP পাবে।", reply_markup=usr_menu_kb())

@bot.callback_query_handler(func=lambda cq: cq.data == "specadd_id")
def cb_specadd_id(cq):
    if not is_admin(cq.from_user.id): return
    bot.answer_callback_query(cq.id)
    msg = bot.send_message(cq.message.chat.id,
        "✨ *User ID দাও* — Special Rate দেওয়া হবে:\n\n/cancel")
    bot.register_next_step_handler(msg, _do_specadd_id)

def _do_specadd_id(m):
    if not is_admin(m.from_user.id) or (m.text or "").startswith("/"): return
    try:
        tid = int((m.text or "").strip())
    except Exception:
        return bot.send_message(m.chat.id, "❌ Valid ID দাও (শুধু সংখ্যা)।", reply_markup=usr_menu_kb())
    with db() as conn:
        cur = conn.execute("UPDATE users SET special_rate=1 WHERE id=?", (tid,))
        if cur.rowcount == 0:
            bot.send_message(m.chat.id, f"❌ ID `{tid}` নামে কোনো user পাওয়া যায়নি।", reply_markup=usr_menu_kb())
        else:
            try: bot.send_message(tid,
                f"✨ তোমাকে *Special Rate* দেওয়া হয়েছে! এখন থেকে প্রতি OTP-তে +{CURRENCY}0.20 বেশি পাবে।"
            )
            except Exception: pass
            bot.send_message(m.chat.id, f"✅ ID `{tid}` এখন +{CURRENCY}0.2/OTP পাবে।", reply_markup=usr_menu_kb())

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("specrm|"))
def cb_specrm(cq):
    if not is_admin(cq.from_user.id): return
    tid = int(cq.data.split("|",1)[1])
    with db() as conn:
        conn.execute("UPDATE users SET special_rate=0 WHERE id=?", (tid,))
    bot.answer_callback_query(cq.id, "Removed")
    try: bot.edit_message_reply_markup(cq.message.chat.id, cq.message.message_id, reply_markup=None)
    except Exception: pass
    bot.send_message(cq.message.chat.id, f"✅ Removed special rate from `{tid}`")


# ╔════════════ ⚙️ SETTINGS ════════════╗
@bot.message_handler(func=lambda m: m.text == A_SET and _admin_only(m))
def adm_set(m):
    bot.send_message(m.chat.id, "⚙️ *SETTINGS*", reply_markup=set_menu_kb())

def _set_setting(key, val):
    with db() as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(val)))

def _ask_setting(m, label, key, default, fn):
    cur = setting(key, str(default))
    msg = bot.send_message(m.chat.id, f"{label}\nCurrent: *{cur}*\nSend new value:\n\n/cancel")
    bot.register_next_step_handler(msg, fn)

@bot.message_handler(func=lambda m: m.text == S_COOL and _admin_only(m))
def adm_s_cool(m): _ask_setting(m, "⏱️ *Cooldown (sec)*", "cooldown", 30, _save_cool)
def _save_cool(m):
    if (m.text or "").startswith("/"): return
    try:
        v = max(0, int(m.text.strip())); _set_setting("cooldown", v)
        bot.send_message(m.chat.id, f"✅ Cooldown: {v}s", reply_markup=set_menu_kb())
    except Exception: bot.send_message(m.chat.id, "❌ Number please", reply_markup=set_menu_kb())

@bot.message_handler(func=lambda m: m.text == S_LIMIT and _admin_only(m))
def adm_s_limit(m): _ask_setting(m, "🎚️ *Daily Limit* (0=∞)", "daily_limit", 20, _save_limit)
def _save_limit(m):
    if (m.text or "").startswith("/"): return
    try:
        v = max(0, int(m.text.strip())); _set_setting("daily_limit", v)
        bot.send_message(m.chat.id, f"✅ Daily limit: {v}", reply_markup=set_menu_kb())
    except Exception: bot.send_message(m.chat.id, "❌ Number please", reply_markup=set_menu_kb())

@bot.message_handler(func=lambda m: m.text == S_PER and _admin_only(m))
def adm_s_per(m): _ask_setting(m, "📱 *Numbers per request* (1-10)", "per_request", 2, _save_per)
def _save_per(m):
    if (m.text or "").startswith("/"): return
    try:
        v = max(1, min(10, int(m.text.strip()))); _set_setting("per_request", v)
        bot.send_message(m.chat.id, f"✅ Per request: {v}", reply_markup=set_menu_kb())
    except Exception: bot.send_message(m.chat.id, "❌ Number please", reply_markup=set_menu_kb())

@bot.message_handler(func=lambda m: m.text == S_MAINT and _admin_only(m))
def adm_s_maint(m):
    cur = setting("maintenance", "off")
    new = "off" if cur == "on" else "on"
    _set_setting("maintenance", new)
    bot.send_message(m.chat.id, f"🛠️ Maintenance: *{new.upper()}*", reply_markup=set_menu_kb())

@bot.message_handler(func=lambda m: m.text == S_MINWD and _admin_only(m))
def adm_s_minwd(m): _ask_setting(m, "💳 *Min withdraw*", "min_withdraw", MIN_WITHDRAW, _save_minwd)
def _save_minwd(m):
    if (m.text or "").startswith("/"): return
    try:
        v = max(0.0, float(m.text.strip())); _set_setting("min_withdraw", v)
        bot.send_message(m.chat.id, f"✅ Min withdraw: {CURRENCY}{v:.2f}", reply_markup=set_menu_kb())
    except Exception: bot.send_message(m.chat.id, "❌ Number please", reply_markup=set_menu_kb())

# OTP Rate per country
@bot.message_handler(func=lambda m: m.text == S_LIVE and _admin_only(m))
def adm_s_live(m):
    cur = setting("live_otp_rate", "1.0")
    msg = bot.send_message(
        m.chat.id,
        f"⚡ *Live OTP Rate*\nCurrent: *{CURRENCY}{cur}* per live OTP\n\nSend new rate (e.g. `1.5`):",
        reply_markup=set_menu_kb(),
    )
    bot.register_next_step_handler(msg, _save_live_rate)


def _save_live_rate(m):
    try:
        v = float((m.text or "").strip())
        with db() as conn:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('live_otp_rate',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(v),),
            )
        bot.send_message(m.chat.id, f"✅ Live OTP rate: {CURRENCY}{v:.2f}", reply_markup=set_menu_kb())
    except Exception:
        bot.send_message(m.chat.id, "❌ Number please", reply_markup=set_menu_kb())


@bot.message_handler(func=lambda m: m.text == S_RATE and _admin_only(m))
def adm_s_rate(m):
    with db() as conn:
        cns = conn.execute("SELECT DISTINCT country FROM countries ORDER BY country").fetchall()
        rates = {r["country"]: r["rate"] for r in conn.execute("SELECT country, rate FROM otp_rates").fetchall()}
    out = [f"⚙️ *OTP Rate per Country*\n(Default: {CURRENCY}{DEFAULT_OTP_RATE:.2f})", "━━━━━━━━━━━━━━━━"]
    kb = types.InlineKeyboardMarkup(row_width=2)
    if not cns:
        out.append("_(no countries yet)_")
    for c in cns:
        nm = c["country"]; r = rates.get(nm, DEFAULT_OTP_RATE)
        out.append(f"• {nm}: *{CURRENCY}{r:.2f}*")
        kb.add(types.InlineKeyboardButton(f"✏️ {nm}", callback_data=f"rate|{nm}"))
    bot.send_message(m.chat.id, "\n".join(out), reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("rate|"))
def cb_rate(cq):
    if not is_admin(cq.from_user.id): return
    country = cq.data.split("|",1)[1]
    user_temp[cq.from_user.id] = {"rate_country": country}
    bot.answer_callback_query(cq.id)
    msg = bot.send_message(cq.message.chat.id,
        f"⚙️ Set OTP rate for *{country}* in {CURRENCY} (e.g. 1.5):\n\n/cancel")
    bot.register_next_step_handler(msg, _save_rate)

def _save_rate(m):
    if not is_admin(m.from_user.id) or (m.text or "").startswith("/"): return
    country = user_temp.get(m.from_user.id, {}).get("rate_country")
    if not country: return
    try:
        v = max(0.0, float(m.text.strip()))
        with db() as conn:
            conn.execute(
                "INSERT INTO otp_rates(country,rate) VALUES(?,?) "
                "ON CONFLICT(country) DO UPDATE SET rate=excluded.rate", (country, v))
        bot.send_message(m.chat.id, f"✅ {country} rate = {CURRENCY}{v:.2f}", reply_markup=set_menu_kb())
    except Exception:
        bot.send_message(m.chat.id, "❌ Number please", reply_markup=set_menu_kb())
    user_temp.pop(m.from_user.id, None)


# ╔════════════ 💰 FINANCE ════════════╗
@bot.message_handler(func=lambda m: m.text == A_FIN and _admin_only(m))
def adm_fin(m):
    bot.send_message(m.chat.id, "💰 *FINANCE*", reply_markup=fin_menu_kb())

@bot.message_handler(func=lambda m: m.text == F_REQ and _admin_only(m))
def adm_wd_req(m):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, uid, username, name, amount, method, account, requested_at "
            "FROM withdrawals WHERE status='pending' ORDER BY id LIMIT 20"
        ).fetchall()
    if not rows:
        return bot.send_message(m.chat.id, "✅ No pending withdrawals.", reply_markup=fin_menu_kb())
    for r in rows:
        txt = (f"💰 *Withdrawal #{r['id']}*\n"
               f"👤 {user_label(r['username'], r['uid'], r['name'])}\n"
               f"🆔 {md_code(r['uid'])}\n"
               f"💳 {md_escape(r['method'])} • {md_code(r['account'])}\n"
               f"💵 *{CURRENCY}{r['amount']:.2f}*\n"
               f"📅 {md_escape(r['requested_at'])}")
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("✅ Approve", callback_data=f"wdok|{r['id']}"),
               types.InlineKeyboardButton("❌ Reject",  callback_data=f"wdno|{r['id']}"))
        bot.send_message(m.chat.id, txt, reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("wdok|"))
def cb_wdok(cq):
    if not is_admin(cq.from_user.id): return
    wid = int(cq.data.split("|",1)[1])
    with db() as conn:
        r = conn.execute("SELECT * FROM withdrawals WHERE id=? AND status='pending'", (wid,)).fetchone()
        if not r:
            return bot.answer_callback_query(cq.id, "Already processed.", show_alert=True)
        u = conn.execute("SELECT balance FROM users WHERE id=?", (r["uid"],)).fetchone()
        if not u or u["balance"] < r["amount"]:
            conn.execute("UPDATE withdrawals SET status='rejected', processed_at=? WHERE id=?",
                         (utcnow_str(), wid))
            bot.answer_callback_query(cq.id, "Insufficient — auto-rejected", show_alert=True)
            try: bot.edit_message_text(cq.message.text + "\n\n❌ Auto-rejected (insufficient).",
                                       cq.message.chat.id, cq.message.message_id)
            except Exception: pass
            return
        conn.execute("UPDATE users SET balance=balance-? WHERE id=?", (r["amount"], r["uid"]))
        conn.execute("UPDATE withdrawals SET status='approved', processed_at=? WHERE id=?",
                     (utcnow_str(), wid))
    try:
        bot.send_message(r["uid"],
            f"✅ *Withdrawal Approved!*\n"
            f"💳 {md_escape(r['method'])} • {md_code(r['account'])}\n"
            f"💵 {CURRENCY}{r['amount']:.2f}")
    except Exception: pass
    try: bot.edit_message_text(cq.message.text + "\n\n✅ *APPROVED*",
                               cq.message.chat.id, cq.message.message_id)
    except Exception: pass
    bot.answer_callback_query(cq.id, "Approved")

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("wdno|"))
def cb_wdno(cq):
    if not is_admin(cq.from_user.id): return
    wid = int(cq.data.split("|",1)[1])
    with db() as conn:
        r = conn.execute("SELECT * FROM withdrawals WHERE id=? AND status='pending'", (wid,)).fetchone()
        if not r:
            return bot.answer_callback_query(cq.id, "Already processed.", show_alert=True)
        conn.execute("UPDATE withdrawals SET status='rejected', processed_at=? WHERE id=?",
                     (utcnow_str(), wid))
    try:
        bot.send_message(r["uid"],
            f"❌ *Withdrawal Rejected*\n"
            f"💵 {CURRENCY}{r['amount']:.2f} via {md_escape(r['method'])}\n"
            "Your balance was not deducted.")
    except Exception: pass
    try: bot.edit_message_text(cq.message.text + "\n\n❌ *REJECTED*",
                               cq.message.chat.id, cq.message.message_id)
    except Exception: pass
    bot.answer_callback_query(cq.id, "Rejected")

@bot.message_handler(func=lambda m: m.text == F_HIST and _admin_only(m))
def adm_wd_hist(m):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, uid, username, amount, method, status, processed_at "
            "FROM withdrawals WHERE status!='pending' ORDER BY id DESC LIMIT 30"
        ).fetchall()
    if not rows:
        return bot.send_message(m.chat.id, "_(no history yet)_", reply_markup=fin_menu_kb())
    out = ["📊 *Withdrawal History* (latest 30)", "━━━━━━━━━━━━━━━━━━━━"]
    for r in rows:
        icon = "✅" if r["status"] == "approved" else "❌"
        out.append(f"{icon} #{r['id']} {user_label(r['username'], r['uid'])} • {CURRENCY}{r['amount']:.2f} "
                   f"• {md_escape(r['method'])} • {md_escape(r['processed_at'] or '—')}")
    bot.send_message(m.chat.id, "\n".join(out), reply_markup=fin_menu_kb())


# ╔════════════ 📊 STATISTICS ════════════╗
@bot.message_handler(func=lambda m: m.text == A_STA and _admin_only(m))
def adm_stats(m):
    bot.send_message(m.chat.id, "📊 *STATISTICS*", reply_markup=stats_menu_kb())

@bot.message_handler(func=lambda m: m.text == ST_DASH and _admin_only(m))
def adm_dash(m):
    with db() as conn:
        total_u = conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        vip_u   = conn.execute("SELECT COUNT(*) n FROM users WHERE vip=1").fetchone()["n"]
        ban_u   = conn.execute("SELECT COUNT(*) n FROM users WHERE banned=1").fetchone()["n"]
        stock   = conn.execute("SELECT COUNT(*) n FROM numbers WHERE used=0").fetchone()["n"]
        used    = conn.execute("SELECT COUNT(*) n FROM numbers WHERE used=1").fetchone()["n"]
        otp_t   = conn.execute("SELECT IFNULL(SUM(otp_count),0) n FROM users").fetchone()["n"]
        bal_t   = conn.execute("SELECT IFNULL(SUM(balance),0) n FROM users").fetchone()["n"]
        paid_t  = conn.execute("SELECT IFNULL(SUM(amount),0) n FROM withdrawals WHERE status='approved'").fetchone()["n"]
        pend    = conn.execute("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'").fetchone()["n"]
    txt = (
        "📊 *DASHBOARD*\n━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Users: *{total_u}*  •  ⭐ VIP: *{vip_u}*  •  🚫 Banned: *{ban_u}*\n"
        f"📦 Stock: *{stock}*  •  ✅ Used: *{used}*\n"
        f"📩 Total OTPs: *{otp_t}*\n"
        f"💰 Total Balance: *{CURRENCY}{bal_t:.2f}*\n"
        f"💵 Total Paid: *{CURRENCY}{paid_t:.2f}*\n"
        f"⏳ Pending Withdrawals: *{pend}*"
    )
    bot.send_message(m.chat.id, txt, reply_markup=stats_menu_kb())

@bot.message_handler(func=lambda m: m.text == ST_TOPU and _admin_only(m))
def adm_top_users(m):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, username, name, otp_count, balance FROM users "
            "WHERE otp_count>0 ORDER BY otp_count DESC LIMIT 10").fetchall()
    if not rows:
        return bot.send_message(m.chat.id, "_(no OTPs yet)_", reply_markup=stats_menu_kb())
    out = ["🏆 *Top OTP Users*", "━━━━━━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows, 1):
        out.append(f"{i}. {user_label(r['username'], r['id'], r['name'])} — *{r['otp_count']}* OTP "
                   f"• {CURRENCY}{r['balance']:.2f}")
    bot.send_message(m.chat.id, "\n".join(out), reply_markup=stats_menu_kb())

@bot.message_handler(func=lambda m: m.text == ST_TOPC and _admin_only(m))
def adm_top_countries(m):
    with db() as conn:
        rows = conn.execute(
            "SELECT country, COUNT(*) n FROM history "
            "WHERE country IS NOT NULL AND country!='' "
            "GROUP BY country ORDER BY n DESC LIMIT 10").fetchall()
    if not rows:
        return bot.send_message(m.chat.id, "_(no data yet)_", reply_markup=stats_menu_kb())
    out = ["🌍 *Top Countries (by issued numbers)*", "━━━━━━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows, 1):
        out.append(f"{i}. {r['country']} — *{r['n']}*")
    bot.send_message(m.chat.id, "\n".join(out), reply_markup=stats_menu_kb())


# ╔════════════ 📢 BROADCAST ════════════╗
@bot.message_handler(func=lambda m: m.text == A_BC and _admin_only(m))
def adm_bcast(m):
    msg = bot.send_message(
        m.chat.id,
        "📢 Send the *broadcast* now:\n"
        "Text / photo / video / sticker / voice / document — anything.\n\n"
        "/cancel"
    )
    bot.register_next_step_handler(msg, _do_bcast)

def _do_bcast(m):
    if not is_admin(m.from_user.id): return
    if (m.text or "").strip().startswith("/"):
        return bot.send_message(m.chat.id, "❌ Cancelled.", reply_markup=admin_menu_kb())
    with db() as conn:
        users = [r["id"] for r in conn.execute("SELECT id FROM users WHERE banned=0").fetchall()]
    bot.send_message(m.chat.id, f"📤 Sending to {len(users)} users…")
    ok = fail = 0
    for tid in users:
        try:
            # copy_message keeps the original media type (text, sticker, voice, photo, etc.)
            # and avoids downloading/re-uploading files.
            bot.copy_message(tid, m.chat.id, m.message_id)
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
    bot.send_message(m.chat.id, f"✅ Sent: *{ok}*  •  ❌ Failed: *{fail}*",
                     reply_markup=admin_menu_kb())


# ╔════════════ 🛡️ SUB-ADMINS ════════════╗
@bot.message_handler(func=lambda m: m.text == A_SUB and _main_admin_only(m))
def adm_sub(m):
    with db() as conn:
        subs = conn.execute("SELECT uid, added_at FROM sub_admins ORDER BY added_at").fetchall()
    out = ["🛡️ *Sub-Admins*", "━━━━━━━━━━━━━━━━━━━━"]
    if not subs:
        out.append("_(none)_")
    for s in subs:
        out.append(f"• `{s['uid']}` ({s['added_at'][:10]})")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("➕ Add", callback_data="subadd"),
           types.InlineKeyboardButton("➖ Remove", callback_data="subrm"))
    bot.send_message(m.chat.id, "\n".join(out), reply_markup=kb)

@bot.callback_query_handler(func=lambda cq: cq.data == "subadd")
def cb_subadd(cq):
    if cq.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(cq.id, "🚫 Main admin only", show_alert=True)
    bot.answer_callback_query(cq.id)
    msg = bot.send_message(ADMIN_ID, "➕ Send sub-admin *user ID*:\n\n/cancel")
    bot.register_next_step_handler(msg, _do_subadd)

def _do_subadd(m):
    if m.from_user.id != ADMIN_ID or (m.text or "").startswith("/"): return
    try:
        tid = int(m.text.strip())
        if tid == ADMIN_ID:
            return bot.send_message(ADMIN_ID, "ℹ️ You are the Main Admin.", reply_markup=admin_menu_kb())
        with db() as conn:
            conn.execute("INSERT OR IGNORE INTO sub_admins(uid,added_at) VALUES(?,?)", (tid, utcnow_str()))
            conn.execute("INSERT OR IGNORE INTO users(id,name,lang,joined_at) VALUES(?,?,?,?)",
                         (tid, "Sub-Admin", "en", utcnow_str()))
        ADMIN_CACHE.add(tid)
        bot.send_message(ADMIN_ID, f"✅ `{tid}` is now sub-admin.", reply_markup=admin_menu_kb())
        try: bot.send_message(tid, "🛡️ *You are now a Sub-Admin!* Type /start.")
        except Exception: pass
    except Exception:
        bot.send_message(ADMIN_ID, "❌ Bad ID", reply_markup=admin_menu_kb())

@bot.callback_query_handler(func=lambda cq: cq.data == "subrm")
def cb_subrm(cq):
    if cq.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(cq.id, "🚫 Main admin only", show_alert=True)
    bot.answer_callback_query(cq.id)
    msg = bot.send_message(ADMIN_ID, "➖ Send sub-admin *user ID* to remove:\n\n/cancel")
    bot.register_next_step_handler(msg, _do_subrm)

def _do_subrm(m):
    if m.from_user.id != ADMIN_ID or (m.text or "").startswith("/"): return
    try:
        tid = int(m.text.strip())
        with db() as conn: conn.execute("DELETE FROM sub_admins WHERE uid=?", (tid,))
        ADMIN_CACHE.discard(tid)
        bot.send_message(ADMIN_ID, f"✅ Removed `{tid}`.", reply_markup=admin_menu_kb())
    except Exception:
        bot.send_message(ADMIN_ID, "❌ Bad ID", reply_markup=admin_menu_kb())


# ╔══════════════════════════════════════════════════════════╗
# ║              📩 OTP auto-forward + Balance                ║
# ╚══════════════════════════════════════════════════════════╝
# ╔══════════════════════════════════════════════════════════╗
# ║                  🌐 VoltX (Live Number) API               ║
# ╚══════════════════════════════════════════════════════════╝
VOLTX_HEADERS = {"mauthapi": VOLTX_API_KEY, "Content-Type": "application/json"}

COUNTRY_FLAGS = {
    # ── Europe ──
    "United Kingdom": "🇬🇧", "England": "🇬🇧", "Great Britain": "🇬🇧",
    "Germany": "🇩🇪", "France": "🇫🇷", "Spain": "🇪🇸", "Italy": "🇮🇹",
    "Netherlands": "🇳🇱", "Belgium": "🇧🇪", "Switzerland": "🇨🇭",
    "Austria": "🇦🇹", "Sweden": "🇸🇪", "Norway": "🇳🇴", "Denmark": "🇩🇰",
    "Finland": "🇫🇮", "Poland": "🇵🇱", "Ukraine": "🇺🇦", "Russia": "🇷🇺",
    "Turkey": "🇹🇷", "Greece": "🇬🇷", "Portugal": "🇵🇹", "Czech Republic": "🇨🇿",
    "Czechia": "🇨🇿", "Slovakia": "🇸🇰", "Hungary": "🇭🇺", "Romania": "🇷🇴",
    "Bulgaria": "🇧🇬", "Serbia": "🇷🇸", "Croatia": "🇭🇷", "Slovenia": "🇸🇮",
    "Bosnia and Herzegovina": "🇧🇦", "Montenegro": "🇲🇪", "Albania": "🇦🇱",
    "North Macedonia": "🇲🇰", "Kosovo": "🇽🇰", "Moldova": "🇲🇩",
    "Belarus": "🇧🇾", "Latvia": "🇱🇻", "Lithuania": "🇱🇹", "Estonia": "🇪🇪",
    "Iceland": "🇮🇸", "Ireland": "🇮🇪", "Luxembourg": "🇱🇺", "Malta": "🇲🇹",
    "Cyprus": "🇨🇾", "Liechtenstein": "🇱🇮", "Monaco": "🇲🇨",
    "San Marino": "🇸🇲", "Vatican": "🇻🇦", "Andorra": "🇦🇩",

    # ── Americas ──
    "United States": "🇺🇸", "USA": "🇺🇸", "Canada": "🇨🇦",
    "Mexico": "🇲🇽", "Brazil": "🇧🇷", "Argentina": "🇦🇷", "Colombia": "🇨🇴",
    "Chile": "🇨🇱", "Peru": "🇵🇪", "Venezuela": "🇻🇪", "Ecuador": "🇪🇨",
    "Bolivia": "🇧🇴", "Paraguay": "🇵🇾", "Uruguay": "🇺🇾", "Guyana": "🇬🇾",
    "Suriname": "🇸🇷", "French Guiana": "🇬🇫", "Cuba": "🇨🇺", "Haiti": "🇭🇹",
    "Dominican Republic": "🇩🇴", "Jamaica": "🇯🇲", "Trinidad and Tobago": "🇹🇹",
    "Barbados": "🇧🇧", "Bahamas": "🇧🇸", "Belize": "🇧🇿",
    "Guatemala": "🇬🇹", "Honduras": "🇭🇳", "El Salvador": "🇸🇻",
    "Nicaragua": "🇳🇮", "Costa Rica": "🇨🇷", "Panama": "🇵🇦",
    "Puerto Rico": "🇵🇷", "Grenada": "🇬🇩", "Saint Lucia": "🇱🇨",
    "Saint Vincent and the Grenadines": "🇻🇨", "Antigua and Barbuda": "🇦🇬",
    "Dominica": "🇩🇲", "Saint Kitts and Nevis": "🇰🇳",

    # ── Asia ──
    "India": "🇮🇳", "China": "🇨🇳", "Japan": "🇯🇵", "South Korea": "🇰🇷",
    "North Korea": "🇰🇵", "Bangladesh": "🇧🇩", "Pakistan": "🇵🇰",
    "Sri Lanka": "🇱🇰", "Nepal": "🇳🇵", "Bhutan": "🇧🇹", "Maldives": "🇲🇻",
    "Afghanistan": "🇦🇫", "Iran": "🇮🇷", "Iraq": "🇮🇶", "Syria": "🇸🇾",
    "Lebanon": "🇱🇧", "Jordan": "🇯🇴", "Israel": "🇮🇱", "Palestine": "🇵🇸",
    "Saudi Arabia": "🇸🇦", "UAE": "🇦🇪", "United Arab Emirates": "🇦🇪",
    "Qatar": "🇶🇦", "Kuwait": "🇰🇼", "Bahrain": "🇧🇭", "Oman": "🇴🇲",
    "Yemen": "🇾🇪", "Indonesia": "🇮🇩", "Malaysia": "🇲🇾",
    "Singapore": "🇸🇬", "Philippines": "🇵🇭", "Thailand": "🇹🇭",
    "Vietnam": "🇻🇳", "Cambodia": "🇰🇭", "Laos": "🇱🇦", "Myanmar": "🇲🇲",
    "Brunei": "🇧🇳", "East Timor": "🇹🇱", "Timor-Leste": "🇹🇱",
    "Mongolia": "🇲🇳", "Kazakhstan": "🇰🇿", "Uzbekistan": "🇺🇿",
    "Turkmenistan": "🇹🇲", "Kyrgyzstan": "🇰🇬", "Tajikistan": "🇹🇯",
    "Azerbaijan": "🇦🇿", "Armenia": "🇦🇲", "Georgia": "🇬🇪",
    "Taiwan": "🇹🇼", "Hong Kong": "🇭🇰", "Macau": "🇲🇴",

    # ── Africa ──
    "Nigeria": "🇳🇬", "Ethiopia": "🇪🇹", "Egypt": "🇪🇬",
    "Democratic Republic of the Congo": "🇨🇩", "DR Congo": "🇨🇩",
    "Tanzania": "🇹🇿", "Kenya": "🇰🇪", "Uganda": "🇺🇬", "Algeria": "🇩🇿",
    "Sudan": "🇸🇩", "South Sudan": "🇸🇸", "Morocco": "🇲🇦",
    "Angola": "🇦🇴", "Mozambique": "🇲🇿", "Ghana": "🇬🇭", "Cameroon": "🇨🇲",
    "Madagascar": "🇲🇬", "Ivory Coast": "🇨🇮", "Côte d'Ivoire": "🇨🇮",
    "Niger": "🇳🇪", "Burkina Faso": "🇧🇫", "Mali": "🇲🇱", "Malawi": "🇲🇼",
    "Zambia": "🇿🇲", "Senegal": "🇸🇳", "Chad": "🇹🇩", "Somalia": "🇸🇴",
    "Zimbabwe": "🇿🇼", "Guinea": "🇬🇳", "Rwanda": "🇷🇼", "Benin": "🇧🇯",
    "Burundi": "🇧🇮", "Tunisia": "🇹🇳", "South Africa": "🇿🇦",
    "Togo": "🇹🇬", "Sierra Leone": "🇸🇱", "Libya": "🇱🇾", "Congo": "🇨🇬",
    "Republic of the Congo": "🇨🇬", "Liberia": "🇱🇷",
    "Central African Republic": "🇨🇫", "Mauritania": "🇲🇷",
    "Eritrea": "🇪🇷", "Namibia": "🇳🇦", "Gambia": "🇬🇲",
    "Botswana": "🇧🇼", "Gabon": "🇬🇦", "Lesotho": "🇱🇸",
    "Guinea-Bissau": "🇬🇼", "Equatorial Guinea": "🇬🇶",
    "Mauritius": "🇲🇺", "Eswatini": "🇸🇿", "Djibouti": "🇩🇯",
    "Réunion": "🇷🇪", "Comoros": "🇰🇲", "Cape Verde": "🇨🇻",
    "Sao Tome and Principe": "🇸🇹", "Seychelles": "🇸🇨",

    # ── Oceania ──
    "Australia": "🇦🇺", "New Zealand": "🇳🇿", "Papua New Guinea": "🇵🇬",
    "Fiji": "🇫🇯", "Solomon Islands": "🇸🇧", "Vanuatu": "🇻🇺",
    "Samoa": "🇼🇸", "Kiribati": "🇰🇮", "Tonga": "🇹🇴",
    "Micronesia": "🇫🇲", "Palau": "🇵🇼", "Marshall Islands": "🇲🇭",
    "Nauru": "🇳🇷", "Tuvalu": "🇹🇻",
}
def country_flag(name: str) -> str:
    return COUNTRY_FLAGS.get(name or "", "🌐")


def voltx_getnum(rid: str) -> dict | None:
    try:
        r = requests.post(f"{VOLTX_BASE_URL}/getnum",
                          json={"rid": str(rid)},
                          headers=VOLTX_HEADERS, timeout=15)
        j = r.json()
        if j.get("meta", {}).get("code") == 200:
            return j.get("data") or None
        log.warning(f"voltx getnum non-200: {j}")
    except Exception as e:
        log.warning(f"voltx getnum err: {e}")
    return None


def voltx_success_otp() -> list[dict]:
    try:
        r = requests.get(f"{VOLTX_BASE_URL}/success-otp",
                         headers=VOLTX_HEADERS, timeout=15)
        j = r.json()
        if j.get("meta", {}).get("code") == 200:
            return (j.get("data") or {}).get("otps") or []
    except Exception as e:
        log.warning(f"voltx success-otp err: {e}")
    return []


def live_number_card(range_id: str, items: list[dict], cat: str = "", country_name: str = "") -> str:
    if items:
        first_number = items[0].get("full_number") or ""
        country, flag = phone_country(first_number, items[0].get("country") or country_name or "")
    else:
        country = country_name or ""; flag = country_flag(country_name) if country_name else "🌐"
    title = f"{flag} {cat}⚡ ┊ {country or '—'} ✅" if cat else f"{flag} Country: *{country or '—'}*"
    lines = [
        "╔══ ✦ 𝗡𝗨𝗠𝗜𝗙𝗬 ✦ ══╗",
        "",
        title,
        "",
    ]
    for i, it in enumerate(items):
        bullet = NUM_BULLET[i] if i < len(NUM_BULLET) else f"({i+1})"
        lines.append(f"{bullet} 📱 `{it.get('full_number') or ''}`")
    lines.append("")
    lines.append("🔔 Waiting for OTP....")
    lines.append("╚══════════════════════╝")
    return "\n".join(lines)


def live_number_kb(cat: str = "", country: str = "") -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    if cat and country:
        kb.row(
            types.InlineKeyboardButton("🔄 Change Number", callback_data=f"achg_{cat}|{country}"),
            types.InlineKeyboardButton("🔔 View OTP", url=OTP_GROUP_LINK),
        )
    else:
        kb.row(
            types.InlineKeyboardButton("🔔 View OTP", url=OTP_GROUP_LINK),
        )
    kb.add(types.InlineKeyboardButton("🔙 Other Category", callback_data="pick"))
    return kb


def _do_live_fetch(chat_id: int, uid: int, range_id: str,
                   cat: str = "", country_name: str = "", edit_msg_id: int | None = None):
    """Call VoltX once for a single number, save, send card."""
    range_id = re.sub(r"\D", "", str(range_id))
    if not range_id:
        bot.send_message(chat_id, "⚠️ Invalid range.")
        return
    wait_mid = edit_msg_id
    if wait_mid:
        try:
            bot.edit_message_text("⏳ Getting a fresh number...", chat_id, wait_mid)
        except Exception:
            wait_mid = None
    if not wait_mid:
        wait = bot.send_message(chat_id, "⏳ Getting a fresh number...")
        wait_mid = wait.message_id
    items = []
    # Honor admin "per_request" setting for live/range numbers too.
    try:
        per_req = int(setting("per_request", "2") or "2")
    except Exception:
        per_req = 1
    per_req = max(1, min(10, per_req))

    # Build candidate range pool (current + others for same cat/country) as fallbacks.
    range_pool = [range_id]
    if cat and country_name:
        with db() as conn:
            others = conn.execute(
                "SELECT range_id FROM api_ranges WHERE cat=? AND country=? AND range_id!=? ORDER BY RANDOM()",
                (cat, country_name, range_id),
            ).fetchall()
        range_pool.extend([o["range_id"] for o in others])

    seen_nums = set()
    attempts = 0
    max_attempts = per_req * max(1, len(range_pool)) + 3
    while len(items) < per_req and attempts < max_attempts:
        rid = range_pool[attempts % len(range_pool)]
        attempts += 1
        d = voltx_getnum(rid)
        if not d:
            continue
        n = d.get("full_number") or ""
        if not n or n in seen_nums:
            continue
        seen_nums.add(n)
        items.append(d)
        range_id = rid
    if not items:
        try:
            bot.edit_message_text("❌ Not available in stock",
                                  chat_id, wait_mid)
        except Exception:
            bot.send_message(chat_id, "❌ Not available in stock")
        return
    now = utcnow_str()
    with db() as conn:
        for it in items:
            num = it.get("full_number") or ""
            detected_country, _ = phone_country(num, it.get("country") or country_name or "")
            conn.execute(
                "INSERT INTO live_numbers(uid,number,range_id,country,operator,taken_at,active) "
                "VALUES(?,?,?,?,?,?,1)",
                (uid, num, range_id, detected_country, it.get("operator") or "", now),
            )
    try:
        bot.edit_message_text(
            live_number_card(range_id, items, cat=cat, country_name=country_name),
            chat_id, wait_mid,
            reply_markup=live_number_kb(cat=cat, country=country_name),
            parse_mode="Markdown",
        )
    except Exception:
        bot.send_message(chat_id,
                         live_number_card(range_id, items, cat=cat, country_name=country_name),
                         reply_markup=live_number_kb(cat=cat, country=country_name),
                         parse_mode="Markdown")


# ── Legacy /start range_xxxxx deep-link compatibility ──────────
@bot.callback_query_handler(func=lambda cq: cq.data == "live_num")
def cb_live_num(cq):
    bot.answer_callback_query(cq.id, "ℹ️ Pick a category to get a number.", show_alert=False)


# ╔══════════════════════════════════════════════════════════╗
# ║              📡 API NUMBER — user flow                    ║
# ╚══════════════════════════════════════════════════════════╝
def api_country_kb(cat: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    with db() as conn:
        rows = conn.execute("""
            SELECT c.name, c.emoji,
                (SELECT COUNT(*) FROM api_ranges r WHERE r.cat=c.cat AND r.country=c.name) AS rcnt
            FROM api_countries c WHERE c.cat=? ORDER BY c.name
        """, (cat,)).fetchall()
    if not rows:
        kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="pick"))
        return kb
    btns = [types.InlineKeyboardButton(
        f"{r['emoji']} {bold_sans(r['name'])}",
        callback_data=f"acn_{cat}|{r['name']}"
    ) for r in rows]
    kb.add(*btns)
    kb.add(types.InlineKeyboardButton("🔙 Categories", callback_data="pick"))
    return kb


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("acat_"))
def cb_acat(cq):
    cat = cq.data[5:]
    try:
        bot.edit_message_text(f"🌍 *Pick a country* — _{cat}_", cq.message.chat.id,
                              cq.message.message_id, reply_markup=api_country_kb(cat),
                              parse_mode="Markdown")
    except Exception:
        pass
    bot.answer_callback_query(cq.id)


def _api_fetch_for_user(cq, cat: str, country: str, edit: bool = True):
    uid = cq.from_user.id
    chat_id = cq.message.chat.id
    mid = cq.message.message_id
    if is_banned(uid):
        return bot.answer_callback_query(cq.id, "🚫 Banned", show_alert=True)
    if setting("maintenance", "off") == "on" and not is_admin(uid):
        return bot.answer_callback_query(cq.id, "🛠 Maintenance", show_alert=True)
    with db() as conn:
        r = conn.execute(
            "SELECT range_id FROM api_ranges WHERE cat=? AND country=? ORDER BY RANDOM() LIMIT 1",
            (cat, country),
        ).fetchone()
    if not r:
        return bot.answer_callback_query(cq.id, "❌ Out of stock", show_alert=True)
    bot.answer_callback_query(cq.id, "⏳ Getting...")
    _do_live_fetch(chat_id, uid, r["range_id"], cat=cat, country_name=country,
                   edit_msg_id=mid if edit else None)


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("acn_"))
def cb_acn(cq):
    try:
        cat, country = cq.data[4:].split("|", 1)
    except Exception:
        return bot.answer_callback_query(cq.id, "⚠️ Bad data")
    _api_fetch_for_user(cq, cat, country, edit=True)


@bot.callback_query_handler(func=lambda cq: cq.data.startswith("achg_"))
def cb_achg(cq):
    try:
        cat, country = cq.data[5:].split("|", 1)
    except Exception:
        return bot.answer_callback_query(cq.id, "⚠️ Bad data")
    # Delete the old number card so the new one is posted at the bottom
    # (keeps recently-received OTPs visible above the fresh number).
    try:
        bot.delete_message(cq.message.chat.id, cq.message.message_id)
    except Exception:
        pass
    _api_fetch_for_user(cq, cat, country, edit=False)


# ╔══════════════════════════════════════════════════════════╗

# ── OTP card helpers (group / inbox) ──────────────────────────
def _extract_otp_code(text: str) -> str:
    t = text or ""
    # Normalize split codes like "123 456", "123-456", "123.456" -> "123456"
    # so Instagram / Google style 6-digit codes are captured fully.
    t_norm = re.sub(r'(?<=\d)[\s\-\.\u00A0](?=\d)', '', t)

    # 1) "code/otp/pin/password is 123456"
    m = re.search(
        r'(?:code|otp|pin|password|c[oó]digo|verification)[^\d]{0,15}(\d{3,10})',
        t_norm, re.I,
    )
    if m:
        return m.group(1)
    # 2) "123456 is your code"
    m = re.search(r'(\d{3,10})\s*(?:is your|as your|kod|code|verification)', t_norm, re.I)
    if m:
        return m.group(1)
    # 3) Fallback: pick the longest plausible numeric group (4-10 digits)
    nums = re.findall(r'\b(\d{3,10})\b', t_norm)
    best = ""
    for n in nums:
        if 4 <= len(n) <= 10 and len(n) > len(best):
            best = n
    if best:
        return best
    return nums[0] if nums else ""


def _mask_number(number: str) -> str:
    n = re.sub(r"\D", "", number or "")
    if len(n) <= 6:
        return n
    return n[:3] + "*" * (len(n) - 6) + n[-3:]


# Common app/service detection from OTP body
_SERVICE_PATTERNS = [
    (r"facebook|fb\b|meta\b", "Facebook"),
    (r"instagram|insta\b|ig\b", "Instagram"),
    (r"whatsapp", "WhatsApp"),
    (r"telegram", "Telegram"),
    (r"google|gmail|youtube", "Google"),
    (r"twitter|\bx\.com\b|\btweet", "Twitter/X"),
    (r"tiktok", "TikTok"),
    (r"snapchat", "Snapchat"),
    (r"discord", "Discord"),
    (r"signal", "Signal"),
    (r"viber", "Viber"),
    (r"wechat", "WeChat"),
    (r"line\b", "LINE"),
    (r"kakao", "KakaoTalk"),
    (r"imo\b", "imo"),
    (r"microsoft|outlook|hotmail|skype|xbox", "Microsoft"),
    (r"apple|icloud", "Apple"),
    (r"amazon", "Amazon"),
    (r"netflix", "Netflix"),
    (r"spotify", "Spotify"),
    (r"uber", "Uber"),
    (r"lyft", "Lyft"),
    (r"airbnb", "Airbnb"),
    (r"paypal", "PayPal"),
    (r"binance", "Binance"),
    (r"coinbase", "Coinbase"),
    (r"linkedin", "LinkedIn"),
    (r"pinterest", "Pinterest"),
    (r"reddit", "Reddit"),
    (r"twitch", "Twitch"),
    (r"steam", "Steam"),
    (r"epic games|epicgames", "Epic Games"),
    (r"riot", "Riot Games"),
    (r"openai|chatgpt", "OpenAI"),
    (r"yahoo", "Yahoo"),
    (r"yandex", "Yandex"),
    (r"vk\b|vkontakte", "VK"),
    (r"alibaba|aliexpress|alipay", "Alibaba"),
    (r"shopee", "Shopee"),
    (r"lazada", "Lazada"),
    (r"grab\b", "Grab"),
    (r"gojek", "Gojek"),
    (r"paytm", "Paytm"),
    (r"phonepe", "PhonePe"),
    (r"flipkart", "Flipkart"),
    (r"hinge", "Hinge"),
    (r"tinder", "Tinder"),
    (r"bumble", "Bumble"),
    (r"badoo", "Badoo"),
]


def detect_service(text: str, hint: str = "") -> str:
    """Return canonical app/service name from OTP body; fallback to hint or Unknown."""
    t = (text or "").lower()
    for pat, name in _SERVICE_PATTERNS:
        if re.search(pat, t):
            return name
    h = (hint or "").strip()
    if h and h.lower() not in ("unknown", "none", "null", "n/a", ""):
        return h
    return "Unknown"


def _range_display(number: str) -> str:
    """Build a 'Range' label like 22397XXX from a phone number."""
    n = re.sub(r"\D", "", number or "")
    if len(n) >= 8:
        return n[3:8] + "XXX"
    if len(n) >= 5:
        return n[:5] + "XXX"
    return (n or "—") + "XXX"


def _otp_lines(num_display: str, service: str, code: str,
               country: str = "", flag: str = "", full_number: str = "") -> str:
    """Plain Markdown variant (kept for back-compat)."""
    country_label = (f"{flag} {country}" if (flag or country) else "🌐 Unknown").strip()
    return (
        f"{country_label:<12}: `{num_display}`\n"
        f"🔧 Service  : {service or 'Unknown'}\n"
        f"🔑 OTP      : `{code or '-'}`"
    )


def _html_escape(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _otp_lines_html(num_display: str, service: str, code: str,
                    country: str = "", flag: str = "", full_number: str = "") -> str:
    """Premium HTML card wrapped in <blockquote> for quote-style design."""
    # Auto-detect country from full number if unknown
    if (not country or country.lower() in ("unknown", "")) and full_number:
        country, flag = phone_country(full_number, "")
    if not flag or not country or country.lower() == "unknown":
        country2, flag2 = phone_country(num_display, "")
        if country2 and country2.lower() != "unknown":
            country, flag = country2, flag2
    if not flag:
        flag = "🌐"
    if not country or country.lower() == "unknown":
        country = "Unknown"
    country_label = f"{flag} {country}"
    return (
        f"<blockquote>"
        f"<b>📩 NUMIFY OTP</b>\n"
        f"🌍 Country  : <b>{_html_escape(country_label)}</b>\n"
        f"📱 Number   : <code>{_html_escape(num_display)}</code>\n"
        f"🔧 Service  : <b>{_html_escape(service or 'Unknown')}</b>\n"
        f"🔑 OTP      : <code>{_html_escape(code or '-')}</code>"
        f"</blockquote>"
    )


def _copy_btn(code: str):
    try:
        return types.InlineKeyboardButton(
            "📋 Copy OTP",
            copy_text=types.CopyTextButton(text=code or "")
        )
    except Exception:
        return types.InlineKeyboardButton(
            "📋 Copy OTP",
            callback_data=("cpyotp:" + (code or ""))[:64]
        )


def group_otp_kb(code: str, rng: str = ""):
    kb = types.InlineKeyboardMarkup()
    kb.row(_copy_btn(code or ""))
    kb.row(
        types.InlineKeyboardButton("📱 Number Panel", url=f"https://t.me/{BOT_USERNAME}"),
    )
    return kb


def inbox_otp_kb(code: str, rng: str = ""):
    kb = types.InlineKeyboardMarkup()
    return kb


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("cpyotp:"))
def _cb_copy_otp(c):
    # Silent acknowledgement (no popup). Native CopyTextButton handles copy
    # silently when Telegram client supports Bot API 7.8+.
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("cpyrng:"))
def _cb_copy_rng(c):
    # Silent acknowledgement (no popup). Native CopyTextButton handles copy
    # silently when Telegram client supports Bot API 7.8+.
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


# ║          🛰  Background poller: live OTP forwarder        ║
# ╚══════════════════════════════════════════════════════════╝
def _live_otp_rate() -> float:
    try:
        return float(setting("live_otp_rate", str(DEFAULT_OTP_RATE)) or DEFAULT_OTP_RATE)
    except Exception:
        return DEFAULT_OTP_RATE


def otp_card(number: str, country: str, flag: str, service: str, message: str,
             rate: float | None = None) -> str:
    heading = "📩 OTP Received!" if rate is not None else "📩 Live OTP"
    lines = [
        "╔══ ✦ 𝗡𝗨𝗠𝗘𝗫 ✦ ══╗", "", heading, "",
        f"📱 {number}", f"{flag} {country}", f"📲 {service or 'Unknown'}", "",
        f"🔐 `{message}`", "",
    ]
    if rate is not None:
        lines.extend([f"💰 +{CURRENCY}{rate:.2f} added to your balance.", ""])
    lines.append("╚══════════════════════╝")
    return "\n".join(lines)


def _match_live_user(number: str) -> dict | None:
    n = re.sub(r"\D", "", number or "")
    if not n:
        return None
    with db() as conn:
        # try multiple forms
        r = conn.execute(
            "SELECT * FROM live_numbers WHERE active=1 AND "
            "(number=? OR number=? OR number=? OR number=?) ORDER BY id DESC LIMIT 1",
            (n, n.lstrip("0"), n[-10:], n[-9:]),
        ).fetchone()
        if not r:
            for tail_len in (10, 9, 8, 7):
                if len(n) < tail_len:
                    continue
                r = conn.execute(
                    "SELECT * FROM live_numbers WHERE active=1 AND "
                    "(number LIKE ? OR number LIKE ?) ORDER BY id DESC LIMIT 1",
                    (f"%{n[-tail_len:]}", f"%{n[-tail_len:]}%"),
                ).fetchone()
                if r:
                    break
    return dict(r) if r else None


def live_otp_worker():
    log.info("live_otp_worker started")
    # ── Prime: mark all currently-existing VoltX OTPs as seen so a fresh
    #         DB on restart (ephemeral host) does NOT replay old OTPs.
    try:
        initial = voltx_success_otp()
        now = utcnow_str()
        with db() as conn:
            for o in initial:
                oid = str(o.get("otp_id") or "")
                if oid:
                    conn.execute(
                        "INSERT OR IGNORE INTO live_otp_seen(otp_id,seen_at) VALUES(?,?)",
                        (oid, now),
                    )
        log.info(f"live_otp_worker primed {len(initial)} historical OTPs (skipped)")
    except Exception as e:
        log.warning(f"live_otp_worker prime failed: {e}")
    while True:
        try:
            otps = voltx_success_otp()
            for o in otps:
                oid = str(o.get("otp_id") or "")
                if not oid:
                    continue
                with db() as conn:
                    seen = conn.execute(
                        "SELECT 1 FROM live_otp_seen WHERE otp_id=?", (oid,)
                    ).fetchone()
                    if seen:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO live_otp_seen(otp_id,seen_at) VALUES(?,?)",
                        (oid, utcnow_str()),
                    )
                number = str(o.get("full_number") or o.get("number") or "")
                message = str(o.get("message") or "")
                raw_service = str(o.get("service") or o.get("operator") or o.get("sid") or "")
                service = detect_service(message, raw_service)
                match = _match_live_user(number)
                rate = _live_otp_rate()
                fallback_country = match["country"] if match else str(o.get("country") or "")
                country, flag = phone_country(number, fallback_country)
                code = _extract_otp_code(message)
                if match:
                    uid = match["uid"]
                    already_credited_live = bool(match.get("credited") or 0)
                    with db() as conn:
                        if not already_credited_live:
                            conn.execute(
                                "UPDATE users SET balance=balance+?, otp_count=otp_count+1 WHERE id=?",
                                (rate, uid),
                            )
                            conn.execute(
                                "UPDATE live_numbers SET credited=1 WHERE id=?",
                                (match["id"],),
                            )
                    try:
                        bot.send_message(
                            uid,
                            _otp_lines_html(number, service, code, country, flag, number)
                            + (f"\n<i>💰 +{CURRENCY}{rate:.2f} added to your balance.</i>"
                               if not already_credited_live
                               else "\n<i>ℹ️ This number was already credited — no extra balance for repeat OTP.</i>"),
                            reply_markup=inbox_otp_kb(code, _range_display(number)),
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        log.warning(f"live otp DM fail: {e}")
                # Always mirror to OTP group (masked)
                try:
                    bot.send_message(
                        OTP_CHAT_ID,
                        _otp_lines_html(_mask_number(number), service, code, country, flag, number),
                        reply_markup=group_otp_kb(code, _range_display(number)),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    log.warning(f"live otp group fail: {e}")
        except Exception as e:
            log.warning(f"live_otp_worker loop err: {e}")
        time.sleep(5)


def _otp_rate(country: str) -> float:
    with db() as conn:
        r = conn.execute("SELECT rate FROM otp_rates WHERE country=?", (country,)).fetchone()
    return float(r["rate"]) if r else DEFAULT_OTP_RATE


@bot.message_handler(
    func=lambda m: m.chat and m.chat.id == OTP_CHAT_ID,
    content_types=["text"],
)
def otp_group_handler(m):
    """Match OTP message to a user by last 3-4 digits of the number; credit balance."""
    text = m.text or ""
    if not text:
        return
    # Pull all phone-like sequences from the OTP message
    candidates = re.findall(r"\d{6,}", text)
    if not candidates:
        return

    with db() as conn:
        # find recently used numbers (last 24h)
        rows = conn.execute(
            "SELECT id, num, taken_by, cat, country, IFNULL(credited,0) AS credited FROM numbers "
            "WHERE used=1 AND taken_by!=0 "
            "ORDER BY id DESC LIMIT 500"
        ).fetchall()

    matched = None
    for r in rows:
        num = re.sub(r"\D", "", r["num"] or "")
        if not num: continue
        tail = num[-4:]
        if any(tail in c[-6:] for c in candidates):
            matched = r; break
        tail3 = num[-3:]
        if any(tail3 in c[-5:] for c in candidates):
            matched = r; break

    if not matched:
        return

    uid = matched["taken_by"]
    number = matched["num"] or ""
    country, flag = phone_country(number, matched["country"] or "")
    service = detect_service(text, matched["cat"] or "")
    rate = _otp_rate(country)

    already_credited = bool(matched["credited"])
    credit = 0.0
    with db() as conn:
        u = conn.execute("SELECT ref_by, ref_count, special_rate, vip FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return
        bonus = 0.0
        try:
            target = int(setting("ref_target", "10") or "10")
        except Exception:
            target = 10
        # VIP = শুধু badge, টাকা বাড়ায় না
        # Special Rate = +০.২০ টাকা বোনাস প্রতি OTP
        if u["special_rate"]:
            bonus += 0.2

        credit = rate + bonus

        # Pay only the FIRST OTP per number — no double credit on retries
        if not already_credited:
            conn.execute(
                "UPDATE users SET balance=balance+?, otp_count=otp_count+1 WHERE id=?",
                (credit, uid),
            )
            conn.execute(
                "UPDATE numbers SET credited=1 WHERE id=?",
                (matched["id"],),
            )

    code = _extract_otp_code(text)

    # Post formatted card to OTP group (masked number + buttons)
    try:
        bot.send_message(
            OTP_CHAT_ID,
            _otp_lines_html(_mask_number(number), service, code, country, flag, number),
            reply_markup=group_otp_kb(code, _range_display(number)),
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning(f"otp group card fail: {e}")

    # Forward OTP to user DM (full number + copy button) + credit notice
    try:
        time.sleep(1)
        bot.send_message(
            uid,
            _otp_lines_html(number, service, code, country, flag, number)
            + (f"\n<i>💰 +{CURRENCY}{credit:.2f} added to your balance.</i>"
               if not already_credited
               else "\n<i>ℹ️ This number was already credited — no extra balance for repeat OTP.</i>"),
            reply_markup=inbox_otp_kb(code, _range_display(number)),
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning(f"otp forward failed: {e}")


# ╔══════════════════════════════════════════════════════════╗
# ║                  Unknown command guard                    ║
# ╚══════════════════════════════════════════════════════════╝
@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(m):
    if anti_flood(m.from_user.id): return
    if (m.text or "").startswith("/"):
        bot.send_message(
            m.chat.id,
            "❗ Unsupported command. Use the keyboard buttons or type /start to continue.",
        )
        return
    # Unknown plain text → soft hint
    bot.send_message(
        m.chat.id,
        "🤖 Please use the menu buttons below, or type /start.",
        reply_markup=main_menu(m.from_user.id),
    )


# ╔══════════════════════════════════════════════════════════╗
# ║                  Bot commands menu                        ║
# ╚══════════════════════════════════════════════════════════╝
def setup_commands():
    try:
        bot.set_my_commands([
            types.BotCommand("start", "🚀 Start"),
        ])
    except Exception as e:
        log.warning(f"set_my_commands: {e}")


# ╔══════════════════════════════════════════════════════════╗
# ║                  MAIN                                     ║
# ╚══════════════════════════════════════════════════════════╝
if __name__ == "__main__":
    init_db()
    setup_commands()
    # Background: poll VoltX /success-otp every 5s and forward
    threading.Thread(target=live_otp_worker, daemon=True).start()
    print("✅ NUMIFY (FULL) running...")
    log.info("Bot started NUMIFY")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except Exception as e:
            log.error(f"polling crash: {e}")
            time.sleep(3)
