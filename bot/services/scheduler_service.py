import logging
import random
import pytz
from datetime import datetime, timezone, timedelta
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

DAYS_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def shuffle_weekly_plan_dict(plan: Dict[str, int]) -> Dict[str, int]:
    """Randomly reassigns the 7 existing commit counts across Mon-Sun."""
    vals = [int(plan.get(d, 0)) for d in DAYS_ORDER]
    if len(set(vals)) > 1:
        original = list(vals)
        for _ in range(5):
            random.shuffle(vals)
            if vals != original:
                break
    return {d: vals[i] for i, d in enumerate(DAYS_ORDER)}


async def maybe_shuffle_week(
    sched: Dict[str, Any],
    loc_tz: Any,
    force_next_week: bool = False,
) -> Optional[Dict[str, int]]:
    """Shuffles weekly_plan once per week when auto_shuffle_weekly is enabled."""
    if not sched.get("auto_shuffle_weekly") or not isinstance(sched.get("weekly_plan"), dict):
        return None

    now_local = datetime.now(loc_tz)
    target_week = (now_local + timedelta(days=1)).strftime("%G-W%V") if force_next_week else now_local.strftime("%G-W%V")
    last_week = sched.get("last_shuffled_week")

    if not force_next_week and not last_week:
        # First time tracking: record current week without shuffling mid-week
        db = MongoDB.get_db()
        await db.schedules.update_one(
            {"schedule_id": sched["schedule_id"]},
            {"$set": {"last_shuffled_week": target_week}}
        )
        sched["last_shuffled_week"] = target_week
        return None

    if force_next_week or last_week != target_week:
        new_plan = shuffle_weekly_plan_dict(sched["weekly_plan"])
        db = MongoDB.get_db()
        await db.schedules.update_one(
            {"schedule_id": sched["schedule_id"]},
            {
                "$set": {
                    "weekly_plan": new_plan,
                    "last_shuffled_week": target_week,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        )
        sched["weekly_plan"] = new_plan
        sched["last_shuffled_week"] = target_week
        await add_job_to_scheduler(sched)
        logger.info(f"Auto-shuffled weekly plan for {sched['schedule_id']} (week={target_week}): {new_plan}")
        return new_plan
    return None


def get_mode_states(sched: Dict[str, Any]) -> tuple[bool, bool]:
    """Returns (weekly_active, deep_green_active) supporting both new dual-state and legacy docs."""
    if "weekly_active" in sched or "deep_green_active" in sched:
        return bool(sched.get("weekly_active", False)), bool(sched.get("deep_green_active", False))
    is_active = sched.get("status") == "active"
    if sched.get("schedule_type") == "deep_green":
        return False, is_active
    return is_active, False


async def execute_scheduled_commit(schedule_id: str, mode: Optional[str] = None):
    """Callback function executed by APScheduler to push a scheduled file commit to GitHub."""
    logger.info(f"Executing scheduled commit for schedule_id: {schedule_id} (mode={mode})")
    db = MongoDB.get_db()
    sched = await db.schedules.find_one({"schedule_id": schedule_id})
    if not sched:
        logger.warning(f"Schedule {schedule_id} missing. Skipping.")
        return

    weekly_active, deep_green_active = get_mode_states(sched)
    if mode == "weekly_planner" and not weekly_active:
        logger.info(f"Weekly mode paused for {schedule_id}. Skipping.")
        return
    if mode == "deep_green" and not deep_green_active:
        logger.info(f"Deep Green mode paused for {schedule_id}. Skipping.")
        return
    if mode is None and sched.get("status") != "active":
        logger.warning(f"Schedule {schedule_id} inactive. Skipping.")
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

    effective_mode = mode or sched.get("schedule_type")
    if effective_mode == "weekly_planner":
        await maybe_shuffle_week(sched, loc_tz, force_next_week=False)

    # Respect weekly_planner 0-commit days
    if effective_mode == "weekly_planner" and isinstance(sched.get("weekly_plan"), dict):
        target_for_today = sched["weekly_plan"].get(day_name, 0)
        if target_for_today <= 0:
            logger.info(f"Schedule {schedule_id} has 0 commits configured for {day_name}. Skipping.")
            return
    
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

        # --- Compute local today's commits & check Sunday end-of-week auto-shuffle ---
        total_executed = (sched.get("total_executions", 0)) + 1
        today_local_str = datetime.now(loc_tz).strftime("%Y-%m-%d")
        history = list(sched.get("execution_history", []))
        history.append(now_iso)

        commits_today = 0
        for ts in history:
            if isinstance(ts, str):
                try:
                    dt_utc = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt_utc.tzinfo is None:
                        dt_utc = pytz.utc.localize(dt_utc)
                    if dt_utc.astimezone(loc_tz).strftime("%Y-%m-%d") == today_local_str:
                        commits_today += 1
                except Exception:
                    pass

        weekly_plan = sched.get("weekly_plan")
        shuffled_next_plan = None
        if (
            effective_mode == "weekly_planner"
            and day_name == "Sun"
            and isinstance(weekly_plan, dict)
            and commits_today >= weekly_plan.get("Sun", 0)
        ):
            shuffled_next_plan = await maybe_shuffle_week(sched, loc_tz, force_next_week=True)

        # --- Send Instant Telegram Notification to User ---
        try:
            from bot.main import get_bot_app
            from bot.utils.helpers import format_user_datetime
            bot_app = get_bot_app()
            if bot_app and bot_app.bot and user.get("notifications", True):
                stype = effective_mode or sched.get("schedule_type", "custom")
                cpd = sched.get("commits_per_day")
                user_tz = sched.get("timezone", "Asia/Kolkata")

                if stype == "weekly_planner" and isinstance(weekly_plan, dict):
                    target_today = weekly_plan.get(day_name, 5)
                    plan_desc = f"📆 Weekly Schedule (Today {day_name}: {target_today} Commits)"
                elif stype == "deep_green":
                    target_today = 24
                    plan_desc = "🟢 Deep Green Mode (24 Commits/Day)"
                elif stype == "custom_daily" or cpd:
                    target_today = cpd or 24
                    plan_desc = f"🎯 Custom Daily ({target_today} Commits/Day)"
                else:
                    target_today = 1
                    plan_desc = f"📅 Cron (<code>{sched.get('cron_expression', 'Daily')}</code>)"

                pending_today = max(0, target_today - commits_today)
                mode_title = "🚀 SCHEDULED COMMIT EXECUTED!"
                formatted_time = format_user_datetime(now_iso, user_tz)
                shuffle_note = (
                    f"\n🔀 <b>Next Week Auto-Shuffled:</b> <code>"
                    + ", ".join(f"{d}:{shuffled_next_plan[d]}" for d in DAYS_ORDER)
                    + "</code>"
                    if shuffled_next_plan else ""
                )

                notif_text = (
                    f"<b>{mode_title}</b>\n\n"
                    f"🆔 <b>Schedule ID:</b> <code>{schedule_id}</code>\n"
                    f"📦 <b>Active Repository:</b> <code>{target_repo_full}</code>\n"
                    f"📄 <b>File Path:</b> <code>{file_path}</code>\n"
                    f"💬 <b>Commit Message:</b> <i>{commit_msg}</i>\n"
                    f"⚙️ <b>Schedule Plan:</b> {plan_desc}\n"
                    f"🕒 <b>Executed At:</b> {formatted_time}\n\n"
                    f"📊 <b>Today's Activity Metrics:</b>\n"
                    f"• <b>Completed Today:</b> {commits_today} / {target_today} commits\n"
                    f"• <b>Pending Today:</b> {pending_today} pending\n"
                    f"• <b>Total Lifetime Executions:</b> {total_executed}{shuffle_note}\n\n"
                    f"🔗 <a href='https://github.com/{target_repo_full}'>View Repository on GitHub</a>"
                )
                await bot_app.bot.send_message(chat_id=telegram_id, text=notif_text, parse_mode="HTML")
        except Exception as ne:
            logger.warning(f"Could not send commit notification to user {telegram_id}: {ne}")
    except Exception as e:
        logger.error(f"Scheduled commit execution failed for {schedule_id}: {e}")

def _remove_all_jobs_for_schedule(scheduler: AsyncIOScheduler, schedule_id: str):
    """Removes legacy and mode-specific APScheduler jobs for a schedule."""
    for job_id in (schedule_id, f"{schedule_id}__weekly", f"{schedule_id}__deep_green"):
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)


async def add_job_to_scheduler(sched: Dict[str, Any]):
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler = get_scheduler()
    schedule_id = sched["schedule_id"]

    _remove_all_jobs_for_schedule(scheduler, schedule_id)

    tz_str = sched.get("timezone", "Asia/Kolkata")
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.timezone("Asia/Kolkata")

    weekly_active, deep_green_active = get_mode_states(sched)
    weekly_plan = sched.get("weekly_plan")
    cron_expr = sched.get("cron_expression")
    stype = sched.get("schedule_type")
    start_time_str = sched.get("start_time")

    # 1. Schedule Weekly Planner job independently if weekly_active
    if weekly_active:
        if isinstance(weekly_plan, dict):
            day_name = datetime.now(tz).strftime("%a")
            target_today = weekly_plan.get(day_name, 5)
            w_interval = max(1, int((24 * 60) / target_today)) if target_today > 0 else 60
            scheduler.add_job(
                execute_scheduled_commit,
                trigger=IntervalTrigger(minutes=w_interval, timezone=tz),
                id=f"{schedule_id}__weekly",
                args=[schedule_id, "weekly_planner"],
                replace_existing=True,
            )
            logger.info(f"Weekly job {schedule_id}__weekly added ({w_interval}m interval).")
        elif sched.get("interval_minutes"):
            w_interval = int(sched["interval_minutes"])
            scheduler.add_job(
                execute_scheduled_commit,
                trigger=IntervalTrigger(minutes=w_interval, timezone=tz),
                id=f"{schedule_id}__weekly",
                args=[schedule_id, "weekly_planner"],
                replace_existing=True,
            )
            logger.info(f"Legacy/Weekly job {schedule_id}__weekly added ({w_interval}m interval).")
        elif cron_expr:
            parts = cron_expr.split()
            if len(parts) == 5:
                trigger = CronTrigger(
                    minute=parts[0], hour=parts[1], day=parts[2],
                    month=parts[3], day_of_week=parts[4], timezone=tz
                )
                scheduler.add_job(
                    execute_scheduled_commit,
                    trigger=trigger,
                    id=f"{schedule_id}__weekly",
                    args=[schedule_id, "weekly_planner"],
                    replace_existing=True,
                )

    # 2. Schedule Deep Green job independently if deep_green_active (24 commits/day = 60m)
    if deep_green_active:
        scheduler.add_job(
            execute_scheduled_commit,
            trigger=IntervalTrigger(minutes=60, timezone=tz),
            id=f"{schedule_id}__deep_green",
            args=[schedule_id, "deep_green"],
            replace_existing=True,
        )
        logger.info(f"Deep Green job {schedule_id}__deep_green added (60m interval).")

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
    user_tz: str = "Asia/Kolkata",
    auto_shuffle_weekly: bool = False,
) -> str:
    import uuid
    schedule_id = f"sched_{uuid.uuid4().hex[:10]}"
    
    weekly_active = (schedule_type != "deep_green")
    deep_green_active = (schedule_type == "deep_green")
    try:
        loc_tz = pytz.timezone(user_tz)
    except Exception:
        loc_tz = pytz.timezone("Asia/Kolkata")
    cur_week = datetime.now(loc_tz).strftime("%G-W%V")

    sched_data = {
        "schedule_id": schedule_id,
        "telegram_id": telegram_id,
        "repo": repo,
        "file_path": file_path,
        "commit_message": commit_message,
        "content": content,
        "schedule_type": schedule_type,
        "weekly_active": weekly_active,
        "deep_green_active": deep_green_active,
        "auto_shuffle_weekly": bool(auto_shuffle_weekly),
        "last_shuffled_week": cur_week if auto_shuffle_weekly else None,
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

    # If configured to start immediately, dispatch the initial commit in background
    if (start_time == "now" or not start_time) and schedule_type in ["custom_daily", "deep_green", "weekly_planner"]:
        import asyncio
        asyncio.create_task(execute_scheduled_commit(schedule_id, mode=schedule_type))

    return schedule_id

async def pause_commit_schedule(schedule_id: str, telegram_id: int) -> bool:
    scheduler = get_scheduler()
    _remove_all_jobs_for_schedule(scheduler, schedule_id)
    db = MongoDB.get_db()
    await db.schedules.update_one(
        {"schedule_id": schedule_id, "telegram_id": telegram_id},
        {"$set": {"status": "paused", "weekly_active": False, "deep_green_active": False}}
    )
    return True

async def resume_commit_schedule(schedule_id: str, telegram_id: int) -> bool:
    db = MongoDB.get_db()
    sched = await db.schedules.find_one({"schedule_id": schedule_id, "telegram_id": telegram_id})
    if sched:
        stype = sched.get("schedule_type", "weekly_planner")
        w_act = stype != "deep_green"
        d_act = stype == "deep_green"
        await db.schedules.update_one(
            {"schedule_id": schedule_id, "telegram_id": telegram_id},
            {"$set": {"status": "active", "weekly_active": w_act, "deep_green_active": d_act}}
        )
        sched["status"] = "active"
        sched["weekly_active"] = w_act
        sched["deep_green_active"] = d_act
        await add_job_to_scheduler(sched)
        return True
    return False

async def remove_commit_schedule(schedule_id: str, telegram_id: int) -> bool:
    scheduler = get_scheduler()
    _remove_all_jobs_for_schedule(scheduler, schedule_id)
    return await delete_schedule(schedule_id, telegram_id)

async def update_commit_schedule(
    schedule_id: str,
    telegram_id: int,
    schedule_type: Optional[str] = None,
    weekly_plan: Optional[Dict[str, int]] = None,
    weekly_active: Optional[bool] = None,
    deep_green_active: Optional[bool] = None,
    auto_shuffle_weekly: Optional[bool] = None,
    status: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Updates weekly/deep_green active flags independently, edits weekly plan, and reschedules."""
    db = MongoDB.get_db()
    sched = await db.schedules.find_one({"schedule_id": schedule_id, "telegram_id": telegram_id})
    if not sched:
        return None

    cur_w, cur_d = get_mode_states(sched)
    new_w = cur_w if weekly_active is None else bool(weekly_active)
    new_d = cur_d if deep_green_active is None else bool(deep_green_active)

    # Support legacy single-status override (e.g. Stop Both sends status="paused")
    if weekly_active is None and deep_green_active is None and status == "paused":
        new_w = False
        new_d = False
    elif weekly_active is None and deep_green_active is None and status == "active":
        target = schedule_type or sched.get("schedule_type", "weekly_planner")
        new_w = (target != "deep_green")
        new_d = (target == "deep_green")

    updates: Dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "weekly_active": new_w,
        "deep_green_active": new_d,
        "status": "active" if (new_w or new_d) else "paused",
    }

    if schedule_type:
        updates["schedule_type"] = schedule_type
    elif new_d and not new_w:
        updates["schedule_type"] = "deep_green"
    elif new_w:
        updates["schedule_type"] = "weekly_planner"

    if weekly_plan is not None:
        updates["weekly_plan"] = weekly_plan
    elif "weekly_plan" not in sched or not sched.get("weekly_plan"):
        updates["weekly_plan"] = {"Mon": 3, "Tue": 5, "Wed": 5, "Thu": 4, "Fri": 6, "Sat": 2, "Sun": 1}

    if auto_shuffle_weekly is not None:
        updates["auto_shuffle_weekly"] = bool(auto_shuffle_weekly)
        if auto_shuffle_weekly and not sched.get("last_shuffled_week"):
            try:
                loc_tz = pytz.timezone(sched.get("timezone", "Asia/Kolkata"))
            except Exception:
                loc_tz = pytz.timezone("Asia/Kolkata")
            updates["last_shuffled_week"] = datetime.now(loc_tz).strftime("%G-W%V")

    await db.schedules.update_one(
        {"schedule_id": schedule_id, "telegram_id": telegram_id},
        {"$set": updates}
    )

    updated_sched = await db.schedules.find_one({"schedule_id": schedule_id, "telegram_id": telegram_id})
    if updated_sched:
        await add_job_to_scheduler(updated_sched)
    return updated_sched
