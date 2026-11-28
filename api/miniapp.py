import os
import hmac
import json
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from bot.services.auth_service import (
    get_user_decrypted_token,
    generate_oauth_url,
    disconnect_user_account,
    authenticate_with_pat,
    list_connected_accounts,
)
from bot.services.github_api import GitHubAPIClient, GitHubAPIException
from bot.services.scheduler_service import (
    create_commit_schedule,
    update_commit_schedule,
    remove_commit_schedule,
    get_mode_states,
    add_job_to_scheduler,
    get_scheduler,
    _remove_all_jobs_for_schedule,
)
from bot.database.mongodb import (
    get_user_schedules,
    update_user_settings,
    switch_active_account,
    MAX_GITHUB_ACCOUNTS,
    MongoDB,
)

logger = logging.getLogger(__name__)

router = APIRouter()

MINIAPP_HTML_PATH = Path(__file__).resolve().parent.parent / "web" / "miniapp.html"


def verify_telegram_init_data(init_data: str) -> Dict[str, Any]:
    """
    Cryptographically verifies Telegram WebApp initData using TELEGRAM_BOT_TOKEN.
    Returns the parsed user dict on success.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram initData.")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        raise HTTPException(status_code=500, detail="Bot token not configured.")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing initData hash.")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram initData signature.")

    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="Missing user in initData.")

    try:
        return json.loads(user_raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed user JSON in initData.")


def serialize_schedule(s: Dict[str, Any]) -> Dict[str, Any]:
    """Strips MongoDB _id and includes normalized weekly_active / deep_green_active booleans."""
    doc = dict(s)
    doc.pop("_id", None)
    w_act, d_act = get_mode_states(doc)
    doc["weekly_active"] = w_act
    doc["deep_green_active"] = d_act
    doc["auto_shuffle_weekly"] = bool(doc.get("auto_shuffle_weekly", False))
    doc["status"] = "active" if (w_act or d_act) else "paused"
    return doc


class CreateScheduleRequest(BaseModel):
    repo_mode: str = Field(..., pattern="^(existing|create)$")
    repo_name: str = Field(..., min_length=1, max_length=120)
    is_private: bool = False
    file_path: str = "ACTIVITY.md"
    commit_message: str = "Update activity log"
    schedule_type: str = Field(..., pattern="^(weekly_planner|deep_green)$")
    weekly_plan: Optional[Dict[str, int]] = None
    auto_shuffle_weekly: bool = False
    timezone: Optional[str] = None


class UpdateScheduleRequest(BaseModel):
    schedule_type: Optional[str] = Field(None, pattern="^(weekly_planner|deep_green)$")
    weekly_plan: Optional[Dict[str, int]] = None
    weekly_active: Optional[bool] = None
    deep_green_active: Optional[bool] = None
    auto_shuffle_weekly: Optional[bool] = None
    status: Optional[str] = Field(None, pattern="^(active|paused)$")


@router.get("/app", response_class=HTMLResponse)
async def serve_miniapp():
    """Serves the Telegram Mini App UI."""
    if not MINIAPP_HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="Mini App UI file not found.")
    return HTMLResponse(content=MINIAPP_HTML_PATH.read_text(encoding="utf-8"))


@router.get("/api/miniapp/me")
async def miniapp_me(x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data")):
    """Returns user GitHub connection status and all commit schedules."""
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        return {
            "connected": False,
            "telegram_id": telegram_id,
            "first_name": tg_user.get("first_name", "Developer"),
            "oauth_url": generate_oauth_url(telegram_id),
            "schedules": [],
        }

    schedules = await get_user_schedules(telegram_id)
    accounts = await list_connected_accounts(telegram_id)
    return {
        "connected": True,
        "telegram_id": telegram_id,
        "first_name": tg_user.get("first_name", "Developer"),
        "github_username": user.get("github_username", ""),
        "auth_method": user.get("auth_method", "oauth").upper(),
        "timezone": user.get("timezone", "Asia/Kolkata"),
        "notifications": user.get("notifications", True),
        "accounts": accounts,
        "max_accounts": MAX_GITHUB_ACCOUNTS,
        "oauth_url": generate_oauth_url(telegram_id),
        "schedules": [serialize_schedule(s) for s in schedules],
    }


@router.get("/api/miniapp/repos")
async def miniapp_list_repos(x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data")):
    """Fetches user's GitHub repositories for quick selection."""
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        raise HTTPException(status_code=401, detail="GitHub account not connected.")

    client = GitHubAPIClient(token)
    try:
        repos = await client.list_repositories(page=1, per_page=30, sort="updated")
        return {
            "repos": [
                {
                    "full_name": r.get("full_name"),
                    "name": r.get("name"),
                    "private": r.get("private", False),
                    "default_branch": r.get("default_branch", "main"),
                }
                for r in (repos or [])
            ]
        }
    except GitHubAPIException as ge:
        raise HTTPException(status_code=ge.status_code or 400, detail=str(ge))


@router.get("/api/miniapp/repo/check")
async def miniapp_check_repo(
    name: str = Query(..., min_length=1),
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data"),
):
    """Fetches and validates an existing GitHub repository by name or owner/repo."""
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        raise HTTPException(status_code=401, detail="GitHub account not connected.")

    username = user.get("github_username", "")
    clean = name.strip().strip("/")
    if "/" in clean:
        owner, repo_name = clean.split("/", 1)
    else:
        owner, repo_name = username, clean

    client = GitHubAPIClient(token)
    try:
        repo = await client.get_repository(owner, repo_name)
        return {
            "found": True,
            "full_name": repo.get("full_name"),
            "name": repo.get("name"),
            "private": repo.get("private", False),
            "default_branch": repo.get("default_branch", "main"),
            "description": repo.get("description") or "",
            "html_url": repo.get("html_url"),
        }
    except GitHubAPIException as ge:
        raise HTTPException(status_code=ge.status_code or 404, detail=f"Repository '{owner}/{repo_name}' not found or inaccessible.")


@router.post("/api/miniapp/schedules")
async def miniapp_create_schedule(
    body: CreateScheduleRequest,
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data"),
):
    """Creates a new Weekly Schedule or Deep Green Mode schedule from the Mini App."""
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        raise HTTPException(status_code=401, detail="GitHub account not connected.")

    username = user.get("github_username", "")
    client = GitHubAPIClient(token)

    raw_repo = body.repo_name.strip().strip("/")
    if body.repo_mode == "existing":
        if "/" in raw_repo:
            owner, r_name = raw_repo.split("/", 1)
        else:
            owner, r_name = username, raw_repo
        try:
            repo_res = await client.get_repository(owner, r_name)
            full_name = repo_res.get("full_name") or f"{owner}/{r_name}"
        except GitHubAPIException as ge:
            raise HTTPException(status_code=ge.status_code or 404, detail=f"Repository '{owner}/{r_name}' not found: {ge}")
    else:
        r_name = raw_repo.split("/")[-1].replace(" ", "-")
        try:
            repo_res = await client.create_repository(
                name=r_name,
                description="Dedicated repository for automated commit schedules",
                private=body.is_private,
            )
            full_name = repo_res.get("full_name") or f"{username}/{r_name}"
        except GitHubAPIException as ge:
            if ge.status_code == 422 or "already exists" in str(ge).lower():
                raise HTTPException(
                    status_code=409,
                    detail=f"Repository '{r_name}' already exists. Switch to 'Use Existing Repo' to select it.",
                )
            raise HTTPException(status_code=ge.status_code or 400, detail=str(ge))

    tz = body.timezone or user.get("timezone", "Asia/Kolkata")
    file_path = body.file_path.strip() or "ACTIVITY.md"
    commit_msg = body.commit_message.strip() or "Update activity log"
    content = "# Scheduled Automated Commit\nLast updated: {{TIMESTAMP}}\n"

    if body.schedule_type == "deep_green":
        interval_minutes = 60
        commits_per_day = 24
        weekly_plan = None
    else:
        default_plan = {"Mon": 3, "Tue": 5, "Wed": 5, "Thu": 4, "Fri": 6, "Sat": 2, "Sun": 1}
        raw_plan = body.weekly_plan or default_plan
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        weekly_plan = {d: max(0, min(50, int(raw_plan.get(d, 0)))) for d in days}
        if sum(weekly_plan.values()) == 0:
            raise HTTPException(status_code=400, detail="Weekly schedule must have at least 1 commit in the week.")
        interval_minutes = 60
        commits_per_day = None

    schedule_id = await create_commit_schedule(
        telegram_id=telegram_id,
        repo=full_name,
        file_path=file_path,
        commit_message=commit_msg,
        content=content,
        schedule_type=body.schedule_type,
        cron_expression="",
        interval_minutes=interval_minutes,
        commits_per_day=commits_per_day,
        start_time="now",
        weekly_plan=weekly_plan,
        user_tz=tz,
        auto_shuffle_weekly=body.auto_shuffle_weekly,
        github_username=username,
    )

    schedules = await get_user_schedules(telegram_id)
    created = next((s for s in schedules if s["schedule_id"] == schedule_id), None)
    return {
        "ok": True,
        "schedule_id": schedule_id,
        "schedule": serialize_schedule(created) if created else None,
    }


@router.patch("/api/miniapp/schedules/{schedule_id}")
async def miniapp_update_schedule(
    schedule_id: str,
    body: UpdateScheduleRequest,
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data"),
):
    """Switches schedule mode, updates weekly plan, or pauses/resumes a schedule."""
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    clean_plan = None
    if body.weekly_plan is not None:
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        clean_plan = {d: max(0, min(50, int(body.weekly_plan.get(d, 0)))) for d in days}
        if body.schedule_type == "weekly_planner" and sum(clean_plan.values()) == 0:
            raise HTTPException(status_code=400, detail="Weekly schedule must have at least 1 commit in the week.")

    updated = await update_commit_schedule(
        schedule_id=schedule_id,
        telegram_id=telegram_id,
        schedule_type=body.schedule_type,
        weekly_plan=clean_plan,
        weekly_active=body.weekly_active,
        deep_green_active=body.deep_green_active,
        auto_shuffle_weekly=body.auto_shuffle_weekly,
        status=body.status,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    return {"ok": True, "schedule": serialize_schedule(updated)}


@router.delete("/api/miniapp/schedules/{schedule_id}")
async def miniapp_delete_schedule(
    schedule_id: str,
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data"),
):
    """Deletes a commit schedule."""
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    deleted = await remove_commit_schedule(schedule_id, telegram_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"ok": True}


@router.get("/api/miniapp/stats")
async def miniapp_stats(x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data")):
    """Returns live GitHub contribution graph, streaks, and bot scheduler execution stats."""
    import pytz
    from datetime import datetime, timezone

    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        raise HTTPException(status_code=401, detail="GitHub account not connected.")

    username = user.get("github_username", "")
    user_tz = user.get("timezone", "Asia/Kolkata")
    try:
        loc_tz = pytz.timezone(user_tz)
    except Exception:
        loc_tz = pytz.timezone("Asia/Kolkata")
    today_local_dt = datetime.now(loc_tz)
    today_local = today_local_dt.strftime("%Y-%m-%d")
    day_name = today_local_dt.strftime("%a")

    schedules = await get_user_schedules(telegram_id)
    bot_total_commits = 0
    bot_today_commits = 0
    bot_today_target = 0
    active_schedules = 0
    today_schedules = []

    for s in schedules:
        bot_total_commits += int(s.get("total_executions") or 0)
        w_act, d_act = get_mode_states(s)
        if w_act or d_act:
            active_schedules += 1

        sched_completed = 0
        for ts in s.get("execution_history", []):
            if isinstance(ts, str):
                try:
                    dt_utc = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt_utc.tzinfo is None:
                        dt_utc = pytz.utc.localize(dt_utc)
                    if dt_utc.astimezone(loc_tz).strftime("%Y-%m-%d") == today_local:
                        bot_today_commits += 1
                        sched_completed += 1
                except Exception:
                    pass

        sched_target = 0
        if w_act:
            if isinstance(s.get("weekly_plan"), dict):
                sched_target += int(s["weekly_plan"].get(day_name, 0))
            else:
                sched_target += int(s.get("commits_per_day") or 0)
        if d_act:
            sched_target += 24

        bot_today_target += sched_target
        today_schedules.append({
            "schedule_id": s.get("schedule_id"),
            "repo": s.get("repo"),
            "weekly_active": w_act,
            "deep_green_active": d_act,
            "completed_today": sched_completed,
            "target_today": sched_target,
            "pending_today": max(0, sched_target - sched_completed),
        })

    bot_today_pending = max(0, bot_today_target - bot_today_commits)

    client = GitHubAPIClient(token)
    gh_user = {}
    try:
        gh_user = await client.get_contribution_calendar(username)
    except GitHubAPIException as ge:
        logger.warning(f"Stats GraphQL fetch failed for {username}: {ge}")

    cal = (gh_user.get("contributionsCollection") or {}).get("contributionCalendar") or {}
    weeks_raw = cal.get("weeks") or []
    all_days = []
    for w in weeks_raw:
        for d in w.get("contributionDays") or []:
            all_days.append(d)

    # Compute current & longest streak
    longest_streak = 0
    running = 0
    for d in all_days:
        if (d.get("contributionCount") or 0) > 0:
            running += 1
            if running > longest_streak:
                longest_streak = running
        else:
            running = 0

    current_streak = 0
    # Walk backwards; if today is 0 so far, allow streak starting from yesterday
    idx = len(all_days) - 1
    if idx >= 0 and (all_days[idx].get("contributionCount") or 0) == 0:
        idx -= 1
    while idx >= 0 and (all_days[idx].get("contributionCount") or 0) > 0:
        current_streak += 1
        idx -= 1

    today_gh_commits = 0
    for d in reversed(all_days):
        if d.get("date") == today_local:
            today_gh_commits = int(d.get("contributionCount") or 0)
            break
    if today_gh_commits == 0 and all_days:
        today_gh_commits = int(all_days[-1].get("contributionCount") or 0)

    level_map = {
        "NONE": 0,
        "FIRST_QUARTILE": 1,
        "SECOND_QUARTILE": 2,
        "THIRD_QUARTILE": 3,
        "FOURTH_QUARTILE": 4,
    }
    recent_weeks = [
        [
            {
                "date": d.get("date"),
                "count": int(d.get("contributionCount") or 0),
                "level": level_map.get(d.get("contributionLevel"), 0),
            }
            for d in (w.get("contributionDays") or [])
        ]
        for w in weeks_raw[-20:]
    ]

    return {
        "github_username": username,
        "avatar_url": gh_user.get("avatarUrl") or "",
        "public_repos": (gh_user.get("repositories") or {}).get("totalCount", 0),
        "followers": (gh_user.get("followers") or {}).get("totalCount", 0),
        "total_contributions": cal.get("totalContributions", 0),
        "today_contributions": today_gh_commits,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "weeks": recent_weeks,
        "bot_total_commits": bot_total_commits,
        "bot_today_commits": bot_today_commits,
        "bot_today_target": bot_today_target,
        "bot_today_pending": bot_today_pending,
        "today_day": day_name,
        "today_schedules": today_schedules,
        "active_schedules": active_schedules,
    }


class UpdateSettingsRequest(BaseModel):
    timezone: Optional[str] = None
    notifications: Optional[bool] = None


@router.patch("/api/miniapp/settings")
async def miniapp_update_settings(
    body: UpdateSettingsRequest,
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data"),
):
    """Updates user timezone (and syncs schedules) or notification preferences."""
    import pytz

    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    token, user = await get_user_decrypted_token(telegram_id)
    if not token or not user:
        raise HTTPException(status_code=401, detail="GitHub account not connected.")

    updates: Dict[str, Any] = {}
    if body.timezone is not None:
        tz_clean = body.timezone.strip()
        try:
            pytz.timezone(tz_clean)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid timezone '{tz_clean}'. Use an IANA name like Asia/Kolkata or UTC.")
        updates["timezone"] = tz_clean

    if body.notifications is not None:
        updates["notifications"] = bool(body.notifications)

    if updates:
        await update_user_settings(telegram_id, updates)

    if "timezone" in updates:
        db = MongoDB.get_db()
        await db.schedules.update_many(
            {"telegram_id": telegram_id},
            {"$set": {"timezone": updates["timezone"]}}
        )
        schedules = await get_user_schedules(telegram_id)
        for s in schedules:
            w_act, d_act = get_mode_states(s)
            if w_act or d_act:
                await add_job_to_scheduler(s)

    return {
        "ok": True,
        "timezone": updates.get("timezone", user.get("timezone", "Asia/Kolkata")),
        "notifications": updates.get("notifications", user.get("notifications", True)),
    }


class ConnectPatRequest(BaseModel):
    token: str = Field(..., min_length=10)


class SwitchAccountRequest(BaseModel):
    github_username: str = Field(..., min_length=1)


class DisconnectAccountRequest(BaseModel):
    github_username: Optional[str] = None


@router.post("/api/miniapp/accounts/pat")
async def miniapp_connect_pat(
    body: ConnectPatRequest,
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data"),
):
    """Connects a GitHub account (up to max 2) using a Personal Access Token."""
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    try:
        user_doc = await authenticate_with_pat(telegram_id, body.token.strip())
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to connect account: {e}")

    return {
        "ok": True,
        "github_username": user_doc.get("github_username"),
        "accounts": await list_connected_accounts(telegram_id),
    }


@router.post("/api/miniapp/accounts/switch")
async def miniapp_switch_account(
    body: SwitchAccountRequest,
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data"),
):
    """Switches the active GitHub account for this Telegram user."""
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    updated = await switch_active_account(telegram_id, body.github_username.strip())
    if not updated:
        raise HTTPException(status_code=404, detail="GitHub account not found.")

    return {
        "ok": True,
        "github_username": updated.get("github_username"),
        "accounts": await list_connected_accounts(telegram_id),
    }


@router.post("/api/miniapp/disconnect")
async def miniapp_disconnect(
    body: Optional[DisconnectAccountRequest] = None,
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data"),
):
    """Disconnects a specific GitHub account (or all accounts) and stops its scheduled jobs."""
    tg_user = verify_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    target_user = body.github_username.strip() if (body and body.github_username) else None
    schedules = await get_user_schedules(telegram_id)
    scheduler = get_scheduler()

    for s in schedules:
        owner = (s.get("github_username") or (s["repo"].split("/")[0] if "/" in s.get("repo", "") else "")).lower()
        if not target_user or owner == target_user.lower():
            _remove_all_jobs_for_schedule(scheduler, s["schedule_id"])

    await disconnect_user_account(telegram_id, github_username=target_user)
    return {
        "ok": True,
        "oauth_url": generate_oauth_url(telegram_id),
        "accounts": await list_connected_accounts(telegram_id),
    }

