import asyncio
import os
import sqlite3
import threading
from flask import Flask
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

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9079313944:AAFXcg-quWi7ABouEQX8724Hiscr_wHLR9U")

FORCE_CHANNELS = [
    {"id": -1004383355739, "name": "📢 JOIN CHANNEL 1", "url": "https://t.me/+ILptMG11g3c0ZDg1"},
    {"id": -1004474453856, "name": "📢 JOIN CHANNEL 2", "url": "https://t.me/+UyBmDcQRKORlNGI1"},
]

# ₹140 free⚡😘 button ke liye channel link (zaroorat ho toh apna link yahan badal le)
FREE_140_CHANNEL_URL = os.environ.get("FREE_140_CHANNEL_URL", "https://t.me/+ILptMG11g3c0ZDg1")

ADMIN_IDS = {8429424734, 8472943262}
DB_PATH = "bot_data.db"
PORT = int(os.environ.get("PORT", "8080"))

awaiting_broadcast = set()


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


# ================= RENDER KEEP-ALIVE WEB SERVER =================
web_app = Flask(__name__)


@web_app.get("/")
def home():
    return "Bot is alive and running!", 200


@web_app.get("/health")
def health():
    return "OK", 200


def start_web_server():
    web_app.run(host="0.0.0.0", port=PORT, use_reloader=False)


# ================= KEYBOARDS & MENUS =================
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


async def send_join_prompt(target_message):
    await target_message.reply_text(
        "🔥 **WELCOME TO otp panels BOT** 🔥\n\n"
        "📢 Is bot ko use karne ke liye aapko hamare channels join karna zaroori hai!\n\n"
        "👇 Niche diye gaye buttons par click karke channels join karein:",
        parse_mode="Markdown",
        reply_markup=join_keyboard(),
    )


async def send_claim_menu(target_message):
    await target_message.reply_text(
        "👑 WELCOME TO MOHIT FZX 👑\n\n"
        "🔥 नमस्कार! आपका हमारे Telegram Account पर स्वागत है। ❤️\n\n"
        "🚀 यहाँ आपको मिलेंगे हमारे सभी जरूरी OTP Panel और Bot Links एक ही जगह पर।\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📲 नीचे दिए गए बटन पर क्लिक करें:\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👤 **Admin / Contact:** @FZXMOHIT1\n"
        "💬 Need help? Type /contact\n\n"
        "⚡ Fast Service | 24×7 Support\n"
        "💀 POWERED BY MOHIT™ 🚀",
        parse_mode="Markdown",
        reply_markup=claim_keyboard(),
    )
    await target_message.reply_text(
        "👇 Niche se option chunein:",
        reply_markup=main_reply_keyboard(),
    )


async def send_contact_menu(target_message):
    await target_message.reply_text(
        "👋 Hello Bhai! Are you trying to contact me? 🤔💬\n\n"
        "Agar koi dikkat ya query hai, toh seedha niche diye gaye button par click karke mujhe message karo! 🚀🔥\n\n"
        "👤 **Official Admin:** @FZXMOHIT1",
        parse_mode="Markdown",
        reply_markup=contact_keyboard(),
    )


async def send_free_140_loot(target_message):
    await target_message.reply_text(
        f"140₹ free Bug loot here\n{FREE_140_CHANNEL_URL}",
        reply_markup=free_140_keyboard(),
    )


# ================= COMMANDS =================
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    joined = await check_force_join(ctx.bot, user.id)
    if joined:
        await send_claim_menu(update.message)
    else:
        await send_join_prompt(update.message)


async def claim_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    joined = await check_force_join(ctx.bot, user.id)
    if not joined:
        await update.message.reply_text(
            "⚠️ Pehle **dono channels** join karo, phir **✅ I HAVE JOINED** dabao!",
            parse_mode="Markdown",
            reply_markup=join_keyboard(),
        )
        return
    await send_claim_menu(update.message)


async def contact_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    await send_contact_menu(update.message)


async def broadcast_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("⛔ Yeh command sirf Admin ke liye hai.")
        return

    awaiting_broadcast.add(uid)
    total_users = len(get_all_user_ids())
    await update.message.reply_text(
        f"📣 **Broadcast Mode ON** (Total Users: `{total_users}`)\n\n"
        "Ab jo bhi bhejoge—**Text, Photo, Video, ya Document/File**—"
        "woh sab users ko broadcast ho jayega.\n\n"
        "Cancel karne ke liye `/cancel` bhejo.",
        parse_mode="Markdown",
    )


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in awaiting_broadcast:
        awaiting_broadcast.remove(uid)
        await update.message.reply_text("❌ Broadcast mode cancel kar diya gaya.")
    else:
        await update.message.reply_text("Koi active process nahi hai.")


async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    total = len(get_all_user_ids())
    await update.message.reply_text(f"📊 **Total Registered Users:** `{total}`", parse_mode="Markdown")


# ================= CALLBACKS =================
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    save_user(user)
    data = query.data

    if data == "/claim":
        joined = await check_force_join(ctx.bot, user.id)
        if not joined:
            await query.answer(
                "❌ Abhi dono channels join nahi hue hain! Pehle dono join karo.",
                show_alert=True,
            )
            return
        await query.answer("✅ Verification complete!")
        await send_claim_menu(query.message)

    elif data == "/contact":
        await query.answer()
        await send_contact_menu(query.message)


# ================= REPLY BUTTONS, BROADCAST & USER TRACKING =================
async def handle_messages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    save_user(user)

    # 1. Agar admin broadcast mode mein hai
    if user.id in ADMIN_IDS and user.id in awaiting_broadcast:
        awaiting_broadcast.remove(user.id)
        user_ids = get_all_user_ids()
        sent = 0
        failed = 0

        status_msg = await msg.reply_text(f"🚀 Broadcasting to `{len(user_ids)}` users...", parse_mode="Markdown")

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
            f"⚠️ Failed/Blocked: `{failed}`",
            parse_mode="Markdown",
        )
        return

    # 2. Reply keyboard buttons handle karna
    txt = (msg.text or "").strip()
    if txt == "Get otp panel":
        joined = await check_force_join(ctx.bot, user.id)
        if not joined:
            await send_join_prompt(msg)
            return
        await send_claim_menu(msg)
    elif txt.lower() == "help":
        await send_contact_menu(msg)
    elif txt == "₹140 free⚡😘":
        await send_free_140_loot(msg)


# ================= MAIN =================
def main():
    init_db()
    threading.Thread(target=start_web_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("claim", claim_cmd))
    app.add_handler(CommandHandler("contact", contact_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_messages))

    print("Bot chal raha hai...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
        
