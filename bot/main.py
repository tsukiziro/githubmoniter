import os
import logging
from typing import Optional
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ContextTypes
)

from bot.database.mongodb import MongoDB

# Handlers imports
from bot.handlers.start import (
    start_command,
    main_menu_callback,
    guide_command,
    verify_sub_callback,
    auto_approve_join_request
)
from bot.handlers.admin import (
    admin_panel_command,
    admin_export_csv_callback,
    broadcast_conv_handler
)
from bot.handlers.auth import pat_conv_handler
from bot.handlers.scheduler import (
    show_scheduler_menu,
    view_schedule_detail,
    toggle_schedule_callback,
    delete_schedule_callback,
    create_schedule_conv_handler
)
from bot.handlers.settings import (
    show_settings_menu,
    prompt_disconnect_account,
    handle_disconnect_confirmation,
    change_tz_conv_handler
)

logger = logging.getLogger(__name__)

async def post_init(app: Application):
    """Post initialization hook for database indexing and Telegram command menu."""
    logger.info("Initializing MongoDB indexes & dropping legacy cache...")
    await MongoDB.init_indices()
    
    # Register Telegram bot commands menu (focused exclusively on commit scheduler)
    commands = [
        BotCommand("start", "🚀 Open Commit Scheduler Dashboard"),
        BotCommand("schedule", "⏰ Create or Manage Commit Schedules"),
        BotCommand("settings", "⚙️ Timezone & Account Settings"),
        BotCommand("guide", "📖 User Guide & How It Works"),
        BotCommand("help", "💡 Quick Help")
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Telegram Bot commands menu registered successfully.")
    except Exception as e:
        logger.warning(f"Failed to set bot commands menu: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global exception handler for Telegram bot errors."""
    logger.error(f"Unhandled exception while processing update: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ An unexpected error occurred while processing your request. Please try again."
            )
        except Exception:
            pass

from telegram.request import HTTPXRequest

_global_bot_app: Optional[Application] = None

def get_bot_app() -> Optional[Application]:
    return _global_bot_app

async def build_application() -> Application:
    global _global_bot_app
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN is not defined in environment variables.")

    # High-performance request pooling & tuned timeouts
    request = HTTPXRequest(
        connection_pool_size=30,
        read_timeout=30.0,
        write_timeout=10.0,
        connect_timeout=10.0,
        pool_timeout=10.0
    )

    builder = ApplicationBuilder().token(bot_token).request(request).post_init(post_init)
    app = builder.build()
    _global_bot_app = app

    # --- Register Chat Join Request Handler (Auto Approve) ---
    app.add_handler(ChatJoinRequestHandler(auto_approve_join_request))

    # --- Register Conversation Handlers (High Priority) ---
    app.add_handler(broadcast_conv_handler)
    app.add_handler(pat_conv_handler)
    app.add_handler(create_schedule_conv_handler)
    app.add_handler(change_tz_conv_handler)

    # --- Command Handlers ---
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("schedule", lambda u, c: show_scheduler_menu(u, c)))
    app.add_handler(CommandHandler("newsched", lambda u, c: show_scheduler_menu(u, c)))
    app.add_handler(CommandHandler("scheduler", lambda u, c: show_scheduler_menu(u, c)))
    app.add_handler(CommandHandler("settings", lambda u, c: show_settings_menu(u, c)))
    app.add_handler(CommandHandler("guide", guide_command))
    app.add_handler(CommandHandler("help", guide_command))
    app.add_handler(CommandHandler("admin", admin_panel_command))
    app.add_handler(CommandHandler("adminpanel", admin_panel_command))

    # --- Callback Query Handlers ---
    # Admin Panel
    app.add_handler(CallbackQueryHandler(admin_panel_command, pattern="^admin_refresh$"))
    app.add_handler(CallbackQueryHandler(admin_export_csv_callback, pattern="^admin_export_csv$"))

    # Dashboard & Navigation
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(verify_sub_callback, pattern="^verify_sub$"))
    app.add_handler(CallbackQueryHandler(guide_command, pattern="^nav_guide$"))

    # Commit Scheduler
    app.add_handler(CallbackQueryHandler(show_scheduler_menu, pattern="^nav_scheduler$"))
    app.add_handler(CallbackQueryHandler(view_schedule_detail, pattern="^sched_view:"))
    app.add_handler(CallbackQueryHandler(toggle_schedule_callback, pattern="^sched_toggle:"))
    app.add_handler(CallbackQueryHandler(delete_schedule_callback, pattern="^sched_delete:"))

    # Settings
    app.add_handler(CallbackQueryHandler(show_settings_menu, pattern="^nav_settings$"))
    app.add_handler(CallbackQueryHandler(prompt_disconnect_account, pattern="^settings_disconnect$"))
    app.add_handler(CallbackQueryHandler(handle_disconnect_confirmation, pattern="^confirm_(yes|no):disconnect:"))

    # Global Error Handler
    app.add_error_handler(error_handler)

    return app
