import logging
import pytz
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from bot.database.mongodb import (
    save_schedule,
    get_user_schedules,
    get_all_active_schedules,
    update_schedule_status,
    delete_schedule,
    MongoDB
)
from bot.services.auth_service import get_user_decrypted_token
from bot.services.github_api import GitHubAPIClient, GitHubAPIException

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None

def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler

async def init_scheduler() -> AsyncIOScheduler:
    """Initializes and starts the APScheduler, restoring saved jobs from MongoDB."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("APScheduler started.")
        
    # Restore existing active schedules
    try:
        schedules = await get_all_active_schedules()
        for sched in schedules:
            await add_job_to_scheduler(sched)
        logger.info(f"Restored {len(schedules)} scheduled commit jobs from database.")
    except Exception as e:
        logger.error(f"Error restoring scheduled jobs: {e}")
        
    return scheduler

async def execute_scheduled_commit(schedule_id: str):
    """Callback function executed by APScheduler to push a scheduled file commit to GitHub."""
    logger.info(f"Executing scheduled commit for schedule_id: {schedule_id}")
    db = MongoDB.get_db()
    sched = await db.schedules.find_one({"schedule_id": schedule_id})
    if not sched or sched.get("status") != "active":
        logger.warning(f"Schedule {schedule_id} inactive or missing. Skipping.")
        return

    telegram_id = sched["telegram_id"]
    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        logger.error(f"User {telegram_id} not authenticated for scheduled commit {schedule_id}.")
        return

    user_tz = sched.get("timezone", "Asia/Kolkata")
    try:
        loc_tz = pytz.timezone(user_tz)
    except Exception:
        loc_tz = pytz.timezone("Asia/Kolkata")

    day_name = datetime.now(loc_tz).strftime("%a")
    multi_plan = sched.get("multi_repo_plan")
    
    target_repo_full = sched["repo"]
    if isinstance(multi_plan, dict) and day_name in multi_plan:
        day_data = multi_plan[day_name]
        if isinstance(day_data, dict) and day_data.get("repos"):
            repos_list = day_data["repos"]
            exec_idx = sched.get("total_executions", 0)
            target_repo_full = repos_list[exec_idx % len(repos_list)]

    if "/" in target_repo_full:
        owner, repo_name = target_repo_full.split("/")
    else:
        owner, repo_name = sched["repo"].split("/")

    file_path = sched.get("file_path") or "ACTIVITY.md"
    commit_msg = sched.get("commit_message") or "Update activity log"
    
    # Generate dynamic timestamp & unique content so GitHub always accepts new commits
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    base_content = sched.get("content") or "# Automated Activity Log\n"
    
    if "{{TIMESTAMP}}" in base_content:
        content_str = base_content.replace("{{TIMESTAMP}}", now_utc)
    else:
        content_str = f"{base_content.strip()}\n\n<!-- Activity Update: {now_utc} -->\n"

    client = GitHubAPIClient(token)
    try:
        # Check existing file SHA if present
        sha = None
        try:
            file_info = await client.get_file_contents(owner, repo_name, file_path)
            if isinstance(file_info, dict) and "sha" in file_info:
                sha = file_info["sha"]
        except GitHubAPIException as ge:
            if ge.status_code != 404:
                raise ge

        await client.create_or_update_file(
            owner=owner,
            repo=repo_name,
            path=file_path,
            content_bytes=content_str.encode("utf-8"),
            commit_message=f"{commit_msg} ({now_utc})",
            sha=sha
        )

        # Update last_run, increment total_executions, and log timestamp history in MongoDB
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.schedules.update_one(
            {"schedule_id": schedule_id},
            {
                "$set": {"last_run": now_iso},
                "$inc": {"total_executions": 1},
                "$push": {
                    "execution_history": {
                        "$each": [now_iso],
                        "$slice": -100 # Keep last 100 execution timestamps
                    }
                }
            }
        )
        logger.info(f"Scheduled commit {schedule_id} executed successfully for {sched['repo']}.")

        # --- Send Instant Telegram Notification to User ---
        try:
            from bot.main import get_bot_app
            from bot.utils.helpers import format_user_datetime
            bot_app = get_bot_app()
            if bot_app and bot_app.bot:
                total_executed = (sched.get("total_executions", 0)) + 1
                today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                
                history = sched.get("execution_history", [])
                history.append(now_iso)
                
                commits_today = sum(
                    1 for ts in history 
                    if isinstance(ts, str) and ts.startswith(today_prefix)
                )
                
                stype = sched.get("schedule_type", "custom")
                cpd = sched.get("commits_per_day")
                weekly_plan = sched.get("weekly_plan")
                user_tz = sched.get("timezone", "Asia/Kolkata")

                if stype == "weekly_planner" and isinstance(weekly_plan, dict):
                    try:
                        loc_tz = pytz.timezone(user_tz)
                    except Exception:
                        loc_tz = pytz.timezone("Asia/Kolkata")
                    day_name = datetime.now(loc_tz).strftime("%a")
                    target_today = weekly_plan.get(day_name, 5)
                    plan_desc = f"📆 Weekly Planner (Today {day_name}: {target_today} Commits)"
                elif stype == "custom_daily" or cpd:
                    target_today = cpd or 24
                    plan_desc = f"🎯 Custom Daily ({target_today} Commits/Day)"
                elif stype == "deep_green":
                    target_today = 24
                    plan_desc = "🟢 Deep Green Mode (24 Commits/Day)"
                else:
                    target_today = 1
                    plan_desc = f"📅 Cron (<code>{sched.get('cron_expression', 'Daily')}</code>)"

                pending_today = max(0, target_today - commits_today)
                mode_title = "🚀 SCHEDULED COMMIT EXECUTED!"
                formatted_time = format_user_datetime(now_iso, user_tz)
                
                notif_text = (
                    f"<b>{mode_title}</b>\n\n"
                    f"🆔 <b>Schedule ID:</b> <code>{schedule_id}</code>\n"
                    f"📦 <b>Repository:</b> <code>{sched['repo']}</code>\n"
                    f"📄 <b>File Path:</b> <code>{file_path}</code>\n"
                    f"💬 <b>Commit Message:</b> <i>{commit_msg}</i>\n"
                    f"⚙️ <b>Schedule Plan:</b> {plan_desc}\n"
                    f"🕒 <b>Executed At:</b> {formatted_time}\n\n"
                    f"📊 <b>Today's Activity Metrics:</b>\n"
                    f"• <b>Completed Today:</b> {commits_today} / {target_today} commits\n"
                    f"• <b>Pending Today:</b> {pending_today} pending\n"
                    f"• <b>Total Lifetime Executions:</b> {total_executed}\n\n"
                    f"🔗 <a href='https://github.com/{sched['repo']}'>View Repository on GitHub</a>"
                )
                await bot_app.bot.send_message(chat_id=telegram_id, text=notif_text, parse_mode="HTML")
        except Exception as ne:
            logger.warning(f"Could not send commit notification to user {telegram_id}: {ne}")
    except Exception as e:
        logger.error(f"Scheduled commit execution failed for {schedule_id}: {e}")

async def add_job_to_scheduler(sched: Dict[str, Any]):
    scheduler = get_scheduler()
    schedule_id = sched["schedule_id"]
    
    # Remove existing job if present
    if scheduler.get_job(schedule_id):
        scheduler.remove_job(schedule_id)
        
    cron_expr = sched.get("cron_expression")
    interval_mins = sched.get("interval_minutes")
    start_time_str = sched.get("start_time")
    stype = sched.get("schedule_type")
    weekly_plan = sched.get("weekly_plan")
    tz_str = sched.get("timezone", "Asia/Kolkata")
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.timezone("Asia/Kolkata")

    if stype == "weekly_planner" and isinstance(weekly_plan, dict):
        # Calculate today's interval dynamically
        day_name = datetime.now(tz).strftime("%a")
        target_today = weekly_plan.get(day_name, 5)
        if target_today > 0:
            interval_mins = max(1, int((24 * 60) / target_today))
        else:
            interval_mins = None

    if interval_mins:
        from apscheduler.triggers.interval import IntervalTrigger
        start_date = None
        if start_time_str and start_time_str != "now":
            try:
                h, m = map(int, start_time_str.split(":"))
                now_local = datetime.now(tz)
                start_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
                if start_local < now_local:
                    start_local = now_local
                start_date = start_local
            except Exception as e:
                logger.warning(f"Could not parse start_time '{start_time_str}': {e}")

        trigger = IntervalTrigger(minutes=int(interval_mins), timezone=tz, start_date=start_date)
        scheduler.add_job(
            execute_scheduled_commit,
            trigger=trigger,
            id=schedule_id,
            args=[schedule_id],
            replace_existing=True
        )
        logger.info(f"Job {schedule_id} added with interval of {interval_mins} minutes (start_time={start_time_str}).")
    elif cron_expr:
        parts = cron_expr.split()
        if len(parts) == 5:
            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
                timezone=tz
            )
            scheduler.add_job(
                execute_scheduled_commit,
                trigger=trigger,
                id=schedule_id,
                args=[schedule_id],
                replace_existing=True
            )
            logger.info(f"Job {schedule_id} added to APScheduler with trigger '{cron_expr}'.")
        else:
            logger.error(f"Invalid cron expression '{cron_expr}' for schedule {schedule_id}.")

async def create_commit_schedule(
    telegram_id: int,
    repo: str,
    file_path: str,
    commit_message: str,
    content: str,
    schedule_type: str, # 'daily', 'weekly', 'deep_green', 'custom_daily', 'weekly_planner', 'custom'
    cron_expression: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    commits_per_day: Optional[int] = None,
    start_time: Optional[str] = None,
    weekly_plan: Optional[Dict[str, int]] = None,
    multi_repo_plan: Optional[Dict[str, Any]] = None,
    user_tz: str = "Asia/Kolkata"
) -> str:
    import uuid
    schedule_id = f"sched_{uuid.uuid4().hex[:10]}"
    
    sched_data = {
        "schedule_id": schedule_id,
        "telegram_id": telegram_id,
        "repo": repo,
        "file_path": file_path,
        "commit_message": commit_message,
        "content": content,
        "schedule_type": schedule_type,
        "cron_expression": cron_expression or "",
        "interval_minutes": interval_minutes,
        "commits_per_day": commits_per_day,
        "start_time": start_time or "now",
        "weekly_plan": weekly_plan,
        "multi_repo_plan": multi_repo_plan,
        "timezone": user_tz,
        "status": "active",
        "last_run": None,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await save_schedule(sched_data)
    await add_job_to_scheduler(sched_data)
    return schedule_id

async def pause_commit_schedule(schedule_id: str, telegram_id: int) -> bool:
    scheduler = get_scheduler()
    if scheduler.get_job(schedule_id):
        scheduler.pause_job(schedule_id)
    return await update_schedule_status(schedule_id, "paused")

async def resume_commit_schedule(schedule_id: str, telegram_id: int) -> bool:
    db = MongoDB.get_db()
    sched = await db.schedules.find_one({"schedule_id": schedule_id, "telegram_id": telegram_id})
    if sched:
        await add_job_to_scheduler(sched)
        return await update_schedule_status(schedule_id, "active")
    return False

async def remove_commit_schedule(schedule_id: str, telegram_id: int) -> bool:
    scheduler = get_scheduler()
    if scheduler.get_job(schedule_id):
        scheduler.remove_job(schedule_id)
    return await delete_schedule(schedule_id, telegram_id)
