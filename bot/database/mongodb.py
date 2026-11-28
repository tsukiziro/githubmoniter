import os
import logging
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class MongoDB:
    _client: Optional[AsyncIOMotorClient] = None
    _db: Optional[AsyncIOMotorDatabase] = None

    @classmethod
    def get_client(cls) -> AsyncIOMotorClient:
        if cls._client is None:
            mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/github_guardian")
            logger.info(f"Connecting to MongoDB at {mongo_uri.split('@')[-1] if '@' in mongo_uri else mongo_uri}...")
            cls._client = AsyncIOMotorClient(mongo_uri)
        return cls._client

    @classmethod
    def get_db(cls) -> AsyncIOMotorDatabase:
        if cls._db is None:
            client = cls.get_client()
            db_name = os.getenv("MONGODB_DB_NAME", "github_guardian")
            cls._db = client[db_name]
        return cls._db

    @classmethod
    async def init_indices(cls):
        """Creates indexes for fast lookup and data integrity."""
        try:
            db = cls.get_db()
            # Unique index on telegram_id
            await db.users.create_index("telegram_id", unique=True)
            await db.users.create_index("github_id")
            
            # Schedules indices
            await db.schedules.create_index([("telegram_id", 1), ("schedule_id", 1)])
            await db.schedules.create_index("github_username")
            
            # Monitoring settings index
            await db.monitoring_settings.create_index("telegram_id", unique=True)

            # Sub-admins index
            await db.subadmins.create_index("telegram_id", unique=True)
            
            # Purge repositories_cache collection completely from database
            try:
                await db.repositories_cache.drop()
                logger.info("Purged and dropped repositories_cache collection.")
            except Exception:
                pass
            
            # Webhook events TTL index (automatically delete entries after 24 hours = 86400s)
            await db.webhook_events.create_index("created_at", expireAfterSeconds=86400)

            logger.info("MongoDB indexes initialized successfully.")
            
            # Run cleanup on startup to purge any existing bloated records
            await cleanup_database_bloat()
        except Exception as e:
            logger.warning(f"MongoDB index initialization error: {e}")

# Database Helper Functions

MAX_GITHUB_ACCOUNTS = 2


def normalize_user_accounts(user: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Returns normalized list of connected GitHub accounts (supports legacy single-account docs)."""
    if not user:
        return []
    accounts = user.get("accounts")
    if isinstance(accounts, list) and len(accounts) > 0:
        return list(accounts)
    if user.get("github_username") and user.get("encrypted_token"):
        return [{
            "github_id": user.get("github_id"),
            "github_username": user.get("github_username"),
            "encrypted_token": user.get("encrypted_token"),
            "auth_method": user.get("auth_method", "oauth"),
            "avatar_url": user.get("avatar_url", ""),
            "connected_at": user.get("connected_at", datetime.now(timezone.utc).isoformat()),
        }]
    return []


# --- User Management ---
async def save_user(
    telegram_id: int,
    github_id: int,
    github_username: str,
    encrypted_token: str,
    auth_method: str = "oauth",
    tz: str = "Asia/Kolkata",
    avatar_url: str = "",
) -> Dict[str, Any]:
    db = MongoDB.get_db()
    existing = await db.users.find_one({"telegram_id": telegram_id})
    accounts = normalize_user_accounts(existing)
    now_iso = datetime.now(timezone.utc).isoformat()

    new_entry = {
        "github_id": github_id,
        "github_username": github_username,
        "encrypted_token": encrypted_token,
        "auth_method": auth_method,
        "avatar_url": avatar_url,
        "connected_at": now_iso,
    }

    idx = next(
        (
            i for i, a in enumerate(accounts)
            if a.get("github_id") == github_id
            or (a.get("github_username") or "").lower() == github_username.lower()
        ),
        None,
    )

    if idx is not None:
        # Refresh existing connected account
        new_entry["connected_at"] = accounts[idx].get("connected_at", now_iso)
        if not avatar_url and accounts[idx].get("avatar_url"):
            new_entry["avatar_url"] = accounts[idx]["avatar_url"]
        accounts[idx] = new_entry
    else:
        if len(accounts) >= MAX_GITHUB_ACCOUNTS:
            raise ValueError(
                f"Maximum {MAX_GITHUB_ACCOUNTS} GitHub accounts allowed per Telegram account. Remove one first."
            )
        accounts.append(new_entry)

    user_data = {
        "telegram_id": telegram_id,
        "github_id": github_id,
        "github_username": github_username,
        "encrypted_token": encrypted_token,
        "auth_method": auth_method,
        "avatar_url": new_entry["avatar_url"],
        "accounts": accounts,
        "connected_at": existing.get("connected_at", now_iso) if existing else now_iso,
        "timezone": existing.get("timezone", tz) if existing else tz,
        "notifications": existing.get("notifications", True) if existing else True,
        "updated_at": now_iso,
    }

    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": user_data},
        upsert=True,
    )
    return user_data


async def switch_active_account(telegram_id: int, github_username: str) -> Optional[Dict[str, Any]]:
    """Switches the user's active GitHub account among their connected accounts."""
    db = MongoDB.get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    accounts = normalize_user_accounts(user)
    target = next(
        (a for a in accounts if (a.get("github_username") or "").lower() == github_username.lower()),
        None,
    )
    if not target:
        return None

    updates = {
        "github_id": target.get("github_id"),
        "github_username": target.get("github_username"),
        "encrypted_token": target.get("encrypted_token"),
        "auth_method": target.get("auth_method", "oauth"),
        "avatar_url": target.get("avatar_url", ""),
        "accounts": accounts,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.update_one({"telegram_id": telegram_id}, {"$set": updates})
    return await db.users.find_one({"telegram_id": telegram_id})


async def remove_github_account(telegram_id: int, github_username: str) -> Dict[str, Any]:
    """Removes a specific GitHub account (and its schedules). Promotes remaining account or deletes user."""
    db = MongoDB.get_db()
    user = await db.users.find_one({"telegram_id": telegram_id})
    accounts = normalize_user_accounts(user)
    remaining = [
        a for a in accounts if (a.get("github_username") or "").lower() != github_username.lower()
    ]

    # Delete schedules belonging to that GitHub account
    await db.schedules.delete_many({
        "telegram_id": telegram_id,
        "$or": [
            {"github_username": {"$regex": f"^{github_username}$", "$options": "i"}},
            {"repo": {"$regex": f"^{github_username}/", "$options": "i"}},
        ],
    })

    if not remaining:
        await delete_user(telegram_id)
        return {"remaining": 0, "user": None}

    active = remaining[0]
    updates = {
        "github_id": active.get("github_id"),
        "github_username": active.get("github_username"),
        "encrypted_token": active.get("encrypted_token"),
        "auth_method": active.get("auth_method", "oauth"),
        "avatar_url": active.get("avatar_url", ""),
        "accounts": remaining,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.update_one({"telegram_id": telegram_id}, {"$set": updates})
    updated_user = await db.users.find_one({"telegram_id": telegram_id})
    return {"remaining": len(remaining), "user": updated_user}


async def migrate_github_account_to_telegram(
    new_telegram_id: int,
    github_id: int,
    github_username: str,
) -> int:
    """
    If this GitHub account was previously linked to a different (e.g. banned) Telegram ID,
    migrates all of its schedules to new_telegram_id and removes the GitHub account from the old Telegram ID.
    Returns number of migrated schedules.
    """
    import re
    db = MongoDB.get_db()
    uname_regex = f"^{re.escape(github_username)}$"
    repo_prefix_regex = f"^{re.escape(github_username)}/"

    # 1. Re-assign schedules owned by this GitHub username from any old telegram_id to new_telegram_id
    sched_res = await db.schedules.update_many(
        {
            "telegram_id": {"$ne": new_telegram_id},
            "$or": [
                {"github_username": {"$regex": uname_regex, "$options": "i"}},
                {"repo": {"$regex": repo_prefix_regex, "$options": "i"}},
            ],
        },
        {
            "$set": {
                "telegram_id": new_telegram_id,
                "github_username": github_username,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    migrated_count = int(sched_res.modified_count or 0)

    # 2. Remove this GitHub account from any old Telegram user documents
    old_users_cursor = db.users.find({
        "telegram_id": {"$ne": new_telegram_id},
        "$or": [
            {"github_id": github_id},
            {"github_username": {"$regex": uname_regex, "$options": "i"}},
            {"accounts.github_id": github_id},
            {"accounts.github_username": {"$regex": uname_regex, "$options": "i"}},
        ],
    })
    old_users = await old_users_cursor.to_list(length=20)
    for old_u in old_users:
        old_tg_id = old_u.get("telegram_id")
        if old_tg_id:
            await remove_github_account(old_tg_id, github_username)

    if migrated_count > 0:
        logger.info(f"Migrated {migrated_count} schedules for @{github_username} to telegram_id={new_telegram_id}.")
    return migrated_count

async def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    db = MongoDB.get_db()
    return await db.users.find_one({"telegram_id": telegram_id})

async def get_user_by_github_username(github_username: str) -> Optional[Dict[str, Any]]:
    db = MongoDB.get_db()
    return await db.users.find_one({"github_username": github_username})

async def update_user_settings(telegram_id: int, updates: Dict[str, Any]) -> bool:
    db = MongoDB.get_db()
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": updates}
    )
    return result.modified_count > 0

async def delete_user(telegram_id: int) -> bool:
    db = MongoDB.get_db()
    # Delete user and associated settings
    await db.users.delete_one({"telegram_id": telegram_id})
    await db.schedules.delete_many({"telegram_id": telegram_id})
    await db.monitoring_settings.delete_one({"telegram_id": telegram_id})
    return True

# --- Cache & Repositories (Disabled - No caching of personal repositories) ---
async def cache_user_repositories(telegram_id: int, repos: List[Dict[str, Any]], only_scheduled: bool = True):
    """No-op: Personal user repositories are no longer cached or stored."""
    return True

save_cached_repositories = cache_user_repositories

async def get_cached_repositories(telegram_id: int) -> Optional[List[Dict[str, Any]]]:
    """Returns empty list: Bot only operates on newly created schedule repos."""
    return []

# --- Schedule Management ---
async def save_schedule(schedule_data: Dict[str, Any]) -> str:
    db = MongoDB.get_db()
    schedule_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.schedules.update_one(
        {"schedule_id": schedule_data["schedule_id"]},
        {"$set": schedule_data},
        upsert=True
    )
    return schedule_data["schedule_id"]

async def get_user_schedules(telegram_id: int) -> List[Dict[str, Any]]:
    db = MongoDB.get_db()
    cursor = db.schedules.find({"telegram_id": telegram_id})
    return await cursor.to_list(length=100)

async def get_all_active_schedules() -> List[Dict[str, Any]]:
    db = MongoDB.get_db()
    cursor = db.schedules.find({"status": "active"})
    return await cursor.to_list(length=500)

async def delete_schedule(schedule_id: str, telegram_id: int) -> bool:
    db = MongoDB.get_db()
    res = await db.schedules.delete_one({"schedule_id": schedule_id, "telegram_id": telegram_id})
    return res.deleted_count > 0

async def update_schedule_status(schedule_id: str, status: str) -> bool:
    db = MongoDB.get_db()
    res = await db.schedules.update_one(
        {"schedule_id": schedule_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    return res.modified_count > 0

# --- Monitoring Management ---
async def save_monitoring_setting(telegram_id: int, monitored_repos: List[str]) -> bool:
    db = MongoDB.get_db()
    await db.monitoring_settings.update_one(
        {"telegram_id": telegram_id},
        {"$set": {
            "telegram_id": telegram_id,
            "monitored_repos": monitored_repos,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }},
        upsert=True
    )
    return True

async def get_monitoring_setting(telegram_id: int) -> List[str]:
    db = MongoDB.get_db()
    doc = await db.monitoring_settings.find_one({"telegram_id": telegram_id})
    if doc:
        return doc.get("monitored_repos", [])
    return []

# --- Webhook Events Log ---
async def log_webhook_event(event_type: str, payload: Dict[str, Any]):
    """Logs lightweight webhook event summary with datetime for 24h TTL auto-deletion."""
    db = MongoDB.get_db()
    repo_name = payload.get("repository", {}).get("full_name")
    sender = payload.get("sender", {}).get("login")
    action = payload.get("action")
    
    await db.webhook_events.insert_one({
        "event_type": event_type,
        "repo": repo_name,
        "sender": sender,
        "action": action,
        "created_at": datetime.now(timezone.utc)
    })

# --- Database Bloat Cleanup Helper ---
async def cleanup_database_bloat():
    """Drops legacy repositories_cache collection and purges heavy payloads."""
    try:
        db = MongoDB.get_db()
        # Delete heavy legacy webhook events storing full raw JSON payloads
        await db.webhook_events.delete_many({"payload": {"$exists": True}})
        
        # Permanently drop the entire bloated repositories_cache collection
        try:
            await db.repositories_cache.drop()
            logger.info("Purged repositories_cache collection.")
        except Exception:
            pass

        logger.info("Database bloat cleanup executed successfully.")
    except Exception as e:
        logger.warning(f"Error during database bloat cleanup: {e}")

# --- Admin Database Helpers ---
async def get_all_registered_users() -> List[Dict[str, Any]]:
    db = MongoDB.get_db()
    cursor = db.users.find({})
    return await cursor.to_list(length=5000)

async def get_admin_dashboard_stats() -> Dict[str, Any]:
    from datetime import timedelta
    db = MongoDB.get_db()
    total_users = await db.users.count_documents({})
    
    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(days=1)).isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    
    new_24h = await db.users.count_documents({"connected_at": {"$gte": cutoff_24h}})
    new_7d = await db.users.count_documents({"connected_at": {"$gte": cutoff_7d}})
    
    total_schedules = await db.schedules.count_documents({})
    active_schedules = await db.schedules.count_documents({"status": "active"})
    
    return {
        "total_users": total_users,
        "new_24h": new_24h,
        "new_7d": new_7d,
        "total_schedules": total_schedules,
        "active_schedules": active_schedules
    }


def get_main_admin_id() -> int:
    return int(os.getenv("ADMIN_ID", 1447828370))


async def get_admin_role(telegram_id: int) -> str:
    """Returns 'admin', 'subadmin', or 'user'."""
    if telegram_id == get_main_admin_id():
        return "admin"
    db = MongoDB.get_db()
    sub = await db.subadmins.find_one({"telegram_id": telegram_id})
    if sub:
        return "subadmin"
    return "user"


async def list_subadmins() -> List[Dict[str, Any]]:
    db = MongoDB.get_db()
    cursor = db.subadmins.find({}).sort("promoted_at", -1)
    subs = await cursor.to_list(length=100)
    res = []
    for s in subs:
        s_doc = dict(s)
        s_doc.pop("_id", None)
        u = await db.users.find_one({"telegram_id": s_doc["telegram_id"]})
        s_doc["github_username"] = u.get("github_username", "") if u else ""
        res.append(s_doc)
    return res


async def add_subadmin(telegram_id: int, promoted_by: int, note: str = "") -> Dict[str, Any]:
    db = MongoDB.get_db()
    doc = {
        "telegram_id": int(telegram_id),
        "promoted_by": int(promoted_by),
        "note": note.strip(),
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.subadmins.update_one(
        {"telegram_id": int(telegram_id)},
        {"$set": doc},
        upsert=True,
    )
    return doc


async def remove_subadmin(telegram_id: int) -> bool:
    db = MongoDB.get_db()
    res = await db.subadmins.delete_one({"telegram_id": int(telegram_id)})
    return res.deleted_count > 0


async def get_admin_users_overview() -> Dict[str, Any]:
    """Returns detailed user directory + aggregate total commits across all users."""
    db = MongoDB.get_db()
    users = await db.users.find({}).to_list(length=5000)
    schedules = await db.schedules.find({}).to_list(length=10000)
    subadmins = await db.subadmins.find({}).to_list(length=100)
    sub_ids = {int(s["telegram_id"]) for s in subadmins}
    main_id = get_main_admin_id()

    sched_by_user: Dict[int, List[Dict[str, Any]]] = {}
    total_bot_commits = 0
    for s in schedules:
        tid = s.get("telegram_id")
        if tid is not None:
            sched_by_user.setdefault(int(tid), []).append(s)
        total_bot_commits += int(s.get("total_executions") or 0)

    user_rows = []
    for u in users:
        tid = int(u["telegram_id"])
        u_scheds = sched_by_user.get(tid, [])
        active_cnt = sum(1 for s in u_scheds if s.get("status") == "active")
        u_commits = sum(int(s.get("total_executions") or 0) for s in u_scheds)
        accs = [a.get("github_username") for a in normalize_user_accounts(u) if a.get("github_username")]
        role = "admin" if tid == main_id else ("subadmin" if tid in sub_ids else "user")
        user_rows.append({
            "telegram_id": tid,
            "github_username": u.get("github_username", ""),
            "accounts": accs,
            "auth_method": (u.get("auth_method") or "oauth").upper(),
            "timezone": u.get("timezone", "Asia/Kolkata"),
            "notifications": u.get("notifications", True),
            "connected_at": u.get("connected_at", ""),
            "schedules_count": len(u_scheds),
            "active_schedules_count": active_cnt,
            "total_commits": u_commits,
            "role": role,
        })

    user_rows.sort(key=lambda r: r["total_commits"], reverse=True)
    return {
        "users": user_rows,
        "total_bot_commits": total_bot_commits,
    }

