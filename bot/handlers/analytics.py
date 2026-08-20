import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.services.auth_service import get_user_decrypted_token
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.database.mongodb import get_cached_repositories
from bot.keyboards.inline import back_cancel_keyboard
from bot.utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)

async def activity_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renders user activity calendar & statistics."""
    query = update.callback_query
    if query:
        await query.answer()
    
    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        await safe_edit_or_reply(update, "Session expired.")
        return

    username = user["github_username"]
    client = GitHubAPIClient(token)

    try:
        events = await client.get_user_events(username)
        
        # Analyze event activity
        event_types = {}
        push_events_count = 0
        repo_commits_count = 0
        
        for e in events:
            t = e.get("type", "Other")
            event_types[t] = event_types.get(t, 0) + 1
            if t == "PushEvent":
                push_events_count += 1
                payload_commits = e.get("payload", {}).get("commits", [])
                repo_commits_count += len(payload_commits)

        # Weekly breakdown
        recent_summary = ""
        for t, count in list(event_types.items())[:5]:
            recent_summary += f"• <b>{t}:</b> {count}\n"

        activity_text = (
            f"📅 <b>GITHUB ACTIVITY CALENDAR</b>\n"
            f"👤 <b>User:</b> @{username}\n\n"
            f"<b>Recent Event Breakdown (Last 30 Events):</b>\n"
            f"{recent_summary or 'No recent events recorded.'}\n"
            f"📤 <b>Push Events:</b> {push_events_count}\n"
            f"💻 <b>Total Commits Pushed:</b> {repo_commits_count}\n\n"
            f"<i>Activity metrics computed directly from GitHub API.</i>"
        )
        
        markup = back_cancel_keyboard("main_menu")
        await safe_edit_or_reply(update, text=activity_text, reply_markup=markup)
    except GitHubAPIException as e:
        await safe_edit_or_reply(update, f"❌ Error fetching activity analytics: {e}")

async def repo_analytics_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Shows repository selector for traffic analytics."""
    query = update.callback_query
    if query:
        await query.answer()
        if query.data.startswith("analytics_page:"):
            page = int(query.data.split(":")[1])
    
    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await safe_edit_or_reply(update, "Session expired.")
        return

    repos = await get_cached_repositories(telegram_id)
    if not repos:
        client = GitHubAPIClient(token)
        try:
            repos = await client.list_repositories(page=1, per_page=100)
        except Exception as e:
            await safe_edit_or_reply(update, f"❌ Error listing repositories: {e}")
            return

    per_page = 8
    total_repos = len(repos)
    total_pages = max(1, (total_repos + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    page_repos = repos[start_idx:start_idx + per_page]

    keyboard = []
    for r in page_repos:
        name = r.get("full_name")
        keyboard.append([InlineKeyboardButton(f"📊 {name}", callback_data=f"repo_analytics:{name}")])
        
    # Pagination
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"analytics_page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="ignore"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"analytics_page:{page + 1}"))
    
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")])
    markup = InlineKeyboardMarkup(keyboard)

    text = f"👁️ <b>Repository Analytics</b>\n\nSelect a repository (Page {page}/{total_pages}):"

    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def repo_analytics_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays views, unique visitors, clones, stargazers, forks for a repository."""
    query = update.callback_query
    await query.answer()
    
    repo_full_name = query.data.split("repo_analytics:")[1]
    owner, repo_name = repo_full_name.split("/")

    telegram_id = update.effective_user.id
    token, _ = await get_user_decrypted_token(telegram_id)
    if not token:
        await query.edit_message_text("Session expired.")
        return

    client = GitHubAPIClient(token)
    
    views_count = "N/A"
    uniques_views = "N/A"
    clones_count = "N/A"
    uniques_clones = "N/A"
    traffic_accessible = True

    try:
        # Fetch traffic views
        try:
            views_data = await client.get_repo_views(owner, repo_name)
            views_count = views_data.get("count", 0)
            uniques_views = views_data.get("uniques", 0)
        except GitHubAPIException as ge:
            if ge.status_code == 403:
                traffic_accessible = False

        # Fetch traffic clones
        if traffic_accessible:
            try:
                clones_data = await client.get_repo_clones(owner, repo_name)
                clones_count = clones_data.get("count", 0)
                uniques_clones = clones_data.get("uniques", 0)
            except GitHubAPIException:
                pass

        # Fetch general repo info
        repo = await client.get_repository(owner, repo_name)
        stars = repo.get("stargazers_count", 0)
        forks = repo.get("forks_count", 0)
        watchers = repo.get("watchers_count", 0)

        traffic_notice = ""
        if not traffic_accessible:
            traffic_notice = "\n\n⚠️ <i>Traffic metrics (views/clones) require push/admin access on this repository.</i>"

        text = (
            f"👁️ <b>REPOSITORY ANALYTICS</b>\n"
            f"📦 <b>{repo_full_name}</b>\n"
            f"<i>Reporting Period: Last 14 Days</i>\n\n"
            f"👀 <b>Total Views:</b> {views_count} ({uniques_views} unique visitors)\n"
            f"📥 <b>Total Clones:</b> {clones_count} ({uniques_clones} unique cloners)\n"
            f"⭐ <b>Stars:</b> {stars}\n"
            f"🍴 <b>Forks:</b> {forks}\n"
            f"👁️ <b>Watchers:</b> {watchers}"
            f"{traffic_notice}"
        )
        
        markup = back_cancel_keyboard(f"repo_detail:{repo_full_name}")
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=markup)
    except GitHubAPIException as e:
        await query.edit_message_text(f"❌ Error fetching analytics: {e}")
