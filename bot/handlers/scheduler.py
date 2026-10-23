import logging
import pytz
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)
from bot.services.auth_service import get_user_decrypted_token
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.services.scheduler_service import (
    create_commit_schedule,
    pause_commit_schedule,
    resume_commit_schedule,
    remove_commit_schedule
)
from bot.database.mongodb import get_user_schedules, get_user
from bot.keyboards.inline import scheduler_menu_keyboard, schedule_detail_keyboard, back_cancel_keyboard
from bot.utils.helpers import safe_edit_or_reply, format_user_datetime

logger = logging.getLogger(__name__)

(
    SCHED_REPO_NAME,
    SCHED_REPO_VISIBILITY,
    SCHED_FILE_PATH,
    SCHED_COMMIT_MSG,
    SCHED_CONTENT,
    SCHED_TYPE,
    SCHED_COMMITS_PER_DAY,
    SCHED_START_TIME,
    SCHED_WEEKLY_PLANNER,
    SCHED_TZ
) = range(10)

def parse_start_time_str(time_str: str) -> Optional[str]:
    """Parses user input into 24-hour HH:MM format (e.g. 09:00 AM -> 09:00)."""
    clean = time_str.strip().lower()
    if clean in ["now", "immediately", "/now", "start immediately"]:
        return "now"
    formats = ["%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M", "%H"]
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt).strftime("%H:%M")
        except ValueError:
            pass
    return None

def parse_weekly_planner_input(text: str) -> Dict[str, int]:
    """Parses weekly commit plan input into day dict."""
    default_plan = {"Mon": 5, "Tue": 8, "Wed": 10, "Thu": 5, "Fri": 8, "Sat": 2, "Sun": 1}
    clean = text.strip().lower()
    
    if "," in clean or len(clean.split()) == 7:
        parts = [p.strip() for p in clean.replace(",", " ").split() if p.strip().isdigit()]
        if len(parts) == 7:
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            return {d: int(parts[i]) for i, d in enumerate(days)}

    day_map = {
        "mon": "Mon", "monday": "Mon",
        "tue": "Tue", "tuesday": "Tue",
        "wed": "Wed", "wednesday": "Wed",
        "thu": "Thu", "thursday": "Thu",
        "fri": "Fri", "friday": "Fri",
        "sat": "Sat", "saturday": "Sat",
        "sun": "Sun", "sunday": "Sun"
    }

    matches = re.findall(r'([a-z]{3,9})[\s:=]+(\d+)', clean)
    if matches:
        res = dict(default_plan)
        for d_str, count in matches:
            if d_str in day_map:
                res[day_map[d_str]] = int(count)
        return res

    return default_plan

# --- Scheduler Dashboard ---
async def show_scheduler_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays scheduled commit jobs list and creation trigger."""
    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        await safe_edit_or_reply(update, "Session expired. Please reconnect your account.")
        return

    schedules = await get_user_schedules(telegram_id)
    
    if not schedules:
        text = (
            "⏰ <b>Commit Scheduler</b>\n\n"
            "You have no active commit schedules yet.\n\n"
            "Click <b>➕ New Schedule</b> to create a dedicated repository on GitHub (Public or Private) "
            "and set up automated daily or weekly commits!"
        )
    else:
        text = f"⏰ <b>Commit Scheduler Dashboard</b> ({len(schedules)} active):"

    markup = scheduler_menu_keyboard(schedules)
    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def view_schedule_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays detailed info and management actions for a schedule."""
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

    is_active = sched["status"] == "active"
    status_str = "🟢 Active" if is_active else "⏸️ Paused"
    
    history = sched.get("execution_history", [])
    now_utc = datetime.now(timezone.utc)
    
    commits_4h = sum(
        1 for ts in history 
        if isinstance(ts, str) and (now_utc - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() <= 14400
    )
    commits_24h = sum(
        1 for ts in history 
        if isinstance(ts, str) and (now_utc - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() <= 86400
    )
    total_commits = len(history) if history else sched.get("total_executions", 0)
    
    last_run = sched.get("last_run", "Never")
    stype = sched.get("schedule_type", "custom")
    user_tz = sched.get('timezone', 'Asia/Kolkata')
    last_run_formatted = format_user_datetime(last_run, user_tz) if last_run != "Never" else "Never"
    
    cpd = sched.get("commits_per_day")
    stime = sched.get("start_time", "now")
    interval = sched.get("interval_minutes")
    weekly_plan = sched.get("weekly_plan")

    if stype == "weekly_planner" and isinstance(weekly_plan, dict):
        total_wk = sum(weekly_plan.values())
        plan_str = ", ".join([f"{d}:{c}" for d, c in weekly_plan.items()])
        stype_display = f"📆 Weekly Planner ({total_wk} Commits/Week)"
        cron_display = f"Daily distribution: {plan_str}"
    elif stype == "custom_daily" or cpd:
        stype_display = f"🎯 Custom Daily ({cpd or 24} Commits/Day)"
        cron_display = f"1 commit every {interval} mins (Starts: {stime})"
    elif stype == "deep_green":
        stype_display = "🟢 Deep Green Mode (24 Commits/Day)"
        cron_display = "Every 60 mins (24 commits/day)"
    else:
        stype_display = stype.upper()
        cron_display = f"<code>{sched.get('cron_expression', 'N/A')}</code>"

    text = (
        f"⏰ <b>Commit Schedule Details</b>\n\n"
        f"🆔 <b>Schedule ID:</b> <code>{schedule_id}</code>\n"
        f"📦 <b>Dedicated Repo:</b> {sched['repo']}\n"
        f"📄 <b>File:</b> <code>{sched['file_path']}</code>\n"
        f"💬 <b>Commit Message:</b> {sched['commit_message']}\n"
        f"⚙️ <b>Type:</b> {stype_display}\n"
        f"⏱ <b>Frequency:</b> {cron_display}\n"
        f"🌐 <b>Timezone:</b> {user_tz}\n"
        f"🟢 <b>Status:</b> {status_str}\n\n"
        f"📊 <b>Execution Stats:</b>\n"
        f"• <b>Last 4 Hours:</b> {commits_4h} commits\n"
        f"• <b>Last 24 Hours:</b> {commits_24h} commits\n"
        f"• <b>Total Commits Pushed:</b> {total_commits}\n"
        f"🕒 <b>Last Run:</b> {last_run_formatted}\n"
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

# --- Create Schedule Wizard (Dedicated Repository Setup) ---

async def start_create_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: User initiates commit schedule creation by setting up a dedicated repository."""
    query = update.callback_query
    if query:
        await query.answer()

    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        await safe_edit_or_reply(update, "Session expired. Please reconnect your account.")
        return ConversationHandler.END

    # Reset wizard state
    context.user_data.pop("sched_repo", None)
    context.user_data.pop("new_repo_name", None)
    context.user_data.pop("is_private", None)
    context.user_data.pop("sched_file_path", None)
    context.user_data.pop("sched_commit_msg", None)
    context.user_data.pop("sched_content", None)
    context.user_data.pop("sched_type", None)
    context.user_data.pop("sched_cpd", None)
    context.user_data.pop("sched_start_time", None)

    msg = (
        "📁 <b>Step 1/5: Create Schedule Repository</b>\n\n"
        "To keep your personal repositories completely untouched, this bot requires a dedicated GitHub repository for scheduled commits.\n\n"
        "Please enter a repository name to create on your GitHub account:\n"
        "<i>(e.g., <code>github-streak</code>, <code>commit-tracker</code>, <code>daily-activity</code>)</i>\n\n"
        "👉 <b>Type repository name below:</b>"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]])
    await safe_edit_or_reply(update, text=msg, reply_markup=kb)
    return SCHED_REPO_NAME

async def receive_sched_repo_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives repository name and asks for public vs private visibility."""
    raw_name = update.message.text.strip()
    clean_name = re.sub(r'\s+', '-', raw_name).strip("-")

    if not re.match(r'^[a-zA-Z0-9_.-]+$', clean_name) or len(clean_name) > 100:
        await update.message.reply_text(
            "❌ <b>Invalid repository name.</b>\n"
            "Please use only letters, numbers, hyphens, and underscores (e.g. <code>commit-tracker</code>):",
            parse_mode="HTML"
        )
        return SCHED_REPO_NAME

    context.user_data["new_repo_name"] = clean_name

    msg = (
        f"🔒 <b>Step 2/5: Repository Visibility</b>\n\n"
        f"Repository Name: <b>{clean_name}</b>\n\n"
        f"Do you want this repository to be <b>Public</b> or <b>Private</b> on your GitHub account?"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Public", callback_data="sched_vis:public"),
            InlineKeyboardButton("🔒 Private", callback_data="sched_vis:private")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")
        ]
    ])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)
    return SCHED_REPO_VISIBILITY

async def receive_sched_visibility(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Creates the repository on GitHub with chosen visibility and transitions to file path."""
    query = update.callback_query
    await query.answer()
    
    vis_type = query.data.split("sched_vis:")[1]
    is_private = (vis_type == "private")
    vis_label = "🔒 Private" if is_private else "🌐 Public"

    repo_name = context.user_data.get("new_repo_name")
    if not repo_name:
        await safe_edit_or_reply(update, "Session expired. Please restart schedule creation.")
        return ConversationHandler.END

    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        await safe_edit_or_reply(update, "Session expired. Please reconnect your account.")
        return ConversationHandler.END

    client = GitHubAPIClient(token)
    await safe_edit_or_reply(update, f"⏳ Creating {vis_label} repository <b>{repo_name}</b> on GitHub...")

    username = user.get("github_username", "")
    full_name = f"{username}/{repo_name}"

    try:
        repo_res = await client.create_repository(
            name=repo_name,
            description="Dedicated repository for automated commit schedules",
            private=is_private
        )
        full_name = repo_res.get("full_name") or full_name
    except GitHubAPIException as ge:
        err_str = str(ge).lower()
        if "already exists" in err_str or ge.status_code == 422:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Use This Existing Repo", callback_data=f"sched_use_existing:{full_name}")],
                [InlineKeyboardButton("🔁 Enter Another Name", callback_data="sched_retry_name")],
                [InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]
            ])
            msg = (
                f"ℹ️ <b>Repository '{repo_name}' already exists</b> on your GitHub account.\n\n"
                f"Would you like to use <b>{full_name}</b> for this schedule, or choose a different name?"
            )
            await safe_edit_or_reply(update, text=msg, reply_markup=kb)
            return SCHED_REPO_VISIBILITY
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Try Another Name", callback_data="sched_retry_name")],
                [InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]
            ])
            await safe_edit_or_reply(update, text=f"❌ Failed to create repository: {ge}", reply_markup=kb)
            return SCHED_REPO_VISIBILITY
    except Exception as e:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔁 Try Another Name", callback_data="sched_retry_name")],
            [InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]
        ])
        await safe_edit_or_reply(update, text=f"❌ Error creating repository: {e}", reply_markup=kb)
        return SCHED_REPO_VISIBILITY

    context.user_data["sched_repo"] = full_name
    context.user_data["is_private"] = is_private

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Use Default (ACTIVITY.md)", callback_data="sched_file_default")],
        [InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]
    ])
    msg = (
        f"✅ <b>Repository Ready!</b>\n"
        f"📦 <b>{full_name}</b> ({vis_label})\n\n"
        f"📄 <b>Step 3/5: Target File Path</b>\n\n"
        f"Enter the file path inside this repository where commits will be pushed, "
        f"or click below to use the default (<code>ACTIVITY.md</code>):"
    )
    await safe_edit_or_reply(update, text=msg, reply_markup=kb)
    return SCHED_FILE_PATH

async def receive_use_existing_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows user to select their existing schedule repo if already created."""
    query = update.callback_query
    await query.answer()
    full_name = query.data.split("sched_use_existing:")[1]
    context.user_data["sched_repo"] = full_name

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Use Default (ACTIVITY.md)", callback_data="sched_file_default")],
        [InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]
    ])
    msg = (
        f"✅ <b>Selected Repository:</b> <b>{full_name}</b>\n\n"
        f"📄 <b>Step 3/5: Target File Path</b>\n\n"
        f"Enter the file path where commits will be pushed, or click below for default (<code>ACTIVITY.md</code>):"
    )
    await safe_edit_or_reply(update, text=msg, reply_markup=kb)
    return SCHED_FILE_PATH

async def receive_sched_file_path(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives target file path (via button or text)."""
    query = update.callback_query
    if query:
        await query.answer()
        file_path = "ACTIVITY.md"
    else:
        text = update.message.text.strip()
        file_path = "ACTIVITY.md" if text in ["/default", "default"] else text

    context.user_data["sched_file_path"] = file_path

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Use Default ('Update activity log')", callback_data="sched_msg_default")],
        [InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]
    ])
    msg = (
        f"Target file: <code>{file_path}</code>\n\n"
        f"💬 <b>Step 4/5: Commit Message</b>\n\n"
        f"Enter the commit message for scheduled commits, or click below for default (<code>Update activity log</code>):"
    )
    if query:
        await safe_edit_or_reply(update, text=msg, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)
    return SCHED_COMMIT_MSG

async def receive_sched_commit_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives commit message and prompts for frequency mode."""
    query = update.callback_query
    if query:
        await query.answer()
        commit_msg = "Update activity log"
    else:
        text = update.message.text.strip()
        commit_msg = "Update activity log" if text in ["/default", "default"] else text

    context.user_data["sched_commit_msg"] = commit_msg
    context.user_data["sched_content"] = "# Scheduled Automated Commit\nLast updated: {{TIMESTAMP}}\n"

    repo = context.user_data.get("sched_repo", "")
    file_path = context.user_data.get("sched_file_path", "ACTIVITY.md")

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 Custom Commits / Day", callback_data="stype_custom_daily")
        ],
        [
            InlineKeyboardButton("🟢 Deep Green Mode (24 Commits/Day)", callback_data="stype_deep_green")
        ],
        [
            InlineKeyboardButton("📆 Weekly Planner (Mon-Sun Custom Plan)", callback_data="stype_weekly_planner")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")
        ]
    ])
    msg = (
        f"📦 <b>Target Repository:</b> <b>{repo}</b>\n"
        f"📄 <b>File:</b> <code>{file_path}</code>\n"
        f"💬 <b>Message:</b> <i>{commit_msg}</i>\n\n"
        f"⏰ <b>Step 5/5: Select Commit Frequency Mode</b>:"
    )
    if query:
        await safe_edit_or_reply(update, text=msg, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)
    return SCHED_TYPE

async def receive_sched_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles schedule frequency choice."""
    query = update.callback_query
    await query.answer()
    stype = query.data.split("stype_")[1]
    context.user_data["sched_type"] = stype

    if stype == "custom_daily":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("5 Commits/Day", callback_data="cpd_5"),
                InlineKeyboardButton("10 Commits/Day", callback_data="cpd_10")
            ],
            [
                InlineKeyboardButton("15 Commits/Day", callback_data="cpd_15"),
                InlineKeyboardButton("20 Commits/Day", callback_data="cpd_20")
            ],
            [
                InlineKeyboardButton("24 Commits/Day (Hourly)", callback_data="cpd_24"),
                InlineKeyboardButton("50 Commits/Day", callback_data="cpd_50")
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")
            ]
        ])
        msg = (
            "📊 <b>Step 5a: How many commits per day do you want?</b>\n\n"
            "Select an option below or type any custom number (1 to 100):"
        )
        await safe_edit_or_reply(update, text=msg, reply_markup=kb)
        return SCHED_COMMITS_PER_DAY

    elif stype == "weekly_planner":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚡ Standard Plan (39 commits/week)", callback_data="wp_default")
            ],
            [
                InlineKeyboardButton("🟢 Balanced (35 commits/week - 5/day)", callback_data="wp_balanced"),
                InlineKeyboardButton("🔥 Heavy (105 commits/week - 15/day)", callback_data="wp_heavy")
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")
            ]
        ])
        msg = (
            "📆 <b>Step 5a: Weekly Planner</b>\n\n"
            "Set custom commit targets for each day of the week!\n\n"
            "Select a preset above or type your custom plan (e.g. <code>Mon 5, Tue 8, Wed 10, Thu 5, Fri 8, Sat 2, Sun 1</code>):"
        )
        await safe_edit_or_reply(update, text=msg, reply_markup=kb)
        return SCHED_WEEKLY_PLANNER

    elif stype == "deep_green":
        context.user_data["sched_cron"] = ""
        context.user_data["sched_interval"] = 60
        context.user_data["sched_cpd"] = 24
        context.user_data["sched_start_time"] = "now"
        return await prompt_timezone(update, context, is_callback=True)

async def receive_commits_per_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes commits per day count."""
    query = update.callback_query
    if query:
        await query.answer()
        cpd = int(query.data.split("cpd_")[1])
    else:
        text = update.message.text.strip()
        if not text.isdigit() or int(text) < 1 or int(text) > 200:
            await update.message.reply_text("❌ Please enter a valid commit count between 1 and 200 (e.g. <code>10</code>):", parse_mode="HTML")
            return SCHED_COMMITS_PER_DAY
        cpd = int(text)

    context.user_data["sched_cpd"] = cpd
    interval_minutes = max(1, int((24 * 60) / cpd))
    context.user_data["sched_interval"] = interval_minutes

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌅 09:00 AM", callback_data="stime_09:00"),
            InlineKeyboardButton("☀️ 12:00 PM", callback_data="stime_12:00")
        ],
        [
            InlineKeyboardButton("🌆 06:00 PM", callback_data="stime_18:00"),
            InlineKeyboardButton("🌙 09:00 PM", callback_data="stime_21:00")
        ],
        [
            InlineKeyboardButton("🚀 Start Immediately (/now)", callback_data="stime_now")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")
        ]
    ])
    msg = (
        f"✅ <b>{cpd} Commits / Day</b> (1 commit every {interval_minutes} minutes).\n\n"
        f"🕒 <b>Step 5b: What time should commits start each day?</b>\n\n"
        f"Select a daily start time or type in <code>HH:MM</code> 24-hr format (e.g. <code>09:00</code> or send <code>now</code>):"
    )
    await safe_edit_or_reply(update, text=msg, reply_markup=kb)
    return SCHED_START_TIME

async def receive_start_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes start time."""
    query = update.callback_query
    if query:
        await query.answer()
        stime = query.data.split("stime_")[1]
    else:
        text = update.message.text.strip()
        parsed = parse_start_time_str(text)
        if not parsed:
            await update.message.reply_text(
                "❌ Invalid time format. Enter a valid time (e.g. <code>09:00 AM</code>, <code>14:00</code>, or send <code>now</code>):",
                parse_mode="HTML"
            )
            return SCHED_START_TIME
        stime = parsed

    context.user_data["sched_start_time"] = stime
    return await prompt_timezone(update, context, is_callback=bool(query))

async def receive_weekly_planner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes weekly planner choices."""
    query = update.callback_query
    if query:
        await query.answer()
        choice = query.data.split("wp_")[1]
        if choice == "balanced":
            plan = {d: 5 for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
        elif choice == "heavy":
            plan = {d: 15 for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
        else:
            plan = {"Mon": 5, "Tue": 8, "Wed": 10, "Thu": 5, "Fri": 8, "Sat": 2, "Sun": 1}
    else:
        text = update.message.text.strip()
        plan = parse_weekly_planner_input(text)

    context.user_data["sched_weekly_plan"] = plan
    total_wk = sum(plan.values())
    
    context.user_data["sched_interval"] = 60
    context.user_data["sched_start_time"] = "now"

    plan_str = ", ".join([f"{d}:{c}" for d, c in plan.items()])
    msg = (
        f"✅ <b>Weekly Planner Configured ({total_wk} Commits/Week):</b>\n\n"
        f"<code>{plan_str}</code>\n\n"
        f"Proceeding to timezone confirmation..."
    )
    if query:
        await safe_edit_or_reply(update, text=msg)
    else:
        await update.message.reply_text(msg, parse_mode="HTML")
        
    return await prompt_timezone(update, context, is_callback=bool(query))

async def prompt_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool = False):
    """Prompts user to confirm or set timezone."""
    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)
    user_tz = user.get("timezone", "Asia/Kolkata") if user else "Asia/Kolkata"

    stype = context.user_data.get("sched_type", "")
    cpd = context.user_data.get("sched_cpd")
    weekly_plan = context.user_data.get("sched_weekly_plan")
    
    if stype == "weekly_planner" and weekly_plan:
        freq_desc = f"Weekly Planner ({sum(weekly_plan.values())} Commits/Week)"
    elif stype == "custom_daily":
        freq_desc = f"{cpd} Commits / Day"
    elif stype == "deep_green":
        freq_desc = "24 Commits / Day (Hourly)"
    else:
        freq_desc = "Scheduled Commit"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Use Default Timezone ({user_tz})", callback_data="tz_default")],
        [InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]
    ])
    msg = (
        f"🌐 <b>Final Step: Timezone Confirmation</b>\n\n"
        f"Schedule: <b>{freq_desc}</b>\n"
        f"Current Timezone: <code>{user_tz}</code>\n\n"
        f"Click below to confirm, or type another valid timezone (e.g. <code>America/New_York</code>, <code>Europe/London</code>, <code>UTC</code>):"
    )
    await safe_edit_or_reply(update, text=msg, reply_markup=kb)
    return SCHED_TZ

async def receive_sched_tz_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves commit schedule and starts APScheduler job."""
    query = update.callback_query
    if query:
        await query.answer()
        tz_input = "/default"
    else:
        tz_input = update.message.text.strip() if update.message else "/default"

    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)
    user_tz = user.get("timezone", "Asia/Kolkata") if user else "Asia/Kolkata"

    if tz_input != "/default":
        try:
            pytz.timezone(tz_input)
            tz_final = tz_input
        except Exception:
            await update.message.reply_text("❌ Invalid timezone name. Please try again (e.g. <code>Asia/Kolkata</code> or <code>UTC</code>):", parse_mode="HTML")
            return SCHED_TZ
    else:
        tz_final = user_tz

    repo = context.user_data["sched_repo"]
    file_path = context.user_data["sched_file_path"]
    commit_msg = context.user_data["sched_commit_msg"]
    content = context.user_data.get("sched_content", "# Scheduled Automated Commit\nLast updated: {{TIMESTAMP}}\n")
    stype = context.user_data["sched_type"]
    cron = context.user_data.get("sched_cron", "")
    interval = context.user_data.get("sched_interval")
    cpd = context.user_data.get("sched_cpd")
    stime = context.user_data.get("sched_start_time", "now")
    weekly_plan = context.user_data.get("sched_weekly_plan")

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
            commits_per_day=cpd,
            start_time=stime,
            weekly_plan=weekly_plan,
            user_tz=tz_final
        )

        if stype == "weekly_planner" and weekly_plan:
            mode_desc = f"📆 <b>Weekly Planner ({sum(weekly_plan.values())} Commits/Week)</b>"
            plan_str = ", ".join([f"{d}:{c}" for d, c in weekly_plan.items()])
            freq_desc = f"• <b>Weekly Distribution:</b> <code>{plan_str}</code>\n"
        elif stype == "custom_daily":
            mode_desc = f"🎯 <b>Custom Daily Schedule ({cpd} Commits/Day)</b>"
            freq_desc = f"• <b>Commits / Day:</b> {cpd}\n• <b>Interval:</b> Every {interval} minutes\n• <b>Daily Start Time:</b> {stime} ({tz_final})\n"
        elif stype == "deep_green":
            mode_desc = "🟢 <b>Deep Green Mode (24 Commits/Day)</b>"
            freq_desc = "• <b>Frequency:</b> Every 60 minutes (24 commits/day)\n"
        else:
            mode_desc = f"<b>Schedule Mode:</b> {stype.upper()}"
            freq_desc = f"• <b>Cron:</b> <code>{cron}</code>\n"

        success_text = (
            f"🎉 <b>Commit Schedule Created & Running!</b>\n\n"
            f"🆔 <b>Schedule ID:</b> <code>{s_id}</code>\n"
            f"{mode_desc}\n"
            f"• <b>Dedicated Repo:</b> {repo}\n"
            f"• <b>File:</b> <code>{file_path}</code>\n"
            f"{freq_desc}"
            f"• <b>Timezone:</b> {tz_final}\n\n"
            f"<i>Automated commits will strictly push only to {repo}.</i>"
        )
        
        reply_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏰ View Schedule", callback_data=f"sched_view:{s_id}")],
            [InlineKeyboardButton("⏰ All Schedules", callback_data="nav_scheduler")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
        ])

        if query:
            await safe_edit_or_reply(update, text=success_text, reply_markup=reply_kb)
        else:
            await update.message.reply_text(success_text, parse_mode="HTML", reply_markup=reply_kb)
    except Exception as e:
        logger.error(f"Failed to create commit schedule: {e}", exc_info=True)
        err_text = f"❌ Failed to create commit schedule: {e}"
        if query:
            await safe_edit_or_reply(update, text=err_text)
        else:
            await update.message.reply_text(err_text)

    return ConversationHandler.END

async def cancel_schedule_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels schedule creation wizard."""
    await safe_edit_or_reply(update, text="❌ Schedule creation cancelled.")
    await show_scheduler_menu(update, context)
    return ConversationHandler.END

create_schedule_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_create_schedule, pattern="^sched_create$"),
        CallbackQueryHandler(start_create_schedule, pattern="^sched_retry_name$"),
        CommandHandler("schedule", start_create_schedule),
        CommandHandler("newsched", start_create_schedule)
    ],
    states={
        SCHED_REPO_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_repo_name)
        ],
        SCHED_REPO_VISIBILITY: [
            CallbackQueryHandler(receive_sched_visibility, pattern="^sched_vis:"),
            CallbackQueryHandler(receive_use_existing_repo, pattern="^sched_use_existing:"),
            CallbackQueryHandler(start_create_schedule, pattern="^sched_retry_name$")
        ],
        SCHED_FILE_PATH: [
            CallbackQueryHandler(receive_sched_file_path, pattern="^sched_file_default$"),
            CommandHandler("default", receive_sched_file_path),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_file_path)
        ],
        SCHED_COMMIT_MSG: [
            CallbackQueryHandler(receive_sched_commit_msg, pattern="^sched_msg_default$"),
            CommandHandler("default", receive_sched_commit_msg),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_commit_msg)
        ],
        SCHED_TYPE: [
            CallbackQueryHandler(receive_sched_type, pattern="^stype_")
        ],
        SCHED_COMMITS_PER_DAY: [
            CallbackQueryHandler(receive_commits_per_day, pattern="^cpd_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_commits_per_day)
        ],
        SCHED_START_TIME: [
            CallbackQueryHandler(receive_start_time, pattern="^stime_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_start_time)
        ],
        SCHED_WEEKLY_PLANNER: [
            CallbackQueryHandler(receive_weekly_planner, pattern="^wp_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_weekly_planner)
        ],
        SCHED_TZ: [
            CallbackQueryHandler(receive_sched_tz_and_save, pattern="^tz_default$"),
            CommandHandler("default", receive_sched_tz_and_save),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_tz_and_save)
        ]
    },
    fallbacks=[
        CallbackQueryHandler(show_scheduler_menu, pattern="^nav_scheduler$"),
        CommandHandler("cancel", cancel_schedule_wizard)
    ],
    per_message=False
)
