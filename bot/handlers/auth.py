import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.services.auth_service import authenticate_with_pat, generate_oauth_url
from bot.keyboards.inline import back_cancel_keyboard, auth_keyboard
from bot.handlers.start import render_dashboard, get_user_decrypted_token
from bot.utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)

WAITING_FOR_PAT = 1

async def start_pat_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback when user clicks 'Use Personal Access Token'."""
    query = update.callback_query
    if query:
        await query.answer()
    
    instructions = (
        "🔑 <b>Personal Access Token (PAT) Instructions</b>\n\n"
        "1. Go to GitHub Settings ➔ Developer Settings ➔ <b>Personal Access Tokens</b>.\n"
        "2. Generate a <b>Fine-Grained Token</b> (or Classic Token).\n"
        "3. Grant the required permissions:\n"
        "   • <b>Repository permissions:</b> Contents (read/write), Issues (read/write), Administration (read/write)\n"
        "4. Copy your token and paste it here in chat.\n\n"
        "⚠️ <i>Your token message will be deleted immediately for security. Plaintext tokens are never stored or logged.</i>"
    )
    
    await safe_edit_or_reply(update, text=instructions, reply_markup=back_cancel_keyboard("main_menu"))
    return WAITING_FOR_PAT

async def receive_pat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives PAT input from user, attempts message deletion, validates and stores encrypted PAT."""
    pat = update.message.text.strip()
    telegram_id = update.effective_user.id

    # 1. Attempt to delete message immediately for security
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete PAT message from user {telegram_id}: {e}")

    # Send progress message
    status_msg = await update.effective_chat.send_message("⏳ Validating Personal Access Token with GitHub...")

    try:
        # Validate PAT and save user
        user_doc = await authenticate_with_pat(telegram_id, pat)
        username = user_doc["github_username"]
        
        await status_msg.edit_text(
            f"✅ <b>Authentication Successful!</b>\n\nConnected as <b>@{username}</b> via Personal Access Token.",
            parse_mode="HTML"
        )
        
        # Render dashboard
        token, user = await get_user_decrypted_token(telegram_id)
        if token and user:
            await render_dashboard(update, context, telegram_id, user, token)

        return ConversationHandler.END
    except Exception as e:
        logger.error(f"PAT authentication failed for user {telegram_id}: {e}")
        oauth_url = generate_oauth_url(telegram_id)
        await status_msg.edit_text(
            "❌ <b>Authentication Failed</b>\n\nThe provided Personal Access Token was invalid or lacked required permissions.",
            parse_mode="HTML",
            reply_markup=auth_keyboard(oauth_url)
        )
        return ConversationHandler.END

async def cancel_auth_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels PAT auth flow."""
    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    if token and user:
        await render_dashboard(update, context, telegram_id, user, token)
    else:
        oauth_url = generate_oauth_url(telegram_id)
        await safe_edit_or_reply(update, text="Authentication cancelled.", reply_markup=auth_keyboard(oauth_url))
    return ConversationHandler.END

pat_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_pat_flow, pattern="^auth_pat$")],
    states={
        WAITING_FOR_PAT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pat_message)
        ]
    },
    fallbacks=[
        CallbackQueryHandler(cancel_auth_flow, pattern="^main_menu$"),
        CommandHandler("cancel", cancel_auth_flow)
    ],
    per_message=False
)
