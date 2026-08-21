import logging
import pytz
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.services.auth_service import get_user_decrypted_token
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.services.scheduler_service import (
    create_commit_schedule,
    pause_commit_schedule,
    resume_commit_schedule,
    remove_commit_schedule
)
from bot.database.mongodb import get_user_schedules, get_cached_repositories, get_user
from bot.keyboards.inline import scheduler_menu_keyboard, schedule_detail_keyboard, back_cancel_keyboard
from bot.utils.helpers import safe_edit_or_reply, format_user_datetime
from bot.utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)

SCHED_SELECT_REPO, SCHED_FILE_PATH, SCHED_COMMIT_MSG, SCHED_CONTENT, SCHED_TYPE, SCHED_CRON, SCHED_TZ = range(7)

# --- Scheduler Dashboard ---
async def show_scheduler_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays scheduled commit jobs list and creation trigger."""
    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Session expired. Please reconnect your account.")
        return

    schedules = await get_user_schedules(telegram_id)
    
    if not schedules:
        text = (
            "⏰ <b>Scheduled Commits</b>\n\n"
            "You have no active commit schedules.\n"
            "Create scheduled commits for legitimate automated documentation updates, status files, or automated release notes."
        )
    else:
        text = f"⏰ <b>Scheduled Commits Dashboard</b> ({len(schedules)} total):"

    markup = scheduler_menu_keyboard(schedules)
    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def view_schedule_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    schedule_id = query.data.split("sched_view:")[1]
    telegram_id = update.effective_user.id
    schedules = await get_user_schedules(telegram_id)
    
    sched = next((s for s in schedules if s["schedule_id"] == schedule_id), None)
    if not sched:
        await safe_edit_or_reply(update, "Schedule not found.")
        return

    is_active = (sched.get("status") == "active")
    status_str = "▶️ Active" if is_active else "⏸️ Paused"
    last_run_raw = sched.get("last_run")
    last_run = last_run_raw.replace("T", " ")[:19] + " UTC" if last_run_raw else "Never"
    
    # Calculate execution metrics
    total_commits = sched.get("total_executions", 0)
    history = sched.get("execution_history", [])
    
    now_dt = datetime.now(timezone.utc)
    cutoff_4h = now_dt - timedelta(hours=4)
    cutoff_24h = now_dt - timedelta(hours=24)
    
    commits_4h = 0
    commits_24h = 0
    
    for h in history:
        try:
            h_dt = datetime.fromisoformat(h)
            if h_dt >= cutoff_4h:
                commits_4h += 1
            if h_dt >= cutoff_24h:
                commits_24h += 1
        except Exception:
            pass

    stype = sched.get("schedule_type", "").upper()
    stype_display = "🟢 DEEP_GREEN (20 Commits/Day)" if stype == "DEEP_GREEN" else stype
    cron_display = f"<code>{sched['cron_expression']}</code>" if sched.get('cron_expression') else "Every 72 mins"
    
    user_tz = sched.get('timezone', 'Asia/Kolkata')
    last_run_formatted = format_user_datetime(last_run, user_tz) if last_run != "Never" else "Never"

    text = (
        f"⏰ <b>Commit Schedule Details</b>\n\n"
        f"<b>Repo:</b> {sched['repo']}\n"
        f"<b>File Path:</b> <code>{sched['file_path']}</code>\n"
        f"<b>Commit Message:</b> {sched['commit_message']}\n"
        f"<b>Schedule Type:</b> {stype_display}\n"
        f"<b>Frequency:</b> {cron_display}\n"
        f"<b>Timezone:</b> {user_tz}\n"
        f"<b>Status:</b> {status_str}\n\n"
        f"📊 <b>Commit Execution Stats:</b>\n"
        f"• <b>Last 4 Hours:</b> {commits_4h} commits\n"
        f"• <b>Last 24 Hours:</b> {commits_24h} commits\n"
        f"• <b>Total Executed:</b> {total_commits} commits\n"
        f"🕒 <b>Last Executed:</b> {last_run_formatted}\n"
    )
    
    markup = schedule_detail_keyboard(schedule_id, is_active)
    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def toggle_schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer("Schedule status updated.")
    
    parts = query.data.split("sched_toggle:")[1].split(":")
    schedule_id = parts[0]
    action = parts[1]
    telegram_id = update.effective_user.id
    
    if action == "pause":
        await pause_commit_schedule(schedule_id, telegram_id)
    else:
        await resume_commit_schedule(schedule_id, telegram_id)
        
    await show_scheduler_menu(update, context)

async def delete_schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer("🗑 Schedule deleted!")
    
    schedule_id = query.data.split("sched_delete:")[1]
    telegram_id = update.effective_user.id
    
    await remove_commit_schedule(schedule_id, telegram_id)
    await show_scheduler_menu(update, context)

# --- Create Schedule Wizard ---
async def start_create_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    query = update.callback_query
    if query:
        await query.answer()
        if query.data.startswith("sched_page:"):
            page = int(query.data.split(":")[1])
    
    telegram_id = update.effective_user.id
    repos = await get_cached_repositories(telegram_id)
    if not repos:
        await safe_edit_or_reply(update, "Please visit Repositories first to refresh repository list.")
        return ConversationHandler.END

    per_page = 8
    total_repos = len(repos)
    total_pages = max(1, (total_repos + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    page_repos = repos[start_idx:start_idx + per_page]

    keyboard = []
    for r in page_repos:
        name = r.get("full_name")
        keyboard.append([InlineKeyboardButton(name, callback_data=f"sched_repo:{name}")])

    # Pagination buttons
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"sched_page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="ignore"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"sched_page:{page + 1}"))
        
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")])

    markup = InlineKeyboardMarkup(keyboard)

    text = f"⏰ <b>Create Commit Schedule</b>\n\nStep 1: Select target repository (Page {page}/{total_pages}):"

    await safe_edit_or_reply(update, text=text, reply_markup=markup)
    return SCHED_SELECT_REPO

async def receive_sched_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        repo_full_name = query.data.split("sched_repo:")[1]
        context.user_data["sched_repo"] = repo_full_name

    text = f"Selected repo: <b>{context.user_data.get('sched_repo', '')}</b>\n\nStep 2: Enter target file path (e.g. <code>docs/status.md</code>):"
    await safe_edit_or_reply(update, text=text)
    return SCHED_FILE_PATH

async def receive_sched_file_path(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_text = update.message.text.strip()
    file_path = "ACTIVITY.md" if input_text == "/default" else input_text
    context.user_data["sched_file_path"] = file_path

    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    repo_full_name = context.user_data.get("sched_repo", "")
    
    file_exists = False
    if token and repo_full_name:
        owner, repo_name = repo_full_name.split("/")
        client = GitHubAPIClient(token)
        try:
            res = await client.get_file_contents(owner, repo_name, file_path)
            if isinstance(res, dict) and "sha" in res:
                file_exists = True
        except GitHubAPIException:
            file_exists = False

    if file_exists:
        status_note = f"✅ <b>File <code>{file_path}</code> found in {repo_full_name}.</b>"
    else:
        status_note = (
            f"ℹ️ <b>File <code>{file_path}</code> does not exist in {repo_full_name} yet.</b>\n"
            f"<i>No problem! GitHub Guardian will auto-create this file on your schedule run!</i>"
        )

    await update.message.reply_text(
        f"{status_note}\n\n"
        f"Step 3: Enter commit message (or send /default for 'Update activity log'):",
        parse_mode="HTML"
    )
    return SCHED_COMMIT_MSG

async def receive_sched_commit_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_text = update.message.text.strip()
    commit_msg = "Update activity log" if input_text == "/default" else input_text
    context.user_data["sched_commit_msg"] = commit_msg

    await update.message.reply_text(
        f"Commit message: <i>{commit_msg}</i>\n\nStep 4: Enter file content/text template (or send /default for automatic timestamp update):",
        parse_mode="HTML"
    )
    return SCHED_CONTENT

async def receive_sched_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content_input = update.message.text.strip()
    content = "# Scheduled Automated Commit\nLast updated: {{TIMESTAMP}}\n" if content_input == "/default" else content_input
    context.user_data["sched_content"] = content

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Deep Green Mode (20 Commits/Day)", callback_data="stype_deep_green")
        ],
        [
            InlineKeyboardButton("📅 Daily (1 Commit/Day)", callback_data="stype_daily"),
            InlineKeyboardButton("📆 Weekly (Monday Midnight)", callback_data="stype_weekly")
        ],
        [
            InlineKeyboardButton("⚙️ Custom Cron Expression", callback_data="stype_custom")
        ]
    ])

    await update.message.reply_text(
        "Step 5: Select schedule frequency mode:",
        reply_markup=kb
    )
    return SCHED_TYPE

async def receive_sched_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    stype = query.data.split("stype_")[1]
    context.user_data["sched_type"] = stype

    if stype == "deep_green":
        context.user_data["sched_cron"] = ""
        context.user_data["sched_interval"] = 60 # 60 mins interval -> 1 commit every 1 hour (24 commits per day)
        return await prompt_timezone(update, context, is_callback=True)
    elif stype == "daily":
        context.user_data["sched_cron"] = "0 0 * * *"
        context.user_data["sched_interval"] = None
        return await prompt_timezone(update, context, is_callback=True)
    elif stype == "weekly":
        context.user_data["sched_cron"] = "0 0 * * 1"
        context.user_data["sched_interval"] = None
        return await prompt_timezone(update, context, is_callback=True)
    else:
        context.user_data["sched_interval"] = None
        await safe_edit_or_reply(update, text="Step 6: Enter standard 5-part cron expression (e.g., <code>0 12 * * *</code> for daily at 12:00 UTC):")
        return SCHED_CRON

async def receive_sched_cron(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cron = update.message.text.strip()
    if len(cron.split()) != 5:
        await update.message.reply_text("❌ Invalid cron expression. Please provide a 5-part expression (e.g. <code>0 12 * * *</code>):", parse_mode="HTML")
        return SCHED_CRON

    context.user_data["sched_cron"] = cron
    return await prompt_timezone(update, context, is_callback=False)

async def prompt_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool = False):
    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)
    user_tz = user.get("timezone", "UTC") if user else "UTC"

    stype = context.user_data.get("sched_type", "")
    freq_desc = "20 Commits / Day (Every 72 mins)" if stype == "deep_green" else "Standard Scheduled Commit"

    msg = (
        f"Step 7: Confirm timezone for <b>{freq_desc}</b> (Default: <code>{user_tz}</code>).\n\n"
        f"Send /default to use default or type a valid timezone (e.g. <code>Asia/Kolkata</code>, <code>America/New_York</code>):"
    )
    
    await safe_edit_or_reply(update, text=msg)
    return SCHED_TZ

async def receive_sched_tz_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz_input = update.message.text.strip()
    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)
    user_tz = user.get("timezone", "UTC") if user else "UTC"

    tz_final = user_tz if tz_input == "/default" else tz_input

    repo = context.user_data["sched_repo"]
    file_path = context.user_data["sched_file_path"]
    commit_msg = context.user_data["sched_commit_msg"]
    content = context.user_data["sched_content"]
    stype = context.user_data["sched_type"]
    cron = context.user_data.get("sched_cron", "")
    interval = context.user_data.get("sched_interval")

    try:
        s_id = await create_commit_schedule(
            telegram_id=telegram_id,
            repo=repo,
            file_path=file_path,
            commit_message=commit_msg,
            content=content,
            schedule_type=stype,
            cron_expression=cron,
            interval_minutes=interval,
            user_tz=tz_final
        )

        schedule_mode_text = "🟢 <b>Deep Green Mode (20 Commits/Day)</b>" if stype == "deep_green" else f"<b>Schedule Mode:</b> {stype.upper()}"

        await update.message.reply_text(
            f"🎉 <b>Commit Schedule Created!</b>\n\n"
            f"{schedule_mode_text}\n"
            f"<b>Repo:</b> {repo}\n"
            f"<b>File:</b> <code>{file_path}</code>\n"
            f"<b>Frequency:</b> Every 72 minutes (20 commits/day)\n" if stype == "deep_green" else f"<b>Cron:</b> <code>{cron}</code>\n"
            f"<b>Timezone:</b> {tz_final}\n"
            f"<b>Schedule ID:</b> <code>{s_id}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏰ View Schedule", callback_data=f"sched_view:{s_id}")],
                [InlineKeyboardButton("⏰ All Schedules", callback_data="nav_scheduler")],
                [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
            ])
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to create schedule: {e}")

    return ConversationHandler.END

create_schedule_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_create_schedule, pattern="^sched_create$"),
        CallbackQueryHandler(start_create_schedule, pattern="^repo_sched:")
    ],
    states={
        SCHED_SELECT_REPO: [
            CallbackQueryHandler(start_create_schedule, pattern="^sched_page:"),
            CallbackQueryHandler(receive_sched_repo, pattern="^sched_repo:")
        ],
        SCHED_FILE_PATH: [
            CommandHandler("default", receive_sched_file_path),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_file_path)
        ],
        SCHED_COMMIT_MSG: [
            CommandHandler("default", receive_sched_commit_msg),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_commit_msg)
        ],
        SCHED_CONTENT: [
            CommandHandler("default", receive_sched_content),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_content)
        ],
        SCHED_TYPE: [CallbackQueryHandler(receive_sched_type, pattern="^stype_")],
        SCHED_CRON: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_cron)],
        SCHED_TZ: [
            CommandHandler("default", receive_sched_tz_and_save),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_tz_and_save)
        ]
    },
    fallbacks=[CallbackQueryHandler(show_scheduler_menu, pattern="^nav_scheduler$")],
    per_message=False
)
