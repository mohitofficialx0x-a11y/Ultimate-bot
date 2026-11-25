import asyncio
import logging
import os
import sqlite3
import threading
import time
from flask import Flask
import requests
from telegram import (
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ================= LOGGING =================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("FastBot")

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9079313944:AAFXcg-quWi7ABouEQX8724Hiscr_wHLR9U")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

FORCE_CHANNELS = [
    {"id": -1004383355739, "name": "📢 JOIN CHANNEL 1", "url": "https://t.me/+ILptMG11g3c0ZDg1"},
    {"id": -1004474453856, "name": "📢 JOIN CHANNEL 2", "url": "https://t.me/+UyBmDcQRKORlNGI1"},
]

FREE_140_CHANNEL_URL = os.environ.get("FREE_140_CHANNEL_URL", "https://t.me/+ILptMG11g3c0ZDg1")
ADMIN_IDS = {8429424734, 8472943262}
DB_PATH = "bot_data.db"
PORT = int(os.environ.get("PORT", "8080"))

awaiting_broadcast = set()
maintenance_mode = False
last_bot_messages = {}  # user_id -> (chat_id, message_id)


# ================= DATABASE =================
def get_db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with get_db() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT
            )
            """
        )


def save_user(user):
    if not user:
        return
    with get_db() as con:
        con.execute(
            """
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (user.id, user.username or "", user.first_name or ""),
        )


def get_all_user_ids():
    with get_db() as con:
        rows = con.execute("SELECT user_id FROM users").fetchall()
    return [r["user_id"] for r in rows]


# ================= WEB SERVER & KEEP-ALIVE =================
web_app = Flask(__name__)


@web_app.get("/")
def home():
    return "OK", 200


@web_app.get("/health")
def health():
    return "OK", 200


def start_web_server():
    web_app.run(host="0.0.0.0", port=PORT, use_reloader=False)


def keep_alive_loop():
    time.sleep(25)
    if not RENDER_EXTERNAL_URL:
        logger.warning("RENDER_EXTERNAL_URL set nahi hai.")
        return

    health_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/health"
    logger.info("Keep-alive loop active: %s", health_url)

    while True:
        try:
            requests.get(health_url, timeout=10)
        except Exception as exc:
            logger.warning("Ping error: %s", exc)
        time.sleep(480)  # Har 8 minute mein ping


# ================= KEYBOARDS =================
def main_reply_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["Get otp panel", "help"],
            ["₹140 free⚡😘"],
        ],
        resize_keyboard=True,
    )


def join_keyboard():
    return InlineKeyboardMarkup(
        [
            [{"text": FORCE_CHANNELS[0]["name"], "url": FORCE_CHANNELS[0]["url"]}],
            [{"text": FORCE_CHANNELS[1]["name"], "url": FORCE_CHANNELS[1]["url"]}],
            [{"text": "✅ I HAVE JOINED", "callback_data": "/claim"}],
        ]
    )


def claim_keyboard():
    return InlineKeyboardMarkup(
        [
            [{"text": "🔗 OTP PANEL 1", "url": "https://t.me/eraXotp_bot?start=ref_8429424734"}],
            [{"text": "🔗 OTP PANEL 2", "url": "https://t.me/PDOTPROBOT?start=ref_8429424734"}],
            [{"text": "🤖 Most wanted bot 🥶", "url": "https://t.me/HardXotp_Bot?start=ref_8429424734"}],
            [{"text": "🔗 OTP PANEL 4☠️", "url": "https://t.me/mahibhaiaurasmsbot?start=ref_8472943262"}],
            [{"text": "🔗 new otp panel☠️", "url": "https://t.me/PDOTPROBOT?start=ref_8429424734"}],
            [{"text": "💬 Contact Admin", "callback_data": "/contact"}],
        ]
    )


def contact_keyboard():
    return InlineKeyboardMarkup(
        [[{"text": "📩 Message Admin Now", "url": "https://t.me/FZXMOHIT1"}]]
    )


def free_140_keyboard():
    return InlineKeyboardMarkup(
        [[{"text": "📢 Join Channel", "url": FREE_140_CHANNEL_URL}]]
    )


# ================= HELPERS =================
async def send_tracked(target_message, user_id: int, text: str, **kwargs):
    sent = await target_message.reply_text(text, **kwargs)
    if sent:
        last_bot_messages[user_id] = (sent.chat_id, sent.message_id)
    return sent


async def delete_tracked(bot, user_id: int):
    tracked = last_bot_messages.pop(user_id, None)
    if not tracked:
        return
    try:
        await bot.delete_message(chat_id=tracked[0], message_id=tracked[1])
    except TelegramError:
        pass


async def check_force_join(bot, user_id: int) -> bool:
    allowed = {"member", "administrator", "creator"}
    for ch in FORCE_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if member.status not in allowed:
                return False
        except TelegramError:
            return False
    return True


async def is_maintenance_blocked(update: Update) -> bool:
    user = update.effective_user
    if maintenance_mode and (not user or user.id not in ADMIN_IDS):
        if update.message:
            await update.message.reply_text("🔧 Bot abhi maintenance mein hai. Thodi der baad try karein.")
        elif update.callback_query:
            await update.callback_query.answer("🔧 Maintenance chal raha hai!", show_alert=True)
        return True
    return False


async def show_join_menu(target_message, user_id: int):
    await send_tracked(
        target_message,
        user_id,
        "🔥 **WELCOME TO OTP PANELS BOT** 🔥\n\n"
        "📢 Pehle dono channels join karein aur phir **✅ I HAVE JOINED** dabayein.\n"
        "↩️ Pichhla message hata kar back aane ke liye `/cancel` bhejein.",
        reply_markup=join_keyboard(),
    )


async def show_main_menu(target_message, user_id: int):
    await send_tracked(
        target_message,
        user_id,
        "👑 **WELCOME TO MOHIT FZX** 👑\n\n"
        "👇 Niche se OTP Panel ya option chunein:\n"
        "👤 Admin: @FZXMOHIT1 | Back/Cancel: `/cancel`",
        reply_markup=claim_keyboard(),
    )
    await target_message.reply_text(
        "⚡ Quick Menu (`/cancel` se pichhla step cancel & back):",
        reply_markup=main_reply_keyboard(),
    )


async def show_help_menu(target_message, user_id: int):
    await send_tracked(
        target_message,
        user_id,
        "💬 **Help & Support**\n\n"
        "Koi bhi dikkat ho toh admin ko message karein: @FZXMOHIT1\n"
        "↩️ Back aane ke liye `/cancel` bhejein.",
        reply_markup=contact_keyboard(),
    )


async def show_140_loot(target_message, user_id: int):
    await send_tracked(
        target_message,
        user_id,
        f"140₹ free Bug loot here\n{FREE_140_CHANNEL_URL}\n\n"
        "↩️ Back aane ke liye `/cancel` bhejein.",
        reply_markup=free_140_keyboard(),
    )


# ================= COMMANDS =================
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_maintenance_blocked(update):
        return
    user = update.effective_user
    save_user(user)

    if await check_force_join(ctx.bot, user.id):
        await show_main_menu(update.message, user.id)
    else:
        await show_join_menu(update.message, user.id)


async def claim_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_maintenance_blocked(update):
        return
    user = update.effective_user
    save_user(user)

    if not await check_force_join(ctx.bot, user.id):
        await send_tracked(
            update.message,
            user.id,
            "⚠️ Pehle dono channels join karo, phir **✅ I HAVE JOINED** dabao!",
            reply_markup=join_keyboard(),
        )
        return
    await show_main_menu(update.message, user.id)


async def contact_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_maintenance_blocked(update):
        return
    user = update.effective_user
    save_user(user)
    await show_help_menu(update.message, user.id)


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    save_user(user)

    was_broadcast = uid in awaiting_broadcast
    if was_broadcast:
        awaiting_broadcast.remove(uid)

    await delete_tracked(ctx.bot, uid)

    if was_broadcast:
        await update.message.reply_text(
            "❌ Broadcast cancel ho gaya. Back to menu:",
            reply_markup=main_reply_keyboard(),
        )
        return

    if await check_force_join(ctx.bot, uid):
        await show_main_menu(update.message, uid)
    else:
        await show_join_menu(update.message, uid)


async def broadcast_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("⛔ Sirf Admin ke liye.")
        return

    awaiting_broadcast.add(uid)
    total = len(get_all_user_ids())
    await send_tracked(
        update.message,
        uid,
        f"📣 **Broadcast Mode ON** (`{total}` users)\n"
        "Text, Photo, Video ya Document bhejo.\n"
        "Cancel & Back ke liye `/cancel` bhejo.",
    )


async def maintenance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global maintenance_mode
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("⛔ Sirf Admin ke liye.")
        return
    maintenance_mode = not maintenance_mode
    await update.message.reply_text(
        f"🔧 Maintenance Mode: `{'ON' if maintenance_mode else 'OFF'}`"
    )


async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    total = len(get_all_user_ids())
    ping_ok = "Set ✅" if RENDER_EXTERNAL_URL else "Missing ⚠️"
    await update.message.reply_text(
        f"📊 **Stats**\nUsers: `{total}`\nSelf-Ping URL: `{ping_ok}`"
    )


# ================= CALLBACKS & MESSAGES =================
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_maintenance_blocked(update):
        return
    query = update.callback_query
    user = query.from_user
    save_user(user)

    if query.data == "/claim":
        if not await check_force_join(ctx.bot, user.id):
            await query.answer("❌ Pehle dono channels join karo!", show_alert=True)
            return
        await query.answer("✅ Verified!")
        await show_main_menu(query.message, user.id)
    elif query.data == "/contact":
        await query.answer()
        await show_help_menu(query.message, user.id)


async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_maintenance_blocked(update):
        return
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    save_user(user)

    if user.id in ADMIN_IDS and user.id in awaiting_broadcast:
        awaiting_broadcast.remove(user.id)
        user_ids = get_all_user_ids()
        sent = 0
        failed = 0
        status = await msg.reply_text(f"🚀 Sending to `{len(user_ids)}` users...")

        for uid in user_ids:
            try:
                await ctx.bot.copy_message(
                    chat_id=uid,
                    from_chat_id=msg.chat_id,
                    message_id=msg.message_id,
                )
                sent += 1
                await asyncio.sleep(0.03)
            except (Forbidden, TelegramError):
                failed += 1

        await status.edit_text(f"✅ Done! Sent: `{sent}` | Failed: `{failed}`")
        return

    txt = (msg.text or "").strip()
    if txt == "Get otp panel":
        if not await check_force_join(ctx.bot, user.id):
            await show_join_menu(msg, user.id)
            return
        await show_main_menu(msg, user.id)
    elif txt.lower() == "help":
        await show_help_menu(msg, user.id)
    elif txt == "₹140 free⚡😘":
        await show_140_loot(msg, user.id)
    else:
        await send_tracked(
            msg,
            user.id,
            "👇 Buttons use karein: `Get otp panel`, `help`, `₹140 free⚡😘`\n"
            "↩️ Back/Cancel ke liye `/cancel` bhejein.",
            reply_markup=main_reply_keyboard(),
        )


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Error: %s", ctx.error)


# ================= MAIN =================
def main():
    init_db()
    threading.Thread(target=start_web_server, daemon=True).start()
    threading.Thread(target=keep_alive_loop, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("claim", claim_cmd))
    app.add_handler(CommandHandler("contact", contact_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("maintenance", maintenance_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    logger.info("Bot running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
    
