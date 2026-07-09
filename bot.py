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
OTP_GROUP_ID = os.getenv('OTP_GROUP_ID', '')
# ===============================================

db = Database()
sms_fetcher = None

if OTP_GROUP_ID:
    db.update_setting('otp_group_id', OTP_GROUP_ID)

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
    status = "🟢 Running" if (sms_fetcher and sms_fetcher.running) else "🔴 Stopped"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📋 Recent Used", callback_data="admin_recent")],
        [InlineKeyboardButton("📁 Upload Numbers", callback_data="admin_upload")],
        [InlineKeyboardButton("🔢 Set Limit", callback_data="admin_set_limit")],
        [InlineKeyboardButton("⏳ Set Cooldown", callback_data="admin_set_cooldown")],
        [InlineKeyboardButton("🔄 Set Interval", callback_data="admin_set_interval")],
        [InlineKeyboardButton("👥 Set Group", callback_data="admin_set_group")],
        [InlineKeyboardButton(f"▶️ Start Fetcher ({status})", callback_data="admin_start_fetcher")],
        [InlineKeyboardButton("⏹ Stop Fetcher", callback_data="admin_stop_fetcher")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_main")],
    ])

def is_admin(user_id):
    return str(user_id) in ADMIN_IDS

def get_keyboard(user_id):
    return admin_keyboard() if is_admin(user_id) else user_keyboard()

# ==================== BOT HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    msg = f"""
🚀 <b>IVAS SMS Bot</b>
👋 Hello <b>{user.first_name}</b>!

📱 Get premium SMS numbers
📩 OTP sent to group
🤖 Bot auto-matches & forwards to you
"""
    kb = get_keyboard(user_id)
    if update.message:
        await update.message.reply_text(msg, reply_markup=kb, parse_mode='HTML')
    else:
        await update.callback_query.message.edit_text(msg, reply_markup=kb, parse_mode='HTML')

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
        await query.answer("❌ No numbers left!", show_alert=True)
        return
    
    db.update_cooldown(user_id)
    
    msg = f"📱 <b>Your Numbers</b>\n\n"
    for i, (phone, country, service) in enumerate(numbers, 1):
        msg += f"<b>{i}.</b> <code>{phone}</code> | {service}\n"
    msg += f"\n⏳ Cooldown: {cooldown_sec}s\n📩 OTP will come in group & auto-forward!"
    
    await query.message.reply_text(msg, parse_mode='HTML')
    await query.answer("✅ Done!")

async def my_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recent = db.get_recent_used(10)
    if not recent:
        await update.callback_query.answer("No history!", show_alert=True)
        return
    msg = "📋 <b>Recent</b>\n\n"
    for phone, otp, time in recent:
        msg += f"📞 <code>{phone}</code> → <code>{otp or 'N/A'}</code>\n"
    await update.callback_query.message.reply_text(msg, parse_mode='HTML')

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = """
ℹ️ <b>Help</b>
1️⃣ Click Get Numbers
2️⃣ Use number on platform
3️⃣ OTP comes to group
4️⃣ Bot forwards to you
"""
    await update.callback_query.message.reply_text(msg, parse_mode='HTML')

# ==================== GROUP MONITOR ====================
async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    group_id = db.get_otp_group_id()
    
    if not group_id or chat_id != group_id:
        return
    
    text = update.message.text or update.message.caption or ""
    if not text:
        return
    
    print(f"[GROUP] {text[:100]}")
    
    phone_match = re.search(r'📞\s*<code>(\+?\d+)</code>', text)
    if not phone_match:
        phone_match = re.search(r'(\d{10,14})', text.replace(' ', ''))
    
    if not phone_match:
        return
    
    phone = phone_match.group(1).replace('+', '')
    
    otp_match = re.search(r'OTP:\s*<code>(\d+)</code>', text)
    if not otp_match:
        otp_match = re.search(r'(?:code|otp|pin)[:\s]*(\d{4,8})', text, re.IGNORECASE)
    
    if not otp_match:
        return
    
    otp = otp_match.group(1)
    assigned = db.get_assigned_numbers()
    
    for assigned_phone, user_id in assigned:
        clean_assigned = assigned_phone.replace('+', '').replace(' ', '')
        clean_phone = phone.replace('+', '').replace(' ', '')
        
        if len(clean_assigned) >= 8 and len(clean_phone) >= 8:
            if clean_assigned[-8:] == clean_phone[-8:] or clean_phone[-8:] == clean_assigned[-8:]:
                db.mark_used_with_otp(assigned_phone, otp)
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"📩 <b>OTP Received!</b>\n\n📞 <code>{assigned_phone}</code>\n🔑 <code>{otp}</code>",
                        parse_mode='HTML'
                    )
                    print(f"[MATCH] {assigned_phone} -> {otp} to {user_id}")
                except Exception as e:
                    print(f"[ERR] {e}")
                return

# ==================== ADMIN HANDLERS ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("⛔ Access Denied!", show_alert=True)
        return
    s = db.get_stats()
    status = "🟢 Running" if (sms_fetcher and sms_fetcher.running) else "🔴 Stopped"
    msg = f"""
👑 <b>Admin Panel</b>
📡 {status}
📱 Total: <b>{s['total']}</b> | ✅ Unused: <b>{s['unused']}</b>
🔄 Assigned: <b>{s['assigned']}</b> | ❌ Used: <b>{s['used']}</b>
"""
    await update.callback_query.message.reply_text(msg, reply_markup=admin_panel_kb(), parse_mode='HTML')

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    s = db.get_stats()
    msg = f"""📊 Stats
📱 Total: {s['total']} | ✅ Unused: {s['unused']}
🔄 Assigned: {s['assigned']} | ❌ Used: {s['used']}
⚙️ Limit: {db.get_setting('numbers_per_request')} | ⏳ Cooldown: {db.get_setting('cooldown_seconds')}s
🔄 Interval: {db.get_setting('fetch_interval')}s | 👥 Group: {db.get_otp_group_id() or 'Not Set'}"""
    await update.callback_query.message.reply_text(msg)

async def admin_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    recent = db.get_recent_used(10)
    if not recent:
        await update.callback_query.answer("No data!", show_alert=True)
        return
    msg = "📋 Recent\n\n" + "\n".join([f"📞 {p} → {o or 'N/A'}" for p, o, t in recent])
    await update.callback_query.message.reply_text(msg)

async def admin_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.callback_query.message.reply_text("📁 Send .txt file or paste:\nphone,country,service")
    context.user_data['awaiting_upload'] = True

async def admin_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    cur = db.get_setting('numbers_per_request') or '3'
    await update.callback_query.message.reply_text(f"🔢 Current: {cur}\nSend new (1-10):")
    context.user_data['awaiting_limit'] = True

async def admin_set_cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    cur = db.get_setting('cooldown_seconds') or '5'
    await update.callback_query.message.reply_text(f"⏳ Current: {cur}s\nSend new (min 5):")
    context.user_data['awaiting_cooldown'] = True

async def admin_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    cur = db.get_setting('fetch_interval') or '10'
    await update.callback_query.message.reply_text(f"🔄 Current: {cur}s\nSend new (min 5):")
    context.user_data['awaiting_interval'] = True

async def admin_set_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    cur = db.get_otp_group_id() or 'Not Set'
    await update.callback_query.message.reply_text(f"👥 Current: {cur}\nForward a message or send ID:")
    context.user_data['awaiting_group'] = True

async def admin_start_fetcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    global sms_fetcher
    if sms_fetcher and sms_fetcher.running:
        await update.callback_query.answer("Already running!", show_alert=True)
        return
    sms_fetcher = IVASMSFetcher(IVASMS_EMAIL, IVASMS_PASSWORD, db, context.application)
    if sms_fetcher.start():
        await update.callback_query.message.reply_text("✅ Fetcher Started!")
    else:
        await update.callback_query.message.reply_text("❌ Failed!")

async def admin_stop_fetcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    global sms_fetcher
    if sms_fetcher:
        sms_fetcher.stop()
        sms_fetcher = None
        await update.callback_query.message.reply_text("⏹ Stopped!")
    else:
        await update.callback_query.answer("Not running!", show_alert=True)

# ==================== MESSAGE HANDLER ====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ''
    user_id = str(update.effective_user.id)
    
    if not is_admin(user_id):
        return
    
    if context.user_data.get('awaiting_upload'):
        count = db.add_numbers(text.strip().split('\n'))
        await update.message.reply_text(f"✅ {count} numbers added!")
        context.user_data['awaiting_upload'] = False
    elif context.user_data.get('awaiting_limit') and text.isdigit() and 1 <= int(text) <= 10:
        db.update_setting('numbers_per_request', text)
        await update.message.reply_text(f"✅ Limit: {text}")
        context.user_data['awaiting_limit'] = False
    elif context.user_data.get('awaiting_cooldown') and text.isdigit() and int(text) >= 5:
        db.update_setting('cooldown_seconds', text)
        await update.message.reply_text(f"✅ Cooldown: {text}s")
        context.user_data['awaiting_cooldown'] = False
    elif context.user_data.get('awaiting_interval') and text.isdigit() and int(text) >= 5:
        db.update_setting('fetch_interval', text)
        await update.message.reply_text(f"✅ Interval: {text}s")
        context.user_data['awaiting_interval'] = False
    elif context.user_data.get('awaiting_group'):
        group_id = str(update.message.forward_from_chat.id) if update.message.forward_from_chat else text.strip()
        db.update_setting('otp_group_id', group_id)
        await update.message.reply_text(f"✅ Group: {group_id}")
        context.user_data['awaiting_group'] = False

async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_upload') or not is_admin(update.effective_user.id):
        return
    file = await update.message.document.get_file()
    content = await file.download_as_bytearray()
    count = db.add_numbers(content.decode('utf-8').strip().split('\n'))
    await update.message.reply_text(f"✅ {count} numbers added!")
    context.user_data['awaiting_upload'] = False

# ==================== CALLBACK ROUTER ====================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    handlers = {
        'get_numbers': get_numbers, 'my_history': my_history, 'help_menu': help_menu,
        'admin_panel': admin_panel, 'admin_stats': admin_stats, 'admin_recent': admin_recent,
        'admin_upload': admin_upload, 'admin_set_limit': admin_set_limit,
        'admin_set_cooldown': admin_set_cooldown, 'admin_set_interval': admin_set_interval,
        'admin_set_group': admin_set_group, 'admin_start_fetcher': admin_start_fetcher,
        'admin_stop_fetcher': admin_stop_fetcher, 'back_main': start,
    }
    if data in handlers:
        await handlers[data](update, context)
    else:
        await update.callback_query.answer("...")

# ==================== MAIN ====================
def main():
    print("=" * 60)
    print("🚀 IVAS SMS BOT")
    print("=" * 60)
    
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN':
        print("❌ Set BOT_TOKEN in .env!")
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_menu))
    app.add_handler(CommandHandler('admin', admin_panel))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, group_message_handler))
    
    print(f"✅ Running! Admins: {ADMIN_IDS}")
    print("=" * 60)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()