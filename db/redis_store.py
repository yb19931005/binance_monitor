import redis.asyncio as redis
from loguru import logger
import orjson
from typing import Optional, Dict, Any, List, Set


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

    async def _get_seen_list(self, key: str) -> List[str]:
        data = await self.client.get(key)
        if not data:
            return []
        try:
            items = orjson.loads(data)
            return [str(x) for x in items] if isinstance(items, list) else []
        except Exception:
            return []

    async def get_seen_set(self, key: str) -> Set[str]:
        return set(await self._get_seen_list(key))

    async def add_seen_set(self, key: str, new_items: List[str], max_size: int = 500):
        """追加去重签名，保留最近 max_size 条"""
        if not new_items:
            return
        ordered = await self._get_seen_list(key)
        existing = set(ordered)
        for item in new_items:
            if item not in existing:
                ordered.append(item)
                existing.add(item)
        if len(ordered) > max_size:
            ordered = ordered[-max_size:]
        await self.client.set(key, orjson.dumps(ordered), ex=86400 * 14)

    async def get_seen_orders(self, portfolio_id: str) -> Set[str]:
        return await self.get_seen_set(f"orders_seen:{portfolio_id}")

    async def add_seen_orders(self, portfolio_id: str, signatures: List[str]):
        await self.add_seen_set(f"orders_seen:{portfolio_id}", signatures)
