import os
import json
import time
import logging
from typing import Optional
import redis.asyncio as aioredis
logger = logging.getLogger("queuestorm.database")
class CacheManager:
    def __init__(self):
        self.client = None
        self.in_memory_db = {}                                                 
        self.is_connected = False
    async def connect(self):
        """Asynchronously connect and test the Redis client."""
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        try:
            self.client = aioredis.from_url(
                redis_url, 
                socket_timeout=1.0, 
                socket_connect_timeout=1.0,
                decode_responses=True
            )
            await self.client.ping()
            self.is_connected = True
            logger.info(f"Successfully connected to Redis cache: {redis_url}")
        except Exception as e:
            self.is_connected = False
            self.client = None
            logger.warning(f"Could not connect to Redis: {e}. Falling back to TTL in-memory cache.")
    async def get(self, key: str) -> Optional[dict]:
        if self.is_connected and self.client:
            try:
                data = await self.client.get(key)
                if data:
                    return json.loads(data)
            except Exception as e:
                logger.warning(f"Redis get failed: {e}")
        if key in self.in_memory_db:
            value, expire_at = self.in_memory_db[key]
            if time.time() < expire_at:
                return value
            else:
                del self.in_memory_db[key]
        return None
    async def set(self, key: str, value: dict, expire_seconds: int = 3600):
        if self.is_connected and self.client:
            try:
                await self.client.setex(key, expire_seconds, json.dumps(value))
                return
            except Exception as e:
                logger.warning(f"Redis set failed: {e}")
        expire_at = time.time() + expire_seconds
        self.in_memory_db[key] = (value, expire_at)
        if len(self.in_memory_db) > 1000:
            now = time.time()
            expired_keys = [k for k, (_, exp) in self.in_memory_db.items() if now >= exp]
            for k in expired_keys:
                del self.in_memory_db[k]
cache_manager = CacheManager()