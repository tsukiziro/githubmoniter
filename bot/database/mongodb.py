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
            
            # Webhook events index
            await db.webhook_events.create_index("created_at")

            logger.info("MongoDB indexes initialized successfully.")
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

# --- Cache Management ---
async def cache_user_repositories(telegram_id: int, repos: List[Dict[str, Any]]):
    db = MongoDB.get_db()
    await db.repositories_cache.update_one(
        {"telegram_id": telegram_id},
        {"$set": {
            "telegram_id": telegram_id,
            "repositories": repos,
            "cached_at": datetime.now(timezone.utc).isoformat()
        }},
        upsert=True
    )

async def get_cached_repositories(telegram_id: int) -> Optional[List[Dict[str, Any]]]:
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
    db = MongoDB.get_db()
    await db.webhook_events.insert_one({
        "event_type": event_type,
        "payload": payload,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

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
