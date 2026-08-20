import os
import asyncio
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
from bot.services.monitoring_service import start_periodic_monitoring_worker

# Handlers imports
from bot.handlers.start import start_command, main_menu_callback, guide_command, verify_sub_callback, auto_approve_join_request
from bot.handlers.admin import admin_panel_command, admin_export_csv_callback, broadcast_conv_handler
from bot.handlers.auth import pat_conv_handler
from bot.handlers.repositories import (
    show_repositories,
    repo_page_callback,
    repo_detail_callback,
    confirm_delete_repo,
    handle_delete_repo_confirmation,
    create_repo_conv_handler
)
from bot.handlers.files import file_push_conv_handler, handle_incoming_file
from bot.handlers.issues import (
    list_repo_issues,
    show_issue_detail,
    toggle_issue_status,
    create_issue_conv_handler,
    add_comment_conv_handler
)
from bot.handlers.collaborators import (
    list_collaborators_callback,
    remove_collaborator_callback,
    invite_collab_conv_handler
)
from bot.handlers.analytics import (
    activity_dashboard_callback,
    repo_analytics_menu,
    repo_analytics_detail
)
from bot.handlers.scheduler import (
    show_scheduler_menu,
    view_schedule_detail,
    toggle_schedule_callback,
    delete_schedule_callback,
    create_schedule_conv_handler
)
from bot.handlers.settings import (
    show_settings_menu,
    toggle_notifications_callback,
    prompt_disconnect_account,
    handle_disconnect_confirmation,
    change_tz_conv_handler
)

logger = logging.getLogger(__name__)

async def post_init(app: Application):
    """Post initialization hook for database indexing, Telegram command menu, and background tasks."""
    logger.info("Initializing MongoDB indexes...")
    await MongoDB.init_indices()
    
    # Register Telegram bot commands menu (appears when user types '/')
    commands = [
        BotCommand("start", "🚀 Open Main Dashboard"),
        BotCommand("guide", "📖 User Guide & Detailed Tutorial"),
        BotCommand("repos", "📁 View & Manage Repositories"),
        BotCommand("createrepo", "➕ Create New GitHub Repository"),
        BotCommand("issues", "🐛 Manage Issues & Comments"),
        BotCommand("scheduler", "⏰ Automated Scheduled Commits"),
        BotCommand("settings", "⚙️ Settings & Timezone"),
        BotCommand("help", "💡 Quick Help & Guide")
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Telegram Bot commands menu registered successfully.")
    except Exception as e:
        logger.warning(f"Failed to set bot commands menu: {e}")

    # Launch background periodic monitoring task
    asyncio.create_task(start_periodic_monitoring_worker(app))

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
    app.add_handler(create_repo_conv_handler)
    app.add_handler(file_push_conv_handler)
    app.add_handler(create_issue_conv_handler)
    app.add_handler(add_comment_conv_handler)
    app.add_handler(invite_collab_conv_handler)
    app.add_handler(create_schedule_conv_handler)
    app.add_handler(change_tz_conv_handler)

    # --- Command Handlers ---
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("guide", guide_command))
    app.add_handler(CommandHandler("help", guide_command))
    app.add_handler(CommandHandler("admin", admin_panel_command))
    app.add_handler(CommandHandler("adminpanel", admin_panel_command))
    app.add_handler(CommandHandler("repos", lambda u, c: show_repositories(u, c)))
    app.add_handler(CommandHandler("issues", lambda u, c: show_scheduler_menu(u, c)))
    app.add_handler(CommandHandler("settings", lambda u, c: show_settings_menu(u, c)))

    # --- Callback Query Handlers ---
    # Admin Panel
    app.add_handler(CallbackQueryHandler(admin_panel_command, pattern="^admin_refresh$"))
    app.add_handler(CallbackQueryHandler(admin_export_csv_callback, pattern="^admin_export_csv$"))

    # --- Callback Query Handlers ---
    # Dashboard & Navigation
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(verify_sub_callback, pattern="^verify_sub$"))
    app.add_handler(CallbackQueryHandler(guide_command, pattern="^nav_guide$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: show_repositories(u, c), pattern="^nav_repos$"))
    
    # Repositories
    app.add_handler(CallbackQueryHandler(repo_page_callback, pattern="^repo_page:"))
    app.add_handler(CallbackQueryHandler(repo_detail_callback, pattern="^repo_detail:"))
    app.add_handler(CallbackQueryHandler(confirm_delete_repo, pattern="^repo_delete_confirm:"))
    app.add_handler(CallbackQueryHandler(handle_delete_repo_confirmation, pattern="^confirm_(yes|no):del_repo:"))
    
    # Issues
    app.add_handler(CallbackQueryHandler(list_repo_issues, pattern="^repo_issues:"))
    app.add_handler(CallbackQueryHandler(show_issue_detail, pattern="^issue_detail:"))
    app.add_handler(CallbackQueryHandler(toggle_issue_status, pattern="^issue_toggle:"))

    # Collaborators
    app.add_handler(CallbackQueryHandler(list_collaborators_callback, pattern="^repo_collabs:"))
    app.add_handler(CallbackQueryHandler(remove_collaborator_callback, pattern="^remove_collab:"))

    # Activity & Analytics
    app.add_handler(CallbackQueryHandler(activity_dashboard_callback, pattern="^nav_activity$"))
    app.add_handler(CallbackQueryHandler(repo_analytics_menu, pattern="^nav_analytics$"))
    app.add_handler(CallbackQueryHandler(repo_analytics_menu, pattern="^analytics_page:"))
    app.add_handler(CallbackQueryHandler(repo_analytics_detail, pattern="^repo_analytics:"))

    # Scheduler
    app.add_handler(CallbackQueryHandler(show_scheduler_menu, pattern="^nav_scheduler$"))
    app.add_handler(CallbackQueryHandler(view_schedule_detail, pattern="^sched_view:"))
    app.add_handler(CallbackQueryHandler(toggle_schedule_callback, pattern="^sched_toggle:"))
    app.add_handler(CallbackQueryHandler(delete_schedule_callback, pattern="^sched_delete:"))

    # Settings
    app.add_handler(CallbackQueryHandler(show_settings_menu, pattern="^nav_settings$"))
    app.add_handler(CallbackQueryHandler(toggle_notifications_callback, pattern="^toggle_monitoring$"))
    app.add_handler(CallbackQueryHandler(prompt_disconnect_account, pattern="^settings_disconnect$"))
    app.add_handler(CallbackQueryHandler(handle_disconnect_confirmation, pattern="^confirm_(yes|no):disconnect:"))

    # Global Error Handler
    app.add_error_handler(error_handler)

    return app
