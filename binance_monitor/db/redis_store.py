import redis.asyncio as redis
from loguru import logger
import orjson
from typing import Optional, Dict, Any


class RedisStore:
    def __init__(self, url: str):
        self.url = url
        self.client: Optional[redis.Redis] = None

    async def connect(self):
        self.client = redis.from_url(self.url, decode_responses=False)
        await self.client.ping()
        logger.info("Redis 连接成功")

    async def close(self):
        if self.client:
            await self.client.aclose()

    async def get_last_positions(self, portfolio_id: str) -> Dict[str, Dict]:
        key = f"positions:{portfolio_id}"
        data = await self.client.get(key)
        if not data:
            return {}
        try:
            return orjson.loads(data)
        except Exception:
            return {}

    async def set_last_positions(self, portfolio_id: str, positions_map: Dict[str, Dict]):
        key = f"positions:{portfolio_id}"
        await self.client.set(key, orjson.dumps(positions_map), ex=86400 * 7)
