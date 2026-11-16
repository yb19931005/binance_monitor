import asyncpg
from loguru import logger
from typing import List, Dict, Any
import json


class PostgresStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.dsn, min_size=2, max_size=10)
        logger.info("PostgreSQL 连接成功")
        await self.init_tables()

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def init_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS position_events (
                    id SERIAL PRIMARY KEY,
                    portfolio_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,  -- open / close / change
                    symbol TEXT NOT NULL,
                    side TEXT,
                    leverage INT,
                    position_amount NUMERIC,
                    entry_price NUMERIC,
                    notional NUMERIC,
                    unrealized_pnl NUMERIC,
                    extra JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_position_events_portfolio_time 
                ON position_events(portfolio_id, created_at DESC);
            """)
            logger.info("表初始化完成")

    async def save_event(
        self,
        portfolio_id: str,
        event_type: str,
        position: Dict[str, Any],
        extra: Dict = None,
    ):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO position_events 
                (portfolio_id, event_type, symbol, side, leverage, position_amount, 
                 entry_price, notional, unrealized_pnl, extra)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
                portfolio_id,
                event_type,
                position.get("symbol"),
                position.get("side"),
                position.get("leverage"),
                position.get("position_amount"),
                position.get("entry_price"),
                position.get("notional"),
                position.get("unrealized_pnl"),
                json.dumps(extra or {}),
            )

    async def get_recent_events(self, portfolio_id: str, limit: int = 20) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT event_type, symbol, side, leverage, position_amount,
                       entry_price, notional, unrealized_pnl, created_at, extra
                FROM position_events
                WHERE portfolio_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            """, portfolio_id, limit)
            return [dict(r) for r in rows]

    async def get_all_events(self, portfolio_id: str, limit: int = 1000) -> List[Dict]:
        """导出用，最多返回 limit 条"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT event_type, symbol, side, leverage, position_amount,
                       entry_price, notional, unrealized_pnl, created_at, extra
                FROM position_events
                WHERE portfolio_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            """, portfolio_id, limit)
            return [dict(r) for r in rows]

    async def get_stats(self, portfolio_id: str) -> Dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    COUNT(*) FILTER (WHERE event_type = 'open') as opens,
                    COUNT(*) FILTER (WHERE event_type = 'close') as closes,
                    COUNT(*) FILTER (WHERE event_type = 'change') as changes,
                    COUNT(*) as total,
                    MAX(created_at) as last_event
                FROM position_events
                WHERE portfolio_id = $1
            """, portfolio_id)
            return dict(row) if row else {}
