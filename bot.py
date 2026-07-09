import os
import re
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from database import Database
from ivasms_fetcher import IVASMSFetcher

load_dotenv()

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
IVASMS_EMAIL = os.getenv('IVASMS_EMAIL', 'your_email@gmail.com')
IVASMS_PASSWORD = os.getenv('IVASMS_PASSWORD', 'your_password')
ADMIN_IDS = os.getenv('ADMIN_IDS', '').split(',')
# ===============================================

db = Database()
sms_fetcher = None  # Will be initialized later

# ==================== KEYBOARDS ====================
def user_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Get Numbers", callback_data="get_numbers")],
        [InlineKeyboardButton("📊 My History", callback_data="my_history")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help_menu")],
    ])

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Get Numbers", callback_data="get_numbers")],
        [InlineKeyboardButton("📊 My History", callback_data="my_history")],
        [InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help_menu")],
    ])

def admin_panel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📋 Recent Used", callback_data="admin_recent")],
        [InlineKeyboardButton("📁 Upload Numbers", callback_data="admin_upload")],
        [InlineKeyboardButton("🔢 Set Limit", callback_data="admin_set_limit")],
        [InlineKeyboardButton("⏳ Set Cooldown", callback_data="admin_set_cooldown")],
        [InlineKeyboardButton("🔄 Set Interval", callback_data="admin_set_interval")],
        [InlineKeyboardButton("▶️ Start Fetcher", callback_data="admin_start_fetcher")],
        [InlineKeyboardButton("⏹ Stop Fetcher", callback_data="admin_stop_fetcher")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_main")],
    ])

def is_admin(user_id):
    return str(user_id) in ADMIN_IDS

def get_keyboard(user_id):
    return admin_keyboard() if is_admin(user_id) else user_keyboard()

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    
    msg = f"""
🚀 <b>IVAS SMS Bot</b>

👋 Hello <b>{user.first_name}</b>!

📱 Get premium SMS numbers
⚡ Auto OTP detection & forward
🔄 Smart matching system

<b>Commands:</b>
📱 Get Numbers - Receive SMS numbers
📊 History - View used numbers
{'👑 Admin Panel - Manage bot' if is_admin(user_id) else ''}
"""
    if update.message:
        await update.message.reply_text(msg, reply_markup=get_keyboard(user_id), parse_mode='HTML')
    else:
        await update.callback_query.message.edit_text(msg, reply_markup=get_keyboard(user_id), parse_mode='HTML')

async def get_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(update.effective_user.id)
    
    cooldown_sec = int(db.get_setting('cooldown_seconds') or 5)
    on_cooldown, remaining = db.check_cooldown(user_id, cooldown_sec)
    
    if on_cooldown:
        await query.answer(f"⏳ Wait {remaining}s!", show_alert=True)
        return
    
    limit = int(db.get_setting('numbers_per_request') or 3)
    numbers = db.assign_numbers(user_id, limit)
    
    if not numbers:
        await query.answer("❌ No numbers left! Contact admin.", show_alert=True)
        return
    
    db.update_cooldown(user_id)
    
    msg = f"📱 <b>Your Numbers</b>\n\n"
    for i, (phone, country, service) in enumerate(numbers, 1):
        msg += f"<b>{i}.</b> <code>{phone}</code>\n"
        msg += f"    🌍 {country} | 📦 {service}\n\n"
    msg += f"⏳ Cooldown: {cooldown_sec}s\n📩 OTP will be auto-forwarded!"
    
    await query.message.reply_text(msg, parse_mode='HTML')
    await query.answer("✅ Done!")

async def my_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recent = db.get_recent_used(10)
    if not recent:
        await update.callback_query.answer("No history!", show_alert=True)
        return
    
    msg = "📋 <b>Recent Used</b>\n\n"
    for phone, otp, time in recent:
        msg += f"📞 <code>{phone}</code> | OTP: <code>{otp or 'N/A'}</code>\n"
    
    await update.callback_query.message.reply_text(msg, parse_mode='HTML')

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """
ℹ️ <b>Help</b>

<b>How to use:</b>
1️⃣ Click <b>Get Numbers</b>
2️⃣ Use number on platform
3️⃣ OTP auto-detected from ivasms
4️⃣ Forwarded to you instantly!

<b>Commands:</b>
/start - Main menu
/help - This help

📞 Contact admin for issues.
"""
    await update.callback_query.message.reply_text(msg, parse_mode='HTML')

# ==================== ADMIN ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("⛔ Access Denied!", show_alert=True)
        return
    
    status = "🟢 Running" if (sms_fetcher and sms_fetcher.running) else "🔴 Stopped"
    msg = f"""
👑 <b>Admin Panel</b>

📡 Fetcher: {status}
📱 Total: <b>{db.get_stats()['total']}</b>
✅ Unused: <b>{db.get_stats()['unused']}</b>

Select option:
"""
    await update.callback_query.message.reply_text(msg, reply_markup=admin_panel_kb(), parse_mode='HTML')

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    s = db.get_stats()
    msg = f"""
📊 <b>Stats</b>
📱 Total: <b>{s['total']}</b>
✅ Unused: <b>{s['unused']}</b>
🔄 Assigned: <b>{s['assigned']}</b>
❌ Used: <b>{s['used']}</b>
⚙️ Limit: <b>{db.get_setting('numbers_per_request')}</b>
⏳ Cooldown: <b>{db.get_setting('cooldown_seconds')}s</b>
🔄 Interval: <b>{db.get_setting('fetch_interval')}s</b>
"""
    await update.callback_query.message.reply_text(msg, parse_mode='HTML')

async def admin_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    recent = db.get_recent_used(10)
    if not recent:
        await update.callback_query.answer("No data!", show_alert=True)
        return
    msg = "📋 <b>Recent Used</b>\n\n"
    for phone, otp, time in recent:
        t = str(time)[:19] if time else 'N/A'
        msg += f"📞 <code>{phone}</code> → <code>{otp or 'N/A'}</code>\n"
    await update.callback_query.message.reply_text(msg, parse_mode='HTML')

async def admin_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = """
📁 <b>Upload Numbers</b>
Send .txt file or paste numbers:
<code>phone,country,service</code>
Example:
<code>8801338680398,BANGLADESH,GoChat
88017XXXXXXXX,BANGLADESH,WhatsApp</code>
"""
    await update.callback_query.message.reply_text(msg, parse_mode='HTML')
    context.user_data['awaiting_upload'] = True

async def admin_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    cur = db.get_setting('numbers_per_request') or '3'
    await update.callback_query.message.reply_text(f"🔢 Current limit: <b>{cur}</b>\nSend new value (1-10):", parse_mode='HTML')
    context.user_data['awaiting_limit'] = True

async def admin_set_cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    cur = db.get_setting('cooldown_seconds') or '5'
    await update.callback_query.message.reply_text(f"⏳ Current cooldown: <b>{cur}s</b>\nSend new value (min 5):", parse_mode='HTML')
    context.user_data['awaiting_cooldown'] = True

async def admin_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    cur = db.get_setting('fetch_interval') or '10'
    await update.callback_query.message.reply_text(f"🔄 Current interval: <b>{cur}s</b>\nSend new value (min 5):", parse_mode='HTML')
    context.user_data['awaiting_interval'] = True

async def admin_start_fetcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    global sms_fetcher
    if sms_fetcher and sms_fetcher.running:
        await update.callback_query.answer("Already running!", show_alert=True)
        return
    
    sms_fetcher = IVASMSFetcher(IVASMS_EMAIL, IVASMS_PASSWORD, db, context.application)
    if sms_fetcher.start():
        await update.callback_query.message.reply_text("✅ <b>SMS Fetcher Started!</b>", parse_mode='HTML')
    else:
        await update.callback_query.message.reply_text("❌ Failed to start! Check logs.", parse_mode='HTML')

async def admin_stop_fetcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    global sms_fetcher
    if sms_fetcher:
        sms_fetcher.stop()
        sms_fetcher = None
        await update.callback_query.message.reply_text("⏹ <b>SMS Fetcher Stopped!</b>", parse_mode='HTML')
    else:
        await update.callback_query.answer("Not running!", show_alert=True)

# ==================== MESSAGE HANDLER ====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ''
    user_id = str(update.effective_user.id)
    
    if context.user_data.get('awaiting_upload'):
        numbers = text.strip().split('\n')
        count = db.add_numbers(numbers)
        await update.message.reply_text(f"✅ <b>{count}</b> numbers added!")
        context.user_data['awaiting_upload'] = False
        return
    
    if context.user_data.get('awaiting_limit'):
        if text.isdigit() and 1 <= int(text) <= 10:
            db.update_setting('numbers_per_request', text)
            await update.message.reply_text(f"✅ Limit set to <b>{text}</b>", parse_mode='HTML')
        else:
            await update.message.reply_text("❌ Enter 1-10")
        context.user_data['awaiting_limit'] = False
        return
    
    if context.user_data.get('awaiting_cooldown'):
        if text.isdigit() and int(text) >= 5:
            db.update_setting('cooldown_seconds', text)
            await update.message.reply_text(f"✅ Cooldown set to <b>{text}s</b>", parse_mode='HTML')
        else:
            await update.message.reply_text("❌ Minimum 5 seconds")
        context.user_data['awaiting_cooldown'] = False
        return
    
    if context.user_data.get('awaiting_interval'):
        if text.isdigit() and int(text) >= 5:
            db.update_setting('fetch_interval', text)
            await update.message.reply_text(f"✅ Interval set to <b>{text}s</b>", parse_mode='HTML')
        else:
            await update.message.reply_text("❌ Minimum 5 seconds")
        context.user_data['awaiting_interval'] = False
        return

async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_upload'): return
    file = await update.message.document.get_file()
    content = await file.download_as_bytearray()
    numbers = content.decode('utf-8').strip().split('\n')
    count = db.add_numbers(numbers)
    await update.message.reply_text(f"✅ <b>{count}</b> numbers added!")
    context.user_data['awaiting_upload'] = False

# ==================== CALLBACK ROUTER ====================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    handlers = {
        'get_numbers': get_numbers,
        'my_history': my_history,
        'help_menu': help_menu,
        'admin_panel': admin_panel,
        'admin_stats': admin_stats,
        'admin_recent': admin_recent,
        'admin_upload': admin_upload,
        'admin_set_limit': admin_set_limit,
        'admin_set_cooldown': admin_set_cooldown,
        'admin_set_interval': admin_set_interval,
        'admin_start_fetcher': admin_start_fetcher,
        'admin_stop_fetcher': admin_stop_fetcher,
        'back_main': start,
    }
    if data in handlers:
        await handlers[data](update, context)
    else:
        await query.answer("Coming soon...")

# ==================== MAIN ====================
async def post_init(app: Application):
    commands = [
        BotCommand("start", "Start bot"),
        BotCommand("help", "Help menu"),
        BotCommand("admin", "Admin panel"),
    ]
    await app.bot.set_my_commands(commands)
    print("✅ Bot commands set!")

def main():
    print("=" * 60)
    print("🚀 IVAS SMS BOT WITH AUTO FETCHER")
    print("=" * 60)
    
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN':
        print("❌ Set BOT_TOKEN in .env file!")
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.post_init = post_init
    
    # Command handlers
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_menu))
    app.add_handler(CommandHandler('admin', admin_panel))
    
    # Callback handler
    app.add_handler(CallbackQueryHandler(callback_router))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    
    print("✅ Bot is running!")
    print("=" * 60)
    print("👑 Admins:", ADMIN_IDS)
    print("📧 Email:", IVASMS_EMAIL)
    print("=" * 60)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()