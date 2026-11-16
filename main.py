# -*- coding: utf-8 -*-
import asyncio
from loguru import logger
import sys
import uvicorn

from config import settings
from core.binance_client import BinanceCopyTradeClient
from core.order_monitor import run_order_monitor
from core.telegram_bot import TelegramBot
from db.redis_store import RedisStore
from db.postgres_store import PostgresStore
from web.dashboard import app as web_app, setup as setup_web, broadcast_event


async def main():
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)
    logger.add("logs/monitor.log", rotation="10 MB", retention="7 days", level="INFO")

    logger.info("启动熬鹰资本仓位监控（订单流，不轮询持仓 REST）...")

    client = BinanceCopyTradeClient(
        settings.portfolio_id,
        proxy=settings.proxy,
    )
    redis_store = RedisStore(settings.redis_url)
    pg_store = PostgresStore(settings.database_url)

    await redis_store.connect()
    await pg_store.connect()

    async def get_positions():
        # 不再请求币安持仓 REST（易 90801003）；公开持仓常隐藏
        return []

    async def get_events(limit: int = 20):
        return await pg_store.get_recent_events(settings.portfolio_id, limit)

    async def get_stats():
        return await pg_store.get_stats(settings.portfolio_id)

    async def get_all_events(limit: int = 500):
        return await pg_store.get_all_events(settings.portfolio_id, limit)

    bot = TelegramBot(
        token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        get_positions=get_positions,
        get_events=get_events,
        get_stats=get_stats,
        proxy=settings.proxy,
    )

    setup_web(get_positions, get_events, get_all_events, proxy=settings.proxy)

    async def on_order(order: dict):
        await broadcast_event({
            "type": "order",
            "action": order.get("action"),
            "symbol": order.get("symbol"),
            "side": order.get("position_side"),
            "price": order.get("price"),
            "qty": order.get("qty"),
            "notional": order.get("notional"),
            "realized_pnl": order.get("realized_pnl"),
            "order_time": order.get("order_time"),
        })

    web_config = uvicorn.Config(
        web_app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="warning",
    )
    web_server = uvicorn.Server(web_config)

    logger.info(f"Web Dashboard: http://{settings.web_host}:{settings.web_port}")
    logger.info("监控信号源: order-history；行情: Binance 合约 WebSocket markPrice")

    try:
        await asyncio.gather(
            run_order_monitor(client, redis_store, pg_store, bot, on_order=on_order),
            bot.start_polling(),
            web_server.serve(),
        )
    finally:
        await client.close()
        await redis_store.close()
        await pg_store.close()
        await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("手动停止")
