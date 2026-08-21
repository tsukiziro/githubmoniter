import os
import time
import random
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ChatJoinRequestHandler
from bot.services.auth_service import get_user_decrypted_token, generate_oauth_url
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.database.mongodb import get_user_schedules, cache_user_repositories, update_user_settings, get_user
from bot.keyboards.inline import auth_keyboard, main_dashboard_keyboard

logger = logging.getLogger(__name__)

# Curated High-Quality Developer/GitHub Banner Images (Fallback)
DEFAULT_WELCOME_IMAGES = [
    "https://images.unsplash.com/photo-1618401471353-b98afee0b2eb?q=80&w=1000",
    "https://images.unsplash.com/photo-1555066931-4365d14bab8c?q=80&w=1000",
    "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?q=80&w=1000",
    "https://images.unsplash.com/photo-1542831371-29b0f74f9713?q=80&w=1000",
    "https://images.unsplash.com/photo-1517694712202-14dd9538aa97?q=80&w=1000"
]

DEFAULT_AVATAR_URL = "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png"

# In-Memory Cache for Force-Sub verifications (TTL = 60s)
FORCE_SUB_CACHE = {}

def get_random_banner() -> str:
    """Randomly selects a banner image from env variable WELCOME_IMAGE_URLS or fallback list."""
    env_urls_raw = os.getenv("WELCOME_IMAGE_URLS", os.getenv("WELCOME_IMAGES", "")).strip()
    if env_urls_raw:
        custom_urls = [
            u.strip(" \"'") for u in env_urls_raw.split(",") 
            if u.strip(" \"'").startswith("http://") or u.strip(" \"'").startswith("https://")
        ]
        if custom_urls:
            return random.choice(custom_urls)
    return random.choice(DEFAULT_WELCOME_IMAGES)

async def auto_approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically approves incoming channel/group join requests."""
    try:
        req = update.chat_join_request
        if req:
            await req.approve()
            logger.info(f"Auto-approved join request for user {req.from_user.id} in chat {req.chat.id}")
    except Exception as e:
        logger.warning(f"Failed to auto-approve join request: {e}")

async def check_all_force_subs(bot, telegram_id: int) -> tuple[bool, list]:
    """
    Checks membership across multiple channels/groups (cached for 60s).
    Supports env format: MUST_JOIN_CHANNELS=@channel1, -100123456789|https://t.me/+PrivateLink, @channel2
    Returns: (is_all_joined, missing_channels_list)
    """
    raw_channels = os.getenv("MUST_JOIN_CHANNELS", os.getenv("MUST_JOIN_CHANNEL", "")).strip()
    if not raw_channels:
        return True, []

    now = time.time()
    if telegram_id in FORCE_SUB_CACHE:
        cached_time, cached_res = FORCE_SUB_CACHE[telegram_id]
        if now - cached_time < 60: # 60 seconds TTL cache
            return cached_res
        
    channel_entries = [c.strip() for c in raw_channels.split(",") if c.strip()]
    missing = []
    
    for entry in channel_entries:
        if "|" in entry:
            chat_identifier, invite_url = entry.split("|", 1)
        else:
            chat_identifier = entry
            invite_url = f"https://t.me/{entry.lstrip('@')}" if entry.startswith("@") else entry
            
        chat_identifier = chat_identifier.strip()
        invite_url = invite_url.strip()

        try:
            member = await bot.get_chat_member(chat_id=chat_identifier, user_id=telegram_id)
            if member.status not in ["creator", "administrator", "member"]:
                missing.append({
                    "chat_id": chat_identifier,
                    "url": invite_url,
                    "name": chat_identifier if chat_identifier.startswith("@") else "Official Group / Channel"
                })
        except Exception as e:
            logger.warning(f"Could not verify channel membership for {telegram_id} in {chat_identifier}: {e}")

    result = (len(missing) == 0), missing
    if result[0]:
        FORCE_SUB_CACHE[telegram_id] = (now, result)
    return result

async def send_force_sub_prompt(update: Update, missing_channels: list):
    """Sends clean force subscription prompt matching exact user design."""
    first_name = update.effective_user.first_name if (update and update.effective_user) else "User"
    
    keyboard = []
    row = []
    for idx, ch in enumerate(missing_channels, start=1):
        row.append(InlineKeyboardButton(f"Join Channel {idx}", url=ch['url']))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("♻️ Try Again", callback_data="verify_sub")])
    markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"Hey <b>{first_name}</b>\n\n"
        f"<i>Please Join All My Update Channels To Use Me!</i>"
    )

    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def render_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, telegram_id: int, user: dict, token: str):
    """Renders main dashboard for authenticated user with GitHub profile avatar photo."""
    # 1. Check Forced Channel Subscription
    is_joined, missing = await check_all_force_subs(context.bot, telegram_id)
    if not is_joined:
        await send_force_sub_prompt(update, missing)
        return

    client = GitHubAPIClient(token)
    
    username = user.get("github_username", "Unknown")
    auth_method = user.get("auth_method", "oauth").upper()
    notifications = user.get("notifications", True)
    notif_status = "ON" if notifications else "OFF"
    avatar_url = user.get("avatar_url") or DEFAULT_AVATAR_URL
    
    total_repos = 0
    total_stars = 0
    total_forks = 0
    recent_activity = "No recent activity recorded."
    
    try:
        # Execute parallel requests for maximum speed
        tasks = [
            client.list_repositories(page=1, per_page=100),
            client.get_user_events(username)
        ]
        if not user.get("avatar_url"):
            tasks.append(client.get_user_profile())

        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check if 401 Unauthorized occurred
        for res in results:
            if isinstance(res, GitHubAPIException) and getattr(res, "status_code", None) == 401:
                oauth_url = generate_oauth_url(telegram_id)
                await safe_edit_or_reply(
                    update,
                    text="❌ Your GitHub access token has expired or been revoked. Please reconnect your account.",
                    reply_markup=auth_keyboard(oauth_url)
                )
                return
                
        repos = results[0] if len(results) > 0 and isinstance(results[0], list) else []
        events = results[1] if len(results) > 1 and isinstance(results[1], list) else []
        
        if len(results) > 2 and isinstance(results[2], dict):
            avatar_url = results[2].get("avatar_url", DEFAULT_AVATAR_URL)
            await update_user_settings(telegram_id, {"avatar_url": avatar_url})

        total_repos = len(repos)
        total_stars = sum(r.get("stargazers_count", 0) for r in repos)
        total_forks = sum(r.get("forks_count", 0) for r in repos)
        
        if repos:
            await cache_user_repositories(telegram_id, repos)

        if events:
            last_event = events[0]
            e_type = last_event.get("type", "Event")
            e_repo = last_event.get("repo", {}).get("name", "")
            recent_activity = f"{e_type} on {e_repo}"

    except Exception as e:
        logger.warning(f"Error fetching dashboard metrics for user {telegram_id}: {e}")

    schedules = await get_user_schedules(telegram_id)
    active_sched_count = sum(1 for s in schedules if s.get("status") == "active")

    dashboard_text = (
        f"🐙 <b>GITHUB GUARDIAN DASHBOARD</b>\n\n"
        f"👤 <b>Username:</b> @{username}\n"
        f"🔐 <b>Auth:</b> {auth_method}\n"
        f"📁 <b>Repositories:</b> {total_repos}\n"
        f"⭐ <b>Total Stars:</b> {total_stars}\n"
        f"🍴 <b>Total Forks:</b> {total_forks}\n\n"
        f"📅 <b>Recent Activity:</b> {recent_activity}\n"
        f"⏰ <b>Active Schedules:</b> {active_sched_count}\n"
        f"🔔 <b>Monitoring:</b> {notif_status}"
    )

    reply_markup = main_dashboard_keyboard(notifications)
    eff_msg = update.effective_message

    if update.callback_query:
        query = update.callback_query
        try:
            await query.edit_message_caption(caption=dashboard_text, parse_mode="HTML", reply_markup=reply_markup)
            return
        except Exception:
            try:
                await query.edit_message_text(text=dashboard_text, parse_mode="HTML", reply_markup=reply_markup)
                return
            except Exception:
                pass

    try:
        if eff_msg:
            await eff_msg.reply_photo(photo=avatar_url, caption=dashboard_text, parse_mode="HTML", reply_markup=reply_markup)
            return
    except Exception as e:
        logger.warning(f"Could not send dashboard photo ({e}), falling back to safe text.")

    await safe_edit_or_reply(update, text=dashboard_text, reply_markup=reply_markup)

async def verify_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback triggered when user clicks 'Verify Subscription'."""
    query = update.callback_query
    if query:
        await query.answer("Verifying subscription...")
    telegram_id = update.effective_user.id
    
    # Clear force sub cache to perform live check
    FORCE_SUB_CACHE.pop(telegram_id, None)
    
    is_joined, missing = await check_all_force_subs(context.bot, telegram_id)
    if not is_joined:
        await send_force_sub_prompt(update, missing)
        return

    token, user = await get_user_decrypted_token(telegram_id)
    if token and user:
        await render_dashboard(update, context, telegram_id, user, token)
    else:
        oauth_url = generate_oauth_url(telegram_id)
        welcome_text = (
            "🐙 <b>Subscription Verified!</b>\n\n"
            "Connect your GitHub account to manage and monitor your repositories securely."
        )
        await safe_edit_or_reply(update, text=welcome_text, reply_markup=auth_keyboard(oauth_url))

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point when user runs /start."""
    telegram_id = update.effective_user.id

    try:
        is_joined, missing = await check_all_force_subs(context.bot, telegram_id)
        if not is_joined:
            await send_force_sub_prompt(update, missing)
            return

        token, user = await get_user_decrypted_token(telegram_id)

        if not token or not user:
            oauth_url = generate_oauth_url(telegram_id)
            welcome_text = (
                "🐙 <b>Welcome to GitHub Guardian!</b>\n\n"
                "Connect your GitHub account to manage and monitor your repositories securely."
            )
            await safe_edit_or_reply(update, text=welcome_text, reply_markup=auth_keyboard(oauth_url))
        else:
            await render_dashboard(update, context, telegram_id, user, token)
    except Exception as e:
        logger.error(f"Error executing start_command for user {telegram_id}: {e}", exc_info=e)
        await safe_edit_or_reply(
            update,
            text=f"⚠️ <b>Service Temporarily Unavailable</b>\n\nCould not load dashboard: {e}"
        )

async def guide_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renders detailed user guide and feature overview."""
    guide_text = (
        "📖 <b>GITHUB GUARDIAN - USER GUIDE</b>\n\n"
        "Welcome! <b>GitHub Guardian</b> helps you manage, monitor, and automate your GitHub repositories directly from Telegram.\n\n"
        "1️⃣ <b>ACCOUNT AUTHENTICATION</b>\n"
        "• <b>OAuth Flow:</b> Click '🔗 Connect with GitHub OAuth', authorize via browser.\n"
        "• <b>PAT Flow:</b> Click '🔑 Use Personal Access Token', paste token. Your message is deleted instantly for security.\n\n"
        "2️⃣ <b>📁 REPOSITORY MANAGEMENT</b>\n"
        "• Run /repos or click <b>📁 Repositories</b> to view paginated repositories.\n"
        "• View stars, forks, watchers, open issues, and default branch.\n"
        "• Click <b>➕ Create Repo</b> to create public or private repositories.\n"
        "• Delete repositories safely using confirmation modals.\n\n"
        "3️⃣ <b>📤 PUSH FILES VIA TELEGRAM</b>\n"
        "• Upload any document, text file, photo, or code file directly to chat!\n"
        "• Select target repository, file path, branch, and commit message.\n"
        "• Works for creating new files or updating existing files (handles SHA automatically).\n\n"
        "4️⃣ <b>🟢 DEEP GREEN MODE & SCHEDULED COMMITS</b>\n"
        "• Go to <b>⏰ Scheduler</b> ➔ <b>➕ New Schedule</b>.\n"
        "• Select repository, file path (send /default for <code>ACTIVITY.md</code>), commit message, and content.\n"
        "• Select <b>🟢 Deep Green Mode (20 Commits/Day)</b> to automatically push dynamic commits every 72 minutes to keep your contribution matrix green!\n"
        "• View live 4-hour, 24-hour, and total commit execution stats anytime.\n\n"
        "5️⃣ <b>🐛 ISSUES & COLLABORATORS</b>\n"
        "• Select a repository ➔ Click <b>🐛 Issues</b> to create, close, reopen, or comment on issues.\n"
        "• Click <b>👥 Collaborators</b> to invite users with custom roles (read, write, admin, triage) or remove collaborators.\n\n"
        "6️⃣ <b>📊 TRAFFIC ANALYTICS & MONITORING</b>\n"
        "• Click <b>📊 Analytics</b> to view views, unique visitors, clones, stargazers, and forks.\n"
        "• Toggle real-time notifications under <b>⚙️ Settings</b> or main dashboard."
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
    ])

    if update.callback_query:
        query = update.callback_query
        try:
            await query.edit_message_caption(caption=guide_text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            try:
                await query.edit_message_text(text=guide_text, parse_mode="HTML", reply_markup=markup)
            except Exception:
                await update.effective_chat.send_message(text=guide_text, parse_mode="HTML", reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text=guide_text, parse_mode="HTML", reply_markup=markup)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles callback for returning to main menu."""
    query = update.callback_query
    await query.answer()
    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    
    if token and user:
        await render_dashboard(update, context, telegram_id, user, token)
    else:
        oauth_url = generate_oauth_url(telegram_id)
        welcome_text = "🐙 <b>Welcome to GitHub Guardian!</b>\n\nPlease connect your GitHub account:"
        try:
            await query.edit_message_caption(caption=welcome_text, parse_mode="HTML", reply_markup=auth_keyboard(oauth_url))
        except Exception:
            try:
                await query.edit_message_text(text=welcome_text, parse_mode="HTML", reply_markup=auth_keyboard(oauth_url))
            except Exception:
                await update.effective_chat.send_message(text=welcome_text, parse_mode="HTML", reply_markup=auth_keyboard(oauth_url))
