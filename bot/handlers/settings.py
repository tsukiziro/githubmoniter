import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.services.auth_service import get_user_decrypted_token, disconnect_user_account, generate_oauth_url
from bot.database.mongodb import get_user, update_user_settings
from bot.keyboards.inline import settings_keyboard, confirm_keyboard, auth_keyboard, back_cancel_keyboard
from bot.utils.helpers import safe_edit_or_reply, format_user_datetime

logger = logging.getLogger(__name__)

SET_TIMEZONE = 1

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renders settings menu."""
    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)
    if not user:
        await safe_edit_or_reply(update, "Account not connected.")
        return

    username = user.get("github_username", "Unknown")
    auth_method = user.get("auth_method", "oauth").upper()
    notifications = "Enabled 🔔" if user.get("notifications", True) else "Disabled 🔕"
    tz = user.get("timezone", "Asia/Kolkata")
    connected_at = format_user_datetime(user.get("connected_at", ""), tz)

    text = (
        f"⚙️ <b>GITHUB GUARDIAN SETTINGS</b>\n\n"
        f"👤 <b>Connected Account:</b> @{username}\n"
        f"🔐 <b>Auth Method:</b> {auth_method}\n"
        f"🔔 <b>Notifications:</b> {notifications}\n"
        f"🌐 <b>Timezone:</b> {tz}\n"
        f"📅 <b>Connected Since:</b> {connected_at}"
    )

    markup = settings_keyboard()
    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def toggle_notifications_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles notification settings."""
    query = update.callback_query
    if query:
        await query.answer()
    telegram_id = update.effective_user.id
    
    user = await get_user(telegram_id)
    if user:
        current = user.get("notifications", True)
        new_val = not current
        await update_user_settings(telegram_id, {"notifications": new_val})
        status_text = "Enabled 🔔" if new_val else "Disabled 🔕"
        if query:
            await query.answer(f"Notifications {status_text}")
        
    await show_settings_menu(update, context)

async def prompt_disconnect_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts confirmation to disconnect GitHub account."""
    query = update.callback_query
    if query:
        await query.answer()
    
    text = (
        "⚠️ <b>Disconnect Account Confirmation</b>\n\n"
        "Are you sure you want to disconnect your GitHub account?\n"
        "<i>This will permanently delete your stored token, cached repositories, monitoring settings, and active scheduled commits from our database.</i>"
    )
    markup = confirm_keyboard("disconnect", "user")
    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def handle_disconnect_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes confirmation to disconnect account."""
    query = update.callback_query
    if query:
        await query.answer()
        decision = query.data.split(":")[0]
    else:
        decision = "confirm_no"
        
    if decision == "confirm_no":
        await show_settings_menu(update, context)
        return

    telegram_id = update.effective_user.id
    await disconnect_user_account(telegram_id)
    
    oauth_url = generate_oauth_url(telegram_id)
    text = "🔌 <b>Account Disconnected</b>\n\nYour GitHub credentials and settings have been securely deleted."
    await safe_edit_or_reply(update, text=text, reply_markup=auth_keyboard(oauth_url))

# --- Change Timezone Wizard ---
async def start_change_tz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    text = "🌐 <b>Change Timezone</b>\n\nPlease enter a valid IANA timezone name (e.g. <code>Asia/Kolkata</code>, <code>America/New_York</code>, <code>Europe/London</code>, <code>UTC</code>):"
    markup = back_cancel_keyboard("nav_settings")
    await safe_edit_or_reply(update, text=text, reply_markup=markup)
    return SET_TIMEZONE

async def receive_and_save_tz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz_input = update.message.text.strip()
    telegram_id = update.effective_user.id
    
    try:
        import pytz
        pytz.timezone(tz_input)
        await update_user_settings(telegram_id, {"timezone": tz_input})
        await update.message.reply_text(f"✅ Timezone updated to <b>{tz_input}</b>.", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("❌ Invalid timezone name. Please try again (e.g. <code>Asia/Kolkata</code> or <code>UTC</code>):", parse_mode="HTML")
        return SET_TIMEZONE

    return ConversationHandler.END

change_tz_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_change_tz, pattern="^settings_tz$")],
    states={
        SET_TIMEZONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_and_save_tz)]
    },
    fallbacks=[CallbackQueryHandler(show_settings_menu, pattern="^nav_settings$")],
    per_message=False
)
