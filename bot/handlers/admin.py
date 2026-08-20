import os
import csv
import io
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.database.mongodb import get_admin_dashboard_stats, get_all_registered_users
from bot.utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)

ADMIN_ID = int(os.getenv("ADMIN_ID", 1447828370))
WAITING_BROADCAST_MSG = 100

def is_admin(telegram_id: int) -> bool:
    return telegram_id == ADMIN_ID

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renders Admin Panel with live statistics, user acquisition, CSV export, and broadcast."""
    telegram_id = update.effective_user.id
    if not is_admin(telegram_id):
        await update.effective_message.reply_text("❌ Access Denied: Admin privileges required.")
        return

    query = update.callback_query
    if query:
        await query.answer("Refreshing admin panel...")

    stats = await get_admin_dashboard_stats()
    
    text = (
        f"👑 <b>ADMIN CONTROL PANEL</b>\n\n"
        f"📊 <b>User Growth & Acquisition:</b>\n"
        f"• <b>Total Registered Users:</b> {stats['total_users']}\n"
        f"• <b>New Joined Today (24h):</b> {stats['new_24h']}\n"
        f"• <b>New Joined 7 Days:</b> {stats['new_7d']}\n\n"
        f"⏰ <b>Commit Schedules:</b>\n"
        f"• <b>Active Schedules:</b> {stats['active_schedules']}\n"
        f"• <b>Total Schedules Created:</b> {stats['total_schedules']}\n\n"
        f"<i>Select an admin action below:</i>"
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Refresh Analytics", callback_data="admin_refresh")],
        [InlineKeyboardButton("📥 Export User Data (CSV)", callback_data="admin_export_csv")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_start_broadcast")],
        [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
    ])

    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def admin_export_csv_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates and sends CSV file with user data & analytics to admin."""
    query = update.callback_query
    if query:
        await query.answer("Generating CSV export...")
        
    telegram_id = update.effective_user.id
    if not is_admin(telegram_id):
        return

    users = await get_all_registered_users()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Telegram ID", "GitHub Username", "Auth Method", "Connected At", "Timezone", "Notifications"])
    
    for u in users:
        writer.writerow([
            u.get("telegram_id"),
            u.get("github_username", ""),
            u.get("auth_method", ""),
            u.get("connected_at", ""),
            u.get("timezone", "UTC"),
            "Enabled" if u.get("notifications", True) else "Disabled"
        ])
        
    output.seek(0)
    document_bytes = output.getvalue().encode("utf-8")
    
    document_file = io.BytesIO(document_bytes)
    document_file.name = "github_guardian_users.csv"
    
    await update.effective_chat.send_document(
        document=document_file,
        caption=f"📊 <b>User Data Analytics Export</b>\n\nTotal Users Exported: {len(users)}",
        parse_mode="HTML"
    )

async def start_broadcast_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts admin to enter broadcast message."""
    telegram_id = update.effective_user.id
    if not is_admin(telegram_id):
        await update.effective_message.reply_text("❌ Access Denied.")
        return ConversationHandler.END

    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "📢 <b>ADMIN BROADCAST MESSAGE</b>\n\n"
        "Send the message, photo, or post you want to broadcast to <b>ALL registered users</b>.\n\n"
        "<i>Supports HTML formatting, links, photos with captions. Send /cancel to stop.</i>"
    )
    await safe_edit_or_reply(update, text=text)
    return WAITING_BROADCAST_MSG

async def receive_and_send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcasts admin message/photo to all registered users."""
    telegram_id = update.effective_user.id
    if not is_admin(telegram_id):
        return ConversationHandler.END

    users = await get_all_registered_users()
    if not users:
        await update.message.reply_text("No users registered to broadcast to.")
        return ConversationHandler.END

    status_msg = await update.message.reply_text(f"⏳ Broadcasting message to {len(users)} users...")

    success_count = 0
    fail_count = 0

    msg = update.message
    for u in users:
        uid = u.get("telegram_id")
        if not uid:
            continue
        try:
            if msg.photo:
                photo_id = msg.photo[-1].file_id
                caption = msg.caption or ""
                await context.bot.send_photo(chat_id=uid, photo=photo_id, caption=caption, parse_mode="HTML")
            else:
                text = msg.text or msg.caption or ""
                await context.bot.send_message(chat_id=uid, text=text, parse_mode="HTML")
            success_count += 1
        except Exception as e:
            logger.warning(f"Broadcast failed for user {uid}: {e}")
            fail_count += 1

    await status_msg.edit_text(
        f"📢 <b>Broadcast Complete!</b>\n\n"
        f"✅ <b>Successfully Sent:</b> {success_count} users\n"
        f"❌ <b>Failed / Blocked:</b> {fail_count} users",
        parse_mode="HTML"
    )
    return ConversationHandler.END

broadcast_conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler("broadcast", start_broadcast_wizard),
        CallbackQueryHandler(start_broadcast_wizard, pattern="^admin_start_broadcast$")
    ],
    states={
        WAITING_BROADCAST_MSG: [
            MessageHandler(filters.ALL & ~filters.COMMAND, receive_and_send_broadcast)
        ]
    },
    fallbacks=[CommandHandler("cancel", admin_panel_command)],
    per_message=False
)
