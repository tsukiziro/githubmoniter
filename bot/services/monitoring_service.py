import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from telegram import Bot
from bot.database.mongodb import MongoDB, get_user, get_monitoring_setting, log_webhook_event
from bot.services.auth_service import get_user_decrypted_token
from bot.services.github_api import GitHubAPIClient

logger = logging.getLogger(__name__)

# Polling interval in seconds (e.g. 180s = 3 min)
POLL_INTERVAL_SECONDS = 180

async def notify_telegram_user(bot: Bot, telegram_id: int, text: str):
    """Sends a formatted notification to a Telegram user if notifications are enabled."""
    user = await get_user(telegram_id)
    if not user or not user.get("notifications", True):
        return
    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Failed to send Telegram notification to user {telegram_id}: {e}")

async def process_github_webhook_event(bot_app, event_type: str, payload: Dict[str, Any]):
    """Processes incoming GitHub webhook payload and broadcasts to relevant user(s)."""
    await log_webhook_event(event_type, payload)
    
    if not bot_app or not hasattr(bot_app, "bot"):
        logger.warning("Bot instance unavailable in webhook processor.")
        return

    repo_full_name = payload.get("repository", {}).get("full_name")
    if not repo_full_name:
        return

    # Find users monitoring this repository
    db = MongoDB.get_db()
    cursor = db.monitoring_settings.find({"monitored_repos": repo_full_name})
    monitored_users = await cursor.to_list(length=100)

    message_text = None

    if event_type == "issues":
        action = payload.get("action")
        issue = payload.get("issue", {})
        sender = payload.get("sender", {}).get("login")
        message_text = (
            f"🔔 <b>GitHub Issue Notification</b>\n"
            f"<b>Repo:</b> {repo_full_name}\n"
            f"<b>Action:</b> {action.title()}\n"
            f"<b>Title:</b> #{issue.get('number')} {issue.get('title')}\n"
            f"<b>By:</b> {sender}\n"
            f"🔗 <a href='{issue.get('html_url')}'>View Issue</a>"
        )
    elif event_type == "issue_comment":
        action = payload.get("action")
        comment = payload.get("comment", {})
        issue = payload.get("issue", {})
        sender = payload.get("sender", {}).get("login")
        message_text = (
            f"💬 <b>New Issue Comment</b>\n"
            f"<b>Repo:</b> {repo_full_name}\n"
            f"<b>Issue:</b> #{issue.get('number')} {issue.get('title')}\n"
            f"<b>Commenter:</b> {sender}\n"
            f"<b>Comment:</b> {comment.get('body', '')[:150]}...\n"
            f"🔗 <a href='{comment.get('html_url')}'>View Comment</a>"
        )
    elif event_type == "pull_request":
        action = payload.get("action")
        pr = payload.get("pull_request", {})
        sender = payload.get("sender", {}).get("login")
        message_text = (
            f"🔀 <b>Pull Request Alert</b>\n"
            f"<b>Repo:</b> {repo_full_name}\n"
            f"<b>Action:</b> {action.title()}\n"
            f"<b>PR:</b> #{pr.get('number')} {pr.get('title')}\n"
            f"<b>By:</b> {sender}\n"
            f"🔗 <a href='{pr.get('html_url')}'>View Pull Request</a>"
        )
    elif event_type == "push":
        ref = payload.get("ref", "")
        branch = ref.split("/")[-1] if "/" in ref else ref
        pusher = payload.get("pusher", {}).get("name")
        commits = payload.get("commits", [])
        message_text = (
            f"📤 <b>New Push Event</b>\n"
            f"<b>Repo:</b> {repo_full_name}\n"
            f"<b>Branch:</b> {branch}\n"
            f"<b>Pusher:</b> {pusher}\n"
            f"<b>Commits Count:</b> {len(commits)}\n"
        )
        if commits:
            first_msg = commits[0].get("message", "").split("\n")[0]
            message_text += f"<b>Latest Commit:</b> {first_msg}\n"
    elif event_type == "workflow_run":
        action = payload.get("action")
        run = payload.get("workflow_run", {})
        conclusion = run.get("conclusion")
        if action == "completed" and conclusion == "failure":
            message_text = (
                f"🚨 <b>Workflow Action Failed!</b>\n"
                f"<b>Repo:</b> {repo_full_name}\n"
                f"<b>Workflow:</b> {run.get('name')}\n"
                f"<b>Branch:</b> {run.get('head_branch')}\n"
                f"🔗 <a href='{run.get('html_url')}'>View Failure Logs</a>"
            )
    elif event_type == "release":
        action = payload.get("action")
        release = payload.get("release", {})
        message_text = (
            f"🚀 <b>New Release ({action.title()})</b>\n"
            f"<b>Repo:</b> {repo_full_name}\n"
            f"<b>Tag:</b> {release.get('tag_name')}\n"
            f"<b>Title:</b> {release.get('name')}\n"
            f"🔗 <a href='{release.get('html_url')}'>View Release</a>"
        )
    elif event_type == "star":
        action = payload.get("action")
        sender = payload.get("sender", {}).get("login")
        message_text = (
            f"⭐ <b>Repository Star Event</b>\n"
            f"<b>Repo:</b> {repo_full_name}\n"
            f"<b>User:</b> {sender} ({action} star)"
        )

    if message_text:
        for u in monitored_users:
            await notify_telegram_user(bot_app.bot, u["telegram_id"], message_text)

# --- Periodic Polling Fallback Worker ---
async def start_periodic_monitoring_worker(bot_app):
    """Periodic task checking user activity/events for instant real-time Telegram notifications."""
    logger.info("Starting background periodic monitoring worker...")
    db = MongoDB.get_db()
    from bot.utils.helpers import format_user_datetime
    
    last_event_ids: Dict[int, str] = {}

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            # Find users with notifications enabled
            users_cursor = db.users.find({"notifications": True})
            async for user in users_cursor:
                telegram_id = user["telegram_id"]
                user_tz = user.get("timezone", "Asia/Kolkata")
                token, _ = await get_user_decrypted_token(telegram_id)
                if not token:
                    continue

                client = GitHubAPIClient(token)
                try:
                    events = await client.get_user_events(user["github_username"])
                    if not events or not isinstance(events, list):
                        continue

                    latest_event = events[0]
                    latest_id = str(latest_event.get("id"))
                    prev_id = last_event_ids.get(telegram_id)

                    if prev_id and prev_id != latest_id:
                        # Real-time event detected!
                        event_type = latest_event.get("type", "GitHub Event")
                        repo_name = latest_event.get("repo", {}).get("name", "Unknown Repo")
                        actor = latest_event.get("actor", {}).get("login", user["github_username"])
                        actor_display = f"@{actor}" if actor != user["github_username"] else f"@{actor} (You)"
                        
                        created_at = latest_event.get("created_at")
                        time_formatted = format_user_datetime(created_at, user_tz)
                        
                        payload_info = ""
                        payload = latest_event.get("payload", {})
                        if event_type == "PushEvent":
                            commits = payload.get("commits", [])
                            commit_msg = commits[0].get("message", "New Commit") if commits else "Push event"
                            payload_info = f"\n💬 <b>Commit:</b> <i>{commit_msg}</i>\n📊 <b>Commits:</b> {len(commits)}"
                        elif event_type == "IssuesEvent":
                            issue = payload.get("issue", {})
                            payload_info = f"\n🐛 <b>Issue:</b> #{issue.get('number')} {issue.get('title')}"
                        elif event_type == "CreateEvent":
                            ref_type = payload.get("ref_type", "branch/repo")
                            payload_info = f"\n✨ <b>Created:</b> {ref_type}"
                        elif event_type == "WatchEvent":
                            payload_info = f"\n⭐ <b>Starred repository!</b>"
                        elif event_type == "PullRequestEvent":
                            pr = payload.get("pull_request", {})
                            payload_info = f"\n🔀 <b>Pull Request:</b> #{pr.get('number')} {pr.get('title')}"

                        msg = (
                            f"⚡ <b>LIVE GITHUB ACTIVITY UPDATE</b>\n\n"
                            f"👤 <b>Performed By:</b> {actor_display}\n"
                            f"🔔 <b>Event:</b> <code>{event_type}</code>\n"
                            f"📦 <b>Repository:</b> <code>{repo_name}</code>{payload_info}\n"
                            f"🕒 <b>Time:</b> {time_formatted}\n\n"
                            f"🔗 <a href='https://github.com/{repo_name}'>View Repository on GitHub</a>"
                        )
                        await notify_telegram_user(bot_app.bot, telegram_id, msg)
                    
                    last_event_ids[telegram_id] = latest_id
                except Exception as ex:
                    logger.debug(f"Polling error for user {telegram_id}: {ex}")
        except asyncio.CancelledError:
            logger.info("Periodic monitoring worker stopped.")
            break
        except Exception as e:
            logger.error(f"Error in periodic monitoring worker loop: {e}")
