import asyncio
import logging
import os
import sqlite3
import threading
import time
from flask import Flask
import requests
from telegram import (
    InlineKeyboardButton,
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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("MohitBot")

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

# Active states & last bot message tracking for /cancel + back
awaiting_broadcast = set()
maintenance_mode = False
last_bot_messages = {}  # user_id -> {"chat_id": int, "message_id": int}


# ================= DATABASE =================
def get_db():
    con = sqlite3.connect(DB_PATH)
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


# ================= WEB SERVER & ANTI-SLEEP PINGER =================
web_app = Flask(__name__)


@web_app.get("/")
def home():
    return "Bot is alive!", 200


@web_app.get("/health")
def health():
    return "OK", 200


def start_web_server():
    web_app.run(host="0.0.0.0", port=PORT, use_reloader=False)


def keep_alive_loop():
    time.sleep(30)
    if not RENDER_EXTERNAL_URL:
        logger.warning("RENDER_EXTERNAL_URL set nahi hai; self-ping skip ho raha hai.")
        return

    base_url = RENDER_EXTERNAL_URL.rstrip("/")
    health_url = f"{base_url}/health"
    logger.info("Anti-sleep self-ping loop start: %s", health_url)

    while True:
        try:
            res = requests.get(health_url, timeout=15)
            logger.info("Self-ping status: %s", res.status_code)
        except Exception as exc:
            logger.warning("Self-ping failed: %s", exc)
        time.sleep(600)  # Har 10 minute mein ping


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


# ================= HELPERS & /CANCEL + BACK =================
async def send_tracked_message(target_message, user_id: int, text: str, **kwargs):
    sent = await target_message.reply_text(text, **kwargs)
    if sent:
        last_bot_messages[user_id] = {
            "chat_id": sent.chat_id,
            "message_id": sent.message_id,
        }
    return sent


async def delete_last_bot_message(bot, user_id: int):
    info = last_bot_messages.pop(user_id, None)
    if not info:
        return
    try:
        await bot.delete_message(chat_id=info["chat_id"], message_id=info["message_id"])
    except TelegramError:
        pass


async def check_force_join(bot, user_id: int) -> bool:
    allowed_statuses = {"member", "administrator", "creator"}
    for ch in FORCE_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if member.status not in allowed_statuses:
                return False
        except TelegramError:
            return False
    return True


async def is_blocked_by_maintenance(update: Update) -> bool:
    user = update.effective_user
    if maintenance_mode and (not user or user.id not in ADMIN_IDS):
        if update.message:
            await update.message.reply_text(
                "🔧 **Maintenance Mode ON**\n\n"
                "Abhi bot ya panels par update chal raha hai. Thodi der baad try karein!"
            )
        elif update.callback_query:
            await update.callback_query.answer(
                "🔧 Abhi maintenance chal raha hai, thodi der baad try karein!",
                show_alert=True,
            )
        return True
    return False


async def send_join_prompt(target_message, user_id: int):
    await send_tracked_message(
        target_message,
        user_id,
        "🔥 **WELCOME TO otp panels BOT** 🔥\n\n"
        "📢 Is bot ko use karne ke liye aapko hamare channels join karna zaroori hai!\n\n"
        "👇 Niche diye gaye buttons par click karke channels join karein:\n"
        "_(Pichhla step cancel karke back aane ke liye `/cancel` bhejein)_",
        reply_markup=join_keyboard(),
    )


async def send_claim_menu(target_message, user_id: int):
    await send_tracked_message(
        target_message,
        user_id,
        "👑 WELCOME TO MOHIT FZX 👑\n\n"
        "🔥 नमस्कार! आपका हमारे Telegram Account पर स्वागत है। ❤️\n\n"
        "🚀 यहाँ आपको मिलेंगे हमारे सभी जरूरी OTP Panel और Bot Links एक ही जगह पर।\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📲 नीचे दिए गए बटन पर क्लिक करें:\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👤 **Admin / Contact:** @FZXMOHIT1\n"
        "💬 Need help? Type /contact | Back/Cancel: /cancel\n\n"
        "⚡ Fast Service | 24×7 Support\n"
        "💀 POWERED BY MOHIT™ 🚀",
        reply_markup=claim_keyboard(),
    )
    await target_message.reply_text(
        "👇 Niche se option chunein (Cancel/Back ke liye `/cancel`):",
        reply_markup=main_reply_keyboard(),
    )


async def send_contact_menu(target_message, user_id: int):
    await send_tracked_message(
        target_message,
        user_id,
        "👋 Hello Bhai! Are you trying to contact me? 🤔💬\n\n"
        "Agar koi dikkat ya query hai, toh seedha niche diye gaye button par click karke mujhe message karo! 🚀🔥\n\n"
        "👤 **Official Admin:** @FZXMOHIT1\n"
        "↩️ Pichhla step cancel karke back aane ke liye `/cancel` bhejein.",
        reply_markup=contact_keyboard(),
    )


async def send_free_140_loot(target_message, user_id: int):
    await send_tracked_message(
        target_message,
        user_id,
        f"140₹ free Bug loot here\n{FREE_140_CHANNEL_URL}\n\n"
        "↩️ Back/Cancel ke liye `/cancel` bhejein.",
        reply_markup=free_140_keyboard(),
    )


# ================= COMMANDS =================
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_blocked_by_maintenance(update):
        return
    user = update.effective_user
    save_user(user)

    if await check_force_join(ctx.bot, user.id):
        await send_claim_menu(update.message, user.id)
    else:
        await send_join_prompt(update.message, user.id)


async def claim_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_blocked_by_maintenance(update):
        return
    user = update.effective_user
    save_user(user)

    if not await check_force_join(ctx.bot, user.id):
        await send_tracked_message(
            update.message,
            user.id,
            "⚠️ Pehle **dono channels** join karo, phir **✅ I HAVE JOINED** dabao!",
            reply_markup=join_keyboard(),
        )
        return
    await send_claim_menu(update.message, user.id)


async def contact_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_blocked_by_maintenance(update):
        return
    user = update.effective_user
    save_user(user)
    await send_contact_menu(update.message, user.id)


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    save_user(user)

    was_broadcasting = uid in awaiting_broadcast
    if was_broadcasting:
        awaiting_broadcast.remove(uid)

    await delete_last_bot_message(ctx.bot, uid)

    if was_broadcasting:
        await update.message.reply_text(
            "❌ Broadcast mode cancel kar diya gaya. Back to main menu:",
            reply_markup=main_reply_keyboard(),
        )
        return

    if await check_force_join(ctx.bot, uid):
        await send_claim_menu(update.message, uid)
    else:
        await send_join_prompt(update.message, uid)


async def broadcast_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("⛔ Yeh command sirf Admin ke liye hai.")
        return

    awaiting_broadcast.add(uid)
    total_users = len(get_all_user_ids())
    await send_tracked_message(
        update.message,
        uid,
        f"📣 **Broadcast Mode ON** (Total Users: `{total_users}`)\n\n"
        "Ab jo bhi bhejoge—**Text, Photo, Video, ya Document/File**—"
        "woh sab users ko broadcast ho jayega.\n\n"
        "Cancel & Back ke liye `/cancel` bhejo.",
    )


async def maintenance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global maintenance_mode
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("⛔ Yeh command sirf Admin ke liye hai.")
        return

    maintenance_mode = not maintenance_mode
    status_text = "ON 🔧" if maintenance_mode else "OFF ✅"
    await update.message.reply_text(f"🛠️ **Maintenance Mode:** `{status_text}`")


async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    total = len(get_all_user_ids())
    ping_status = "Configured ✅" if RENDER_EXTERNAL_URL else "Not Set ⚠️"
    maint_status = "ON 🔧" if maintenance_mode else "OFF ✅"
    await update.message.reply_text(
        f"📊 **Bot Status & Stats**\n\n"
        f"👥 Total Users: `{total}`\n"
        f"⚡ Self-Ping URL: `{ping_status}`\n"
        f"🛠️ Maintenance: `{maint_status}`"
    )


# ================= CALLBACKS =================
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_blocked_by_maintenance(update):
        return
    query = update.callback_query
    user = query.from_user
    save_user(user)
    data = query.data

    if data == "/claim":
        if not await check_force_join(ctx.bot, user.id):
            await query.answer(
                "❌ Abhi dono channels join nahi hue hain! Pehle dono join karo.",
                show_alert=True,
            )
            return
        await query.answer("✅ Verification complete!")
        await send_claim_menu(query.message, user.id)

    elif data == "/contact":
        await query.answer()
        await send_contact_menu(query.message, user.id)


# ================= MESSAGE HANDLER =================
async def handle_messages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await is_blocked_by_maintenance(update):
        return
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    save_user(user)

    # 1. Admin Broadcast Mode
    if user.id in ADMIN_IDS and user.id in awaiting_broadcast:
        awaiting_broadcast.remove(user.id)
        user_ids = get_all_user_ids()
        sent = 0
        failed = 0

        status_msg = await msg.reply_text(f"🚀 Broadcasting to `{len(user_ids)}` users...")

        for target_id in user_ids:
            try:
                await ctx.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=msg.chat_id,
                    message_id=msg.message_id,
                )
                sent += 1
                await asyncio.sleep(0.05)
            except (Forbidden, TelegramError):
                failed += 1

        await status_msg.edit_text(
            f"✅ **Broadcast Complete!**\n\n"
            f"📤 Sent: `{sent}`\n"
            f"⚠️ Failed/Blocked: `{failed}`"
        )
        return

    # 2. Reply Keyboard Buttons
    txt = (msg.text or "").strip()
    if txt == "Get otp panel":
        if not await check_force_join(ctx.bot, user.id):
            await send_join_prompt(msg, user.id)
            return
        await send_claim_menu(msg, user.id)
    elif txt.lower() == "help":
        await send_contact_menu(msg, user.id)
    elif txt == "₹140 free⚡😘":
        await send_free_140_loot(msg, user.id)
    else:
        await send_tracked_message(
            msg,
            user.id,
            "👇 Kripya niche diye gaye buttons ka use karein:\n"
            "• `Get otp panel`\n"
            "• `help`\n"
            "• `₹140 free⚡😘`\n\n"
            "Pichhla step cancel karke back aane ke liye `/cancel` bhejein.",
            reply_markup=main_reply_keyboard(),
        )


# ================= GLOBAL ERROR HANDLER =================
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error: %s", ctx.error)


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
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))
    app.add_error_handler(error_handler)

    logger.info("Bot start ho raha hai...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
