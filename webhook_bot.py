import os
import sqlite3
import asyncio
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# 🔧 CONFIG (Load from environment variables - SECURE)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@comejoin1a")
DB_FILE = "lottery_numbers.db"
CONFIG_FILE = "channel_ids.json"

db_lock = asyncio.Lock()
SHOW_RECENT = 6
BAR_LENGTH = 40

# 📦 Database Initialization
def init_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Main numbers table
    cursor.execute("""CREATE TABLE IF NOT EXISTS numbers (
        number INTEGER PRIMARY KEY,
        taken INTEGER DEFAULT 0,
        claimed_by TEXT,
        claimed_at TEXT
    )""")
    
    # Claims log for recent activity
    cursor.execute("""CREATE TABLE IF NOT EXISTS claims_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number INTEGER,
        claimed_by TEXT,
        claimed_at TEXT
    )""")
    
    # Fill 1-5000 if empty
    cursor.execute("SELECT COUNT(*) FROM numbers")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("INSERT INTO numbers (number) VALUES (?)", [(i,) for i in range(1, 5001)])
    
    # Clean old logs (keep last 50)
    cursor.execute("DELETE FROM claims_log WHERE id <= (SELECT MAX(id) - 50 FROM claims_log)")
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

# 📖 Config Helpers (Load/Save message IDs)
def load_ids():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"board": None, "grids": {}}

def save_ids(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f)

# 🔄 Edit existing message OR send new one (guaranteed ID tracking)
async def edit_or_send(app, msg_id, chat_id, text):
    if msg_id:
        try:
            await app.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text)
            return msg_id
        except TelegramError:
            pass  # Fall through to send new if edit fails
    
    msg = await app.bot.send_message(chat_id=chat_id, text=text)
    return msg.message_id

# 🎛️ SYNC FULL: Send all 11 messages on startup
async def sync_channel_full(app):
    cfg = load_ids()
    
    async with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM numbers WHERE taken=1")
        claimed = c.fetchone()[0]
        c.execute("SELECT number, claimed_by FROM claims_log ORDER BY id DESC LIMIT ?", (SHOW_RECENT,))
        recent = c.fetchall()
        c.execute("SELECT number FROM numbers WHERE taken=1")
        taken = set(r[0] for r in c.fetchall())
        conn.close()

    # Build Live Board
    pct = round((claimed / 5000) * 100, 1)
    filled = int((claimed / 5000) * BAR_LENGTH)
    bar = "🟢" * filled + "⚪️" * (BAR_LENGTH - filled)
    
    board_text = (
        f"🎫 LIVE LOTTERY BOARD\n"
        f"📊 Claimed: {claimed}/5000 ({pct}%)\n"
        f"🟢 Available: {5000 - claimed}\n{bar}\n\n"
        f"🕒 Recent Claims:\n"
    )
    for num, user in recent:
        board_text += f"• #{num} by {user}\n"
    if not recent:
        board_text += "• No claims yet\n"

    # Update Board
    cfg["board"] = await edit_or_send(app, cfg.get("board"), CHANNEL_ID, board_text)
    await asyncio.sleep(0.4)
    save_ids(cfg)

    # Update 10 Grid Blocks (500 numbers each)
    for start in range(1, 5001, 500):
        end = start + 499
        key = f"{start}-{end}"
        items = [f"{n} {'✅' if n in taken else '❌'}" for n in range(start, end + 1)]
        grid_text = f"📋 Numbers {start}-{end}:\n" + " ".join(items)
        
        cfg["grids"][key] = await edit_or_send(app, cfg["grids"].get(key), CHANNEL_ID, grid_text)
        save_ids(cfg)  # Save immediately after each block
        await asyncio.sleep(0.4)  # Avoid Telegram rate limits
    
    print("✅ Full channel sync complete")

# ⚡ FAST UPDATE: Only edit Board + affected 500-block after a claim
async def sync_channel_fast(app, claimed_num):
    cfg = load_ids()
    
    async with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM numbers WHERE taken=1")
        claimed = c.fetchone()[0]
        c.execute("SELECT number, claimed_by FROM claims_log ORDER BY id DESC LIMIT ?", (SHOW_RECENT,))
        recent = c.fetchall()
        c.execute("SELECT number FROM numbers WHERE taken=1")
        taken = set(r[0] for r in c.fetchall())
        conn.close()

    # Update Board
    pct = round((claimed / 5000) * 100, 1)
    filled = int((claimed / 5000) * BAR_LENGTH)
    bar = "🟢" * filled + "⚪️" * (BAR_LENGTH - filled)
    
    board_text = (
        f"🎫 LIVE LOTTERY BOARD\n"
        f"📊 Claimed: {claimed}/5000 ({pct}%)\n"
        f"🟢 Available: {5000 - claimed}\n{bar}\n\n"
        f"🕒 Recent Claims:\n"
    )
    for num, user in recent:
        board_text += f"• #{num} by {user}\n"
    if not recent:
        board_text += "• No claims yet\n"

    cfg["board"] = await edit_or_send(app, cfg["board"], CHANNEL_ID, board_text)
    save_ids(cfg)
    await asyncio.sleep(0.4)

    # Update ONLY the affected 500-block
    start = ((claimed_num - 1) // 500) * 500 + 1
    end = start + 499
    key = f"{start}-{end}"
    items = [f"{n} {'✅' if n in taken else '❌'}" for n in range(start, end + 1)]
    grid_text = f"📋 Numbers {start}-{end}:\n" + " ".join(items)
    
    cfg["grids"][key] = await edit_or_send(app, cfg["grids"].get(key), CHANNEL_ID, grid_text)
    save_ids(cfg)
    print(f"✅ Fast update: Board & {key}")

# 🎬 Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎫 Welcome to the Lottery Bot!\n\n"
        "• `/claim <1-5000>` → Take a number\n"
        "• `/get <1-5000>` → Check status\n"
        "• `/stats` → Quick summary\n\n"
        "📌 All numbers are auto-posted & updated in the channel!"
    )

async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/claim 55`", parse_mode="Markdown")
        return
    try:
        num = int(context.args[0])
        if not (1 <= num <= 5000):
            await update.message.reply_text("❌ Number must be between 1 and 5000.")
            return

        username = update.message.from_user.username or f"User{update.message.from_user.id}"

        # 🔒 Atomic claim with lock
        async with db_lock:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE numbers SET taken=1, claimed_by=?, claimed_at=datetime('now') WHERE number=? AND taken=0",
                (username, num)
            )
            if cursor.rowcount == 0:
                cursor.execute("SELECT claimed_by FROM numbers WHERE number=?", (num,))
                row = cursor.fetchone()
                claimed_by = row[0] if row and row[0] else "unknown"
                await update.message.reply_text(f"❌ Number {num} is already taken by {claimed_by}!")
                conn.close()
                return
            # Log the claim
            cursor.execute(
                "INSERT INTO claims_log (number, claimed_by, claimed_at) VALUES (?, ?, datetime('now'))",
                (num, username)
            )
            conn.commit()
            conn.close()

        # Auto-update channel
        await sync_channel_fast(context.application, num)
        await update.message.reply_text(f"✅ You successfully claimed number **{num}**!", parse_mode="Markdown")

    except ValueError:
        await update.message.reply_text("❌ Please send a valid number.")

async def get_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/get 55`", parse_mode="Markdown")
        return
    try:
        num = int(context.args[0])
        if not (1 <= num <= 5000):
            await update.message.reply_text("❌ Number must be between 1 and 5000.")
            return
        
        async with db_lock:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("SELECT taken, claimed_by FROM numbers WHERE number=?", (num,))
            row = cursor.fetchone()
            conn.close()

        if row and row[0] == 1:
            await update.message.reply_text(f"Number **{num}**: ✅ Taken by {row[1] or 'unknown'}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"Number **{num}**: ❌ Available", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Please send a valid number.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with db_lock:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM numbers WHERE taken=1")
        taken = cursor.fetchone()[0]
        conn.close()
    await update.message.reply_text(
        f"📊 Numbers taken: **{taken}/5000**\n🟢 Still available: **{5000 - taken}**",
        parse_mode="Markdown"
    )

# ✅ Webhook startup handler
async def on_startup(app: Application):
    print("🔄 Initializing channel messages...")
    await sync_channel_full(app)
    print("✅ Bot ready for webhooks")

# 🚀 Main entry point for Render/gunicorn
def create_app():
    init_database()
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("claim", claim))
    app.add_handler(CommandHandler("get", get_status))
    app.add_handler(CommandHandler("stats", stats))
    
    return app

# For local testing with polling (optional)
if __name__ == "__main__" and os.getenv("RENDER", "false") == "false":
    init_database()
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("claim", claim))
    app.add_handler(CommandHandler("get", get_status))
    app.add_handler(CommandHandler("stats", stats))
    print("🤖 Running in polling mode (local testing)")
    app.run_polling()

# WSGI app export for Render/gunicorn (REQUIRED)
app_for_render = create_app()