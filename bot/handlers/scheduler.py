import logging
import pytz
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
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

logger = logging.getLogger(__name__)

(
    SCHED_SELECT_REPO,
    SCHED_FILE_PATH,
    SCHED_COMMIT_MSG,
    SCHED_CONTENT,
    SCHED_TYPE,
    SCHED_COMMITS_PER_DAY,
    SCHED_START_TIME,
    SCHED_WEEKLY_PLANNER,
    SCHED_CRON,
    SCHED_TZ
) = range(10)

def parse_start_time_str(time_str: str) -> Optional[str]:
    """Parses flexible user inputs into 24-hour HH:MM format (e.g. 09:00 AM -> 09:00)."""
    clean = time_str.strip().lower()
    if clean in ["now", "immediately", "start immediately"]:
        return "now"
    formats = ["%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M", "%H"]
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt).strftime("%H:%M")
        except ValueError:
            pass
    return None

def parse_weekly_planner_input(text: str) -> Dict[str, int]:
    """Parses flexible weekly commit plan input into standard day dict."""
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
    if not token:
        await safe_edit_or_reply(update, "Session expired. Please reconnect your account.")
        return

    schedules = await get_user_schedules(telegram_id)
    
    if not schedules:
        text = (
            "⏰ <b>Scheduled Commits</b>\n\n"
            "You have no active commit schedules.\n"
            "Create scheduled commits for legitimate automated documentation updates, status files, or contribution optimization."
        )
    else:
        text = f"⏰ <b>Scheduled Commits Dashboard</b> ({len(schedules)} total):"

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
        stype_display = "🟢 DEEP_GREEN (24 Commits/Day)"
        cron_display = "Every 60 mins"
    else:
        stype_display = stype.upper()
        cron_display = f"<code>{sched.get('cron_expression', 'N/A')}</code>"

    text = (
        f"⏰ <b>Commit Schedule Details</b>\n\n"
        f"🆔 <b>Schedule ID:</b> <code>{schedule_id}</code>\n"
        f"📦 <b>Repo:</b> {sched['repo']}\n"
        f"📄 <b>File Path:</b> <code>{sched['file_path']}</code>\n"
        f"💬 <b>Commit Message:</b> {sched['commit_message']}\n"
        f"⚙️ <b>Schedule Type:</b> {stype_display}\n"
        f"⏱ <b>Frequency / Plan:</b> {cron_display}\n"
        f"🌐 <b>Timezone:</b> {user_tz}\n"
        f"🟢 <b>Status:</b> {status_str}\n\n"
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
        elif query.data.startswith("repo_sched:"):
            repo_full_name = query.data.split("repo_sched:")[1]
            context.user_data["sched_repo"] = repo_full_name
            return await receive_sched_repo(update, context)
    
    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Session expired. Please reconnect your account.")
        return ConversationHandler.END

    repos = await get_cached_repositories(telegram_id)
    if not repos:
        client = GitHubAPIClient(token)
        try:
            live_repos = await client.get_user_repos(per_page=100)
            if live_repos:
                from bot.database.mongodb import save_cached_repositories
                await save_cached_repositories(telegram_id, live_repos)
                repos = live_repos
        except Exception as e:
            logger.error(f"Error fetching live repos for user {telegram_id}: {e}")

    if not repos:
        await safe_edit_or_reply(update, "❌ No GitHub repositories found. Please create or connect a repository first!")
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

    text = (
        f"Selected repo: <b>{context.user_data.get('sched_repo', '')}</b>\n\n"
        f"Step 2: Enter target file path (e.g. <code>docs/status.md</code> or send /default for <code>ACTIVITY.md</code>):"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="sched_back_repo"), InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]])
    await safe_edit_or_reply(update, text=text, reply_markup=kb)
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

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="sched_back_file"), InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]])
    await update.message.reply_text(
        f"{status_note}\n\n"
        f"Step 3: Enter commit message (or send /default for 'Update activity log'):",
        parse_mode="HTML",
        reply_markup=kb
    )
    return SCHED_COMMIT_MSG

async def receive_sched_commit_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_text = update.message.text.strip()
    commit_msg = "Update activity log" if input_text == "/default" else input_text
    context.user_data["sched_commit_msg"] = commit_msg

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="sched_back_msg"), InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]])
    await update.message.reply_text(
        f"Commit message: <i>{commit_msg}</i>\n\nStep 4: Enter file content template (or send /default for automatic timestamp update):",
        parse_mode="HTML",
        reply_markup=kb
    )
    return SCHED_CONTENT

async def receive_sched_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    content_input = update.message.text.strip() if update.message else "/default"
    content = "# Scheduled Automated Commit\nLast updated: {{TIMESTAMP}}\n" if content_input == "/default" else content_input
    context.user_data["sched_content"] = content

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 Custom Commits/Day & Start Time", callback_data="stype_custom_daily")
        ],
        [
            InlineKeyboardButton("📆 Multi-Repo Weekly Planner (Set Repos & Days)", callback_data="stype_weekly_planner")
        ],
        [
            InlineKeyboardButton("🟢 Deep Green Mode (24 Commits/Day)", callback_data="stype_deep_green")
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="sched_back_content"),
            InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")
        ]
    ])

    if update.callback_query:
        await safe_edit_or_reply(update, text="Step 5: Select schedule frequency mode:", reply_markup=kb)
    else:
        await update.message.reply_text("Step 5: Select schedule frequency mode:", reply_markup=kb)
    return SCHED_TYPE

async def receive_sched_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                InlineKeyboardButton("⬅️ Back", callback_data="sched_back_type"),
                InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")
            ]
        ])
        msg = (
            "📊 <b>Step 5a: How many commits per day do you want?</b>\n\n"
            "Select an option below or type any custom number (e.g. <code>5</code>, <code>10</code>, <code>15</code>, <code>20</code>, <code>50</code>):"
        )
        await safe_edit_or_reply(update, text=msg, reply_markup=kb)
        return SCHED_COMMITS_PER_DAY
    elif stype == "weekly_planner":
        telegram_id = update.effective_user.id
        user = await get_user(telegram_id)
        user_tz = user.get("timezone", "Asia/Kolkata") if user else "Asia/Kolkata"
        try:
            loc_tz = pytz.timezone(user_tz)
        except Exception:
            loc_tz = pytz.timezone("Asia/Kolkata")

        now_loc = datetime.now(loc_tz)
        day_full = now_loc.strftime("%A")
        day_short = now_loc.strftime("%a")
        date_str = now_loc.strftime("%d %b %Y")

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚡ Auto-Balance All Repos (39/wk)", callback_data="wp_default")
            ],
            [
                InlineKeyboardButton("🟢 Balanced Plan (35/wk)", callback_data="wp_balanced"),
                InlineKeyboardButton("🔥 Heavy Rotation (105/wk)", callback_data="wp_heavy")
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="sched_back_type"),
                InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")
            ]
        ])
        
        plan_display = "(Mon: 5, Tue: 8, Wed: 10, Thu: 5, Fri: 8, Sat: 2, Sun: 1)".replace(
            f"{day_short}:", f"📌 {day_short}:"
        )
        
        msg = (
            f"📆 <b>Step 5a: Multi-Repo Weekly Planner</b>\n\n"
            f"📅 <b>Today is:</b> <code>{day_full}, {date_str}</code> ({user_tz})\n"
            f"Schedule commits and assign target repositories for each day of the week!\n\n"
            f"<b>Default Plan:</b> {plan_display}\n\n"
            f"Select a preset above or type your custom plan (e.g.:\n"
            f"• <code>Monday repo1 and repo2 2 commits, Tuesday repo6 4 commit, Wed repo1 and repo3 4 commits</code>\n"
            f"• <code>Mon repo1: 5, Tue repo2: 8, Wed repo3: 10, Thu repo1: 4, Fri repo2: 12, Sat repo3: 2, Sun repo1: 1</code>):"
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
    query = update.callback_query
    if query:
        await query.answer()
        cpd_str = query.data.split("cpd_")[1]
        cpd = int(cpd_str)
    else:
        text = update.message.text.strip()
        if not text.isdigit() or int(text) < 1 or int(text) > 1000:
            await update.message.reply_text("❌ Invalid number. Please enter a valid commit count (e.g. <code>10</code>):", parse_mode="HTML")
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
            InlineKeyboardButton("🌕 00:00 Midnight", callback_data="stime_00:00"),
            InlineKeyboardButton("🚀 Start Immediately", callback_data="stime_now")
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="sched_back_cpd"),
            InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")
        ]
    ])
    msg = (
        f"✅ Target set to <b>{cpd} Commits/Day</b> (Interval: 1 commit every {interval_minutes} mins).\n\n"
        f"🕒 <b>Step 5b: What time should commits start each day?</b>\n\n"
        f"Select a daily start time or type custom time (e.g. <code>09:00 AM</code>, <code>10:30 AM</code>, <code>02:00 PM</code>, <code>09:00 PM</code>, <code>00:00</code>):"
    )
    await safe_edit_or_reply(update, text=msg, reply_markup=kb)
    return SCHED_START_TIME

def parse_multi_repo_weekly_input(text: str, default_repo: str) -> Dict[str, Any]:
    """Parses flexible multi-repo weekly schedule inputs (e.g. Monday repo1, repo2 2 commits, Tuesday repo6 4 commits)."""
    default_days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_map = {
        "mon": "Mon", "monday": "Mon",
        "tue": "Tue", "tuesday": "Tue",
        "wed": "Wed", "wednesday": "Wed",
        "thu": "Thu", "thursday": "Thu",
        "fri": "Fri", "friday": "Fri",
        "sat": "Sat", "saturday": "Sat",
        "sun": "Sun", "sunday": "Sun"
    }
    
    clean = text.strip()
    result = {}
    
    segments = re.split(r'[,;\n]', clean)
    for seg in segments:
        seg_clean = seg.strip()
        if not seg_clean:
            continue
        
        matched_day = None
        for k, d in day_map.items():
            if re.search(rf'\b{k}\b', seg_clean, re.IGNORECASE):
                matched_day = d
                break
                
        num_match = re.search(r'\b(\d+)\b', seg_clean)
        count = int(num_match.group(1)) if num_match else 2
        
        repos_found = re.findall(r'[\w\-]+/[\w\-]+|repo\d+|[\w\-]+\-repo', seg_clean, re.IGNORECASE)
        if not repos_found:
            repos_found = [default_repo]
            
        if matched_day:
            result[matched_day] = {
                "count": count,
                "repos": list(dict.fromkeys(repos_found))
            }
            
    for d in default_days:
        if d not in result:
            result[d] = {"count": 2 if d not in ["Sat", "Sun"] else 1, "repos": [default_repo]}
            
    return result

async def receive_weekly_planner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)
    user_tz = user.get("timezone", "Asia/Kolkata") if user else "Asia/Kolkata"
    default_repo = context.user_data.get("sched_repo", "default/repo")
    try:
        loc_tz = pytz.timezone(user_tz)
    except Exception:
        loc_tz = pytz.timezone("Asia/Kolkata")

    now_loc = datetime.now(loc_tz)
    day_short = now_loc.strftime("%a")
    day_full = now_loc.strftime("%A")

    repos = await get_cached_repositories(telegram_id)
    repo_names = [r.get("full_name") for r in repos] if repos else [default_repo]
    if not repo_names:
        repo_names = [default_repo]

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

        multi_plan = {}
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for idx, d in enumerate(days):
            r1 = repo_names[idx % len(repo_names)]
            r2 = repo_names[(idx + 1) % len(repo_names)]
            selected = [r1, r2] if len(repo_names) > 1 and plan[d] > 2 else [r1]
            multi_plan[d] = {"count": plan[d], "repos": selected}
    else:
        text = update.message.text.strip()
        multi_plan = parse_multi_repo_weekly_input(text, default_repo)
        plan = {d: data["count"] for d, data in multi_plan.items()}

    context.user_data["sched_weekly_plan"] = plan
    context.user_data["sched_multi_repo_plan"] = multi_plan
    
    total_wk = sum(plan.values())
    today_target = plan.get(day_short, 5)
    today_repos = multi_plan.get(day_short, {}).get("repos", [default_repo])
    
    context.user_data["sched_interval"] = max(1, int((24 * 60) / max(1, today_target)))
    context.user_data["sched_start_time"] = "now"

    plan_formatted = "\n".join([
        f"{'📌 ' if d == day_short else '  '}{d}: {data['count']} commits on [{', '.join(data['repos'])}]"
        for d, data in multi_plan.items()
    ])
    
    msg = (
        f"✅ <b>Multi-Repo Weekly Plan Configured ({total_wk} Commits/Week):</b>\n\n"
        f"📅 <b>Today ({day_full}):</b> {today_target} commits target on <code>{', '.join(today_repos)}</code>\n\n"
        f"<b>Weekly Mapping:</b>\n"
        f"<code>{plan_formatted}</code>\n\n"
        f"Proceeding to timezone confirmation..."
    )
    
    if query:
        await safe_edit_or_reply(update, text=msg)
    else:
        await update.message.reply_text(msg, parse_mode="HTML")
        
    return await prompt_timezone(update, context, is_callback=bool(query))

async def receive_start_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        stime = query.data.split("stime_")[1]
    else:
        text = update.message.text.strip()
        parsed = parse_start_time_str(text)
        if not parsed:
            await update.message.reply_text(
                "❌ Invalid time format. Please enter a valid time (e.g. <code>09:00 AM</code>, <code>10:30 PM</code>, <code>14:00</code>, or send <code>now</code>):",
                parse_mode="HTML"
            )
            return SCHED_START_TIME
        stime = parsed

    context.user_data["sched_start_time"] = stime
    return await prompt_timezone(update, context, is_callback=bool(query))

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

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="sched_back_type"), InlineKeyboardButton("❌ Cancel", callback_data="nav_scheduler")]])
    msg = (
        f"Step 6: Confirm timezone for <b>{freq_desc}</b> (Default: <code>{user_tz}</code>).\n\n"
        f"Send /default to use default or type a valid timezone (e.g. <code>Asia/Kolkata</code>, <code>America/New_York</code>):"
    )
    
    await safe_edit_or_reply(update, text=msg, reply_markup=kb)
    return SCHED_TZ

async def receive_sched_tz_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz_input = update.message.text.strip() if update.message else "/default"
    telegram_id = update.effective_user.id
    user = await get_user(telegram_id)
    user_tz = user.get("timezone", "Asia/Kolkata") if user else "Asia/Kolkata"

    tz_final = user_tz if tz_input == "/default" else tz_input

    repo = context.user_data["sched_repo"]
    file_path = context.user_data["sched_file_path"]
    commit_msg = context.user_data["sched_commit_msg"]
    content = context.user_data["sched_content"]
    stype = context.user_data["sched_type"]
    cron = context.user_data.get("sched_cron", "")
    interval = context.user_data.get("sched_interval")
    cpd = context.user_data.get("sched_cpd")
    stime = context.user_data.get("sched_start_time", "now")
    weekly_plan = context.user_data.get("sched_weekly_plan")
    multi_repo_plan = context.user_data.get("sched_multi_repo_plan")

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
            multi_repo_plan=multi_repo_plan,
            user_tz=tz_final
        )

        if stype == "weekly_planner" and weekly_plan:
            mode_desc = f"📆 <b>Multi-Repo Weekly Planner ({sum(weekly_plan.values())} Commits/Week)</b>"
            if multi_repo_plan:
                plan_str = ", ".join([f"{d}:{data['count']} ({','.join(data['repos'])})" for d, data in multi_repo_plan.items()])
            else:
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
            f"🎉 <b>Commit Schedule Created!</b>\n\n"
            f"🆔 <b>Schedule ID:</b> <code>{s_id}</code>\n"
            f"{mode_desc}\n"
            f"• <b>Repo:</b> {repo}\n"
            f"• <b>File:</b> <code>{file_path}</code>\n"
            f"{freq_desc}"
            f"• <b>Timezone:</b> {tz_final}\n"
        )
        
        reply_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏰ View Schedule", callback_data=f"sched_view:{s_id}")],
            [InlineKeyboardButton("⏰ All Schedules", callback_data="nav_scheduler")],
            [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
        ])

        if update.callback_query:
            await safe_edit_or_reply(update, text=success_text, reply_markup=reply_kb)
        else:
            await update.message.reply_text(success_text, parse_mode="HTML", reply_markup=reply_kb)
    except Exception as e:
        err_text = f"❌ Failed to create schedule: {e}"
        if update.callback_query:
            await safe_edit_or_reply(update, text=err_text)
        else:
            await update.message.reply_text(err_text)

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
            CallbackQueryHandler(start_create_schedule, pattern="^sched_back_repo$"),
            CommandHandler("default", receive_sched_file_path),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_file_path)
        ],
        SCHED_COMMIT_MSG: [
            CallbackQueryHandler(receive_sched_repo, pattern="^sched_back_file$"),
            CommandHandler("default", receive_sched_commit_msg),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_commit_msg)
        ],
        SCHED_CONTENT: [
            CallbackQueryHandler(receive_sched_file_path, pattern="^sched_back_msg$"),
            CommandHandler("default", receive_sched_content),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_content)
        ],
        SCHED_TYPE: [
            CallbackQueryHandler(receive_sched_commit_msg, pattern="^sched_back_content$"),
            CallbackQueryHandler(receive_sched_type, pattern="^stype_")
        ],
        SCHED_COMMITS_PER_DAY: [
            CallbackQueryHandler(receive_sched_content, pattern="^sched_back_type$"),
            CallbackQueryHandler(receive_commits_per_day, pattern="^cpd_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_commits_per_day)
        ],
        SCHED_START_TIME: [
            CallbackQueryHandler(receive_sched_type, pattern="^sched_back_cpd$"),
            CallbackQueryHandler(receive_start_time, pattern="^stime_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_start_time)
        ],
        SCHED_WEEKLY_PLANNER: [
            CallbackQueryHandler(receive_sched_content, pattern="^sched_back_type$"),
            CallbackQueryHandler(receive_weekly_planner, pattern="^wp_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_weekly_planner)
        ],
        SCHED_CRON: [
            CallbackQueryHandler(receive_sched_content, pattern="^sched_back_type$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_cron)
        ],
        SCHED_TZ: [
            CallbackQueryHandler(receive_sched_content, pattern="^sched_back_type$"),
            CommandHandler("default", receive_sched_tz_and_save),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sched_tz_and_save)
        ]
    },
    fallbacks=[CallbackQueryHandler(show_scheduler_menu, pattern="^nav_scheduler$")],
    per_message=False
)
