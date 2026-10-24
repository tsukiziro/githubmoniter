import asyncio
import logging
import time
from typing import Optional
from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.services.auth_service import get_user_decrypted_token
from bot.services.achievement_service import AchievementService
from bot.keyboards.inline import achievements_menu_keyboard, achievements_completed_keyboard, auth_keyboard
from bot.services.auth_service import generate_oauth_url
from bot.utils.helpers import safe_edit_or_reply

logger = logging.getLogger(__name__)

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

def render_progress_bar(percent: int, length: int = 10) -> str:
    """Renders a visual text progress bar, e.g. [■■■■□□□□□□] 40%"""
    clamped = max(0, min(100, percent))
    filled_len = int(round(length * clamped / 100))
    bar = "■" * filled_len + "□" * (length - filled_len)
    return f"[{bar}] {clamped}%"

async def show_achievements_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the achievements catalog menu."""
    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        oauth_url = generate_oauth_url(telegram_id)
        await safe_edit_or_reply(
            update,
            text="❌ Please connect your GitHub account first to unlock profile achievements.",
            reply_markup=auth_keyboard(oauth_url)
        )
        return

    username = user.get("github_username", "Unknown")

    text = (
        f"🏆 <b>GITHUB PROFILE ACHIEVEMENTS FARMER</b>\n\n"
        f"👤 <b>Account:</b> @{username}\n"
        f"🛡️ <b>Anti-Bot Guard:</b> Active (Humanized 2.5s–4.5s pacing)\n"
        f"📦 <b>Dedicated Repo:</b> <code>free-achievements</code> (Public)\n\n"
        f"<i>Select an achievement to unlock, or tap <b>⚡ UNLOCK ALL</b> to unlock every badge automatically!</i>\n\n"
        f"• 🚀 <b>Quickdraw:</b> Close issue within 5 min\n"
        f"• 🤠 <b>YOLO:</b> Merge PR without review\n"
        f"• 👥 <b>Pair Extraordinaire:</b> Co-authored commit merge\n"
        f"• 🧠 <b>Galaxy Brain:</b> Accepted Q&A discussion answer\n"
        f"• 🦈 <b>Pull Shark:</b> Multi-PR automated merge\n"
        f"• ❤️ <b>Heart on Sleeve:</b> 10+ issue reactions\n"
        f"• 👀 <b>Public Sighting:</b> Maintainer public mention\n"
        f"• 💬 <b>First Interaction:</b> Community issue comment\n"
        f"• 🌱 <b>Open Source Heart:</b> Hacktoberfest contribution\n"
        f"• 🌐 <b>GitHub Pages:</b> Live site deployment"
    )

    markup = achievements_menu_keyboard()
    await safe_edit_or_reply(update, text=text, reply_markup=markup)

async def handle_achievement_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes single achievement or unlock-all with live animated progress updates."""
    query = update.callback_query
    if query:
        await query.answer()

    telegram_id = update.effective_user.id
    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        await safe_edit_or_reply(update, "Session expired. Please reconnect your account.")
        return

    username = user.get("github_username", "Unknown")
    action = query.data

    is_all = action == "ach_run_all"
    achievement_key = action.split("ach_run:")[1] if not is_all else "all"

    # Display initial animation frame
    frame_idx = 0
    start_time = time.time()
    last_edit_time = 0.0

    async def progress_listener(percent: int, detail: str):
        nonlocal frame_idx, last_edit_time
        now = time.time()
        # Avoid Telegram flood limit (throttle edits to at least 1.2s intervals unless 100%)
        if percent < 100 and (now - last_edit_time < 1.2):
            return
            
        last_edit_time = now
        spinner = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
        frame_idx += 1
        bar = render_progress_bar(percent)

        elapsed = int(now - start_time)
        anim_text = (
            f"⚡ <b>FARMING GITHUB ACHIEVEMENTS</b>\n\n"
            f"{spinner} {bar}\n"
            f"⏱ <b>Elapsed:</b> {elapsed}s\n"
            f"🛡️ <b>Anti-Bot Delay:</b> 2.5s–4.5s safe pacing active\n\n"
            f"<b>Status:</b> {detail}\n\n"
            f"<i>Please wait, executing actions safely on GitHub...</i>"
        )
        try:
            await query.edit_message_text(anim_text, parse_mode="HTML")
        except Exception:
            pass

    service = AchievementService(token=token, username=username, progress_callback=progress_listener)

    try:
        if is_all:
            results = await service.run_all()
            total_success = sum(1 for v in results.values() if v)
            summary_lines = []
            for k, ok in results.items():
                icon = "✅" if ok else "⚠️"
                summary_lines.append(f"{icon} <code>{k}</code>")
            details_str = "\n".join(summary_lines)

            finish_text = (
                f"🎉 <b>ALL ACHIEVEMENTS COMPLETED!</b>\n\n"
                f"👤 <b>Account:</b> @{username}\n"
                f"📊 <b>Unlocked:</b> {total_success}/{len(results)} badges processed\n\n"
                f"{details_str}\n\n"
                f"🔗 <a href='https://github.com/{username}?tab=achievements'>View Profile Badges on GitHub</a>\n\n"
                f"<i>Note: GitHub can take 1–5 minutes to index and display badges on your public profile.</i>"
            )
        else:
            success = await service.run_single(achievement_key)
            status_word = "✅ <b>Successfully Unlocked!</b>" if success else "⚠️ <b>Action Executed</b> (GitHub is indexing)"
            finish_text = (
                f"🎉 <b>ACHIEVEMENT PROCESSED!</b>\n\n"
                f"🏆 <b>Badge:</b> <code>{achievement_key.upper()}</code>\n"
                f"Status: {status_word}\n"
                f"👤 <b>Account:</b> @{username}\n"
                f"📦 <b>Repo:</b> <code>https://github.com/{username}/free-achievements</code>\n\n"
                f"🔗 <a href='https://github.com/{username}?tab=achievements'>Check Your Profile Badges</a>\n\n"
                f"<i>Note: Badges typically appear on your profile within a few minutes!</i>"
            )

        markup = achievements_completed_keyboard()
        await query.edit_message_text(finish_text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Error executing achievements for user {telegram_id}: {e}", exc_info=True)
        err_markup = achievements_completed_keyboard()
        await query.edit_message_text(
            f"❌ <b>Error processing achievement:</b> {e}\n\nPlease try again shortly.",
            parse_mode="HTML",
            reply_markup=err_markup
        )
