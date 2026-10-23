import os
import time
import random
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ChatJoinRequestHandler
from bot.services.auth_service import get_user_decrypted_token, generate_oauth_url
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.database.mongodb import get_user_schedules, update_user_settings, get_user
from bot.keyboards.inline import auth_keyboard, main_dashboard_keyboard
from bot.utils.helpers import safe_edit_or_reply

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
    """Sends clean force subscription prompt with random banner photo."""
    first_name = update.effective_user.first_name if (update and update.effective_user) else "User"
    banner_url = get_random_banner()
    
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

    eff_msg = update.effective_message
    
    # Try sending photo message first
    try:
        if update.callback_query:
            query = update.callback_query
            try:
                await query.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=markup)
                return
            except Exception:
                pass
        
        if eff_msg:
            await eff_msg.reply_photo(photo=banner_url, caption=text, parse_mode="HTML", reply_markup=markup)
            return
    except Exception as e:
        logger.warning(f"Could not send force-sub banner photo ({e}), falling back to text.")

    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def render_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, telegram_id: int, user: dict, token: str):
    """Renders main dashboard for authenticated user focusing on Commit Scheduler."""
    # 1. Check Forced Channel Subscription
    is_joined, missing = await check_all_force_subs(context.bot, telegram_id)
    if not is_joined:
        await send_force_sub_prompt(update, missing)
        return

    client = GitHubAPIClient(token)
    username = user.get("github_username", "Unknown")
    auth_method = user.get("auth_method", "oauth").upper()
    avatar_url = user.get("avatar_url") or DEFAULT_AVATAR_URL

    try:
        # Validate token with lightweight profile check
        profile = await client.get_user_profile()
        if profile and isinstance(profile, dict):
            avatar_url = profile.get("avatar_url") or avatar_url
            await update_user_settings(telegram_id, {"avatar_url": avatar_url})
    except GitHubAPIException as ge:
        if getattr(ge, "status_code", None) == 401:
            oauth_url = generate_oauth_url(telegram_id)
            await safe_edit_or_reply(
                update,
                text="❌ Your GitHub access token has expired or been revoked. Please reconnect your account.",
                reply_markup=auth_keyboard(oauth_url)
            )
            return
    except Exception as e:
        logger.warning(f"Error checking profile for user {telegram_id}: {e}")

    schedules = await get_user_schedules(telegram_id)
    active_sched_count = sum(1 for s in schedules if s.get("status") == "active")
    total_sched_count = len(schedules)

    dashboard_text = (
        f"🐙 <b>GITHUB COMMIT SCHEDULER</b>\n\n"
        f"👤 <b>Connected As:</b> @{username}\n"
        f"🔐 <b>Auth:</b> {auth_method}\n"
        f"⏰ <b>Active Schedules:</b> {active_sched_count} / {total_sched_count}\n"
        f"🌐 <b>Timezone:</b> {user.get('timezone', 'Asia/Kolkata')}\n\n"
        f"<i>Automated commits to your dedicated repository.\n"
        f"Click <b>⏰ Commit Scheduler</b> to create a repository and set up your schedule!</i>"
    )

    reply_markup = main_dashboard_keyboard()
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
    """Renders user guide for commit scheduler."""
    guide_text = (
        "📖 <b>GITHUB COMMIT SCHEDULER - USER GUIDE</b>\n\n"
        "This bot is dedicated exclusively to <b>Automated GitHub Commit Scheduling</b>.\n"
        "It will never touch, read, or track your personal repositories.\n\n"
        "1️⃣ <b>CONNECT GITHUB</b>\n"
        "• Connect securely via <b>OAuth</b> or your personal access token (<b>PAT</b>).\n\n"
        "2️⃣ <b>CREATE SCHEDULE REPOSITORY</b>\n"
        "• Click <b>⏰ Commit Scheduler</b> ➔ <b>➕ New Schedule</b>.\n"
        "• You will be prompted to create a new dedicated GitHub repository.\n"
        "• Choose whether your repository should be <b>Public</b> or <b>Private</b>.\n"
        "• The bot creates this repository on GitHub automatically.\n\n"
        "3️⃣ <b>CONFIGURE COMMIT SCHEDULE</b>\n"
        "• Choose target file path (default: <code>ACTIVITY.md</code>).\n"
        "• Enter commit message (default: <code>Update activity log</code>).\n"
        "• Select frequency:\n"
        "   - <b>Custom Commits / Day:</b> Specify commits/day (e.g. 5, 10, 24) and start time.\n"
        "   - <b>Deep Green Mode:</b> 24 commits/day (1 every hour).\n"
        "   - <b>Weekly Planner:</b> Set custom commits per day of the week.\n\n"
        "4️⃣ <b>AUTOMATED EXECUTION</b>\n"
        "• Commits are pushed automatically strictly to your dedicated schedule repository!\n"
        "• Pause, resume, or delete any schedule anytime in the scheduler menu."
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Main Dashboard", callback_data="main_menu")]
    ])

    if update.callback_query:
        await update.callback_query.answer()

    await safe_edit_or_reply(update, text=guide_text, reply_markup=markup)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles callback for returning to main menu."""
    query = update.callback_query
    if query:
        await query.answer()
        
    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    
    if token and user:
        await render_dashboard(update, context, telegram_id, user, token)
    else:
        oauth_url = generate_oauth_url(telegram_id)
        welcome_text = "🐙 <b>Welcome to GitHub Guardian!</b>\n\nPlease connect your GitHub account:"
        await safe_edit_or_reply(update, text=welcome_text, reply_markup=auth_keyboard(oauth_url))
