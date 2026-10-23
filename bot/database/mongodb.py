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
            
            # Monitoring settings index
            await db.monitoring_settings.create_index("telegram_id", unique=True)
            
            # Repositories cache index
            await db.repositories_cache.create_index("telegram_id")
            
            # Webhook events TTL index (automatically delete entries after 24 hours = 86400s)
            await db.webhook_events.create_index("created_at", expireAfterSeconds=86400)

            logger.info("MongoDB indexes initialized successfully.")
            
            # Run cleanup on startup to purge any existing bloated records
            await cleanup_database_bloat()
        except Exception as e:
            logger.warning(f"MongoDB index initialization error: {e}")

# Database Helper Functions

# --- User Management ---
async def save_user(
    telegram_id: int,
    github_id: int,
    github_username: str,
    encrypted_token: str,
    auth_method: str = "oauth",
    tz: str = "Asia/Kolkata"
) -> Dict[str, Any]:
    db = MongoDB.get_db()
    user_data = {
        "telegram_id": telegram_id,
        "github_id": github_id,
        "github_username": github_username,
        "encrypted_token": encrypted_token,
        "auth_method": auth_method,
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "timezone": tz,
        "notifications": True,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.users.update_one(
        {"telegram_id": telegram_id},
        {"$set": user_data},
        upsert=True
    )
    return user_data

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
    # Delete user and associated settings/cache
    await db.users.delete_one({"telegram_id": telegram_id})
    await db.repositories_cache.delete_many({"telegram_id": telegram_id})
    await db.schedules.delete_many({"telegram_id": telegram_id})
    await db.monitoring_settings.delete_one({"telegram_id": telegram_id})
    return True

# --- Cache & Connected Repositories Management ---
async def cache_user_repositories(telegram_id: int, repos: List[Dict[str, Any]], only_scheduled: bool = True):
    """Caches only connected (scheduled) repositories to save MongoDB storage."""
    db = MongoDB.get_db()
    
    if only_scheduled:
        user_schedules = await get_user_schedules(telegram_id)
        scheduled_names = set()
        for s in user_schedules:
            if s.get("repo"):
                scheduled_names.add(s["repo"])
            multi_plan = s.get("multi_repo_plan") or {}
            for day_data in multi_plan.values():
                if isinstance(day_data, dict):
                    for r in day_data.get("repos", []):
                        scheduled_names.add(r)
        
        filtered = [
            r for r in repos 
            if isinstance(r, dict) and (r.get("full_name") in scheduled_names or r.get("name") in scheduled_names)
        ]
    else:
        filtered = repos

    if not filtered:
        # If user has no scheduled repos, delete cache document completely
        await db.repositories_cache.delete_one({"telegram_id": telegram_id})
        return

    pruned_repos = []
    for r in filtered:
        pruned_repos.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "full_name": r.get("full_name") or r.get("name"),
            "private": bool(r.get("private")),
            "description": (r.get("description") or "")[:200],
            "html_url": r.get("html_url"),
            "default_branch": r.get("default_branch", "main"),
            "updated_at": r.get("updated_at")
        })
    await db.repositories_cache.update_one(
        {"telegram_id": telegram_id},
        {"$set": {
            "telegram_id": telegram_id,
            "repositories": pruned_repos,
            "cached_at": datetime.now(timezone.utc).isoformat()
        }},
        upsert=True
    )

save_cached_repositories = cache_user_repositories

async def get_cached_repositories(telegram_id: int) -> Optional[List[Dict[str, Any]]]:
    """Returns connected/scheduled repositories from cache, or None if empty."""
    db = MongoDB.get_db()
    doc = await db.repositories_cache.find_one({"telegram_id": telegram_id})
    if doc:
        return doc.get("repositories", [])
    return None

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
    # If user has no more schedules left, clear their repo cache document
    remaining = await get_user_schedules(telegram_id)
    if not remaining:
        await db.repositories_cache.delete_one({"telegram_id": telegram_id})
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
    """Purges legacy raw webhook event payloads and cleans unscheduled repo cache bloat."""
    try:
        db = MongoDB.get_db()
        # Delete heavy legacy webhook events storing full raw JSON payloads
        await db.webhook_events.delete_many({"payload": {"$exists": True}})
        
        # Clean repositories_cache: remove any entries for users who have no schedules,
        # and retain ONLY scheduled repos for users who have active schedules.
        cursor = db.repositories_cache.find({})
        async for doc in cursor:
            tg_id = doc.get("telegram_id")
            if not tg_id:
                await db.repositories_cache.delete_one({"_id": doc["_id"]})
                continue

            user_schedules = await get_user_schedules(tg_id)
            if not user_schedules:
                # No scheduled repos -> remove cache completely
                await db.repositories_cache.delete_one({"_id": doc["_id"]})
            else:
                scheduled_names = set()
                for s in user_schedules:
                    if s.get("repo"):
                        scheduled_names.add(s["repo"])
                    multi_plan = s.get("multi_repo_plan") or {}
                    for day_data in multi_plan.values():
                        if isinstance(day_data, dict):
                            for r in day_data.get("repos", []):
                                scheduled_names.add(r)

                existing_repos = doc.get("repositories", [])
                pruned = [
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "full_name": r.get("full_name") or r.get("name"),
                        "private": bool(r.get("private")),
                        "description": (r.get("description") or "")[:200],
                        "html_url": r.get("html_url"),
                        "default_branch": r.get("default_branch", "main"),
                        "updated_at": r.get("updated_at")
                    }
                    for r in existing_repos
                    if isinstance(r, dict) and (r.get("full_name") in scheduled_names or r.get("name") in scheduled_names)
                ]
                if pruned:
                    await db.repositories_cache.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"repositories": pruned}}
                    )
                else:
                    await db.repositories_cache.delete_one({"_id": doc["_id"]})
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

