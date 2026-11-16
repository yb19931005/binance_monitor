import asyncio
from loguru import logger
import sys
import uvicorn

from config import settings
from core.binance_client import BinanceCopyTradeClient
from core.diff import positions_to_map, diff_positions
from core.telegram_bot import TelegramBot
from db.redis_store import RedisStore
from db.postgres_store import PostgresStore
from web.dashboard import app as web_app, setup as setup_web


async def monitor_loop(
    client: BinanceCopyTradeClient,
    redis_store: RedisStore,
    pg_store: PostgresStore,
    bot: TelegramBot,
):
    """仓位监控主循环"""
    consecutive_failures = 0
    alert_sent = False
    threshold = settings.health_fail_threshold

    # 启动时推送一次快照
    try:
        initial = await client.fetch_positions()
        await bot.send_snapshot(initial)
        await redis_store.set_last_positions(settings.portfolio_id, positions_to_map(initial))
    except Exception as e:
        logger.error(f"初始快照失败: {e}")

    while True:
        try:
            positions = await client.fetch_positions()
            new_map = positions_to_map(positions)
            old_map = await redis_store.get_last_positions(settings.portfolio_id)

            opened, closed, changed = diff_positions(
                old_map, new_map, min_notional=settings.min_notional_usdt
            )

            for pos in opened:
                await bot.send_open(pos)
                await pg_store.save_event(settings.portfolio_id, "open", pos)

            for pos in closed:
                await bot.send_close(pos)
                await pg_store.save_event(settings.portfolio_id, "close", pos)

            for ch in changed:
                await bot.send_change(ch)
                await pg_store.save_event(
                    settings.portfolio_id, "change", ch["new"],
                    extra={"delta_amount": ch.get("delta_amount")}
                )

            if opened or closed or changed or not old_map:
                await redis_store.set_last_positions(settings.portfolio_id, new_map)

            # 成功：重置失败计数，若之前告警过则发恢复通知
            if consecutive_failures >= threshold and alert_sent:
                await bot.send_health_recovery(consecutive_failures)
            consecutive_failures = 0
            alert_sent = False

        except Exception as e:
            consecutive_failures += 1
            error_msg = str(e)
            logger.exception(f"监控循环异常 (连续第 {consecutive_failures} 次): {error_msg}")

            # 达到阈值且尚未告警时推送
            if consecutive_failures >= threshold and not alert_sent:
                try:
                    await bot.send_health_alert(consecutive_failures, error_msg)
                    alert_sent = True
                except Exception as alert_err:
                    logger.error(f"发送健康告警失败: {alert_err}")

        await asyncio.sleep(settings.poll_interval)


async def main():
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)
    logger.add("logs/monitor.log", rotation="10 MB", retention="7 days", level="INFO")

    logger.info("启动熬鹰资本仓位监控...")

    client = BinanceCopyTradeClient(
        settings.portfolio_id,
        proxy=settings.proxy,
    )
    redis_store = RedisStore(settings.redis_url)
    pg_store = PostgresStore(settings.database_url)

    await redis_store.connect()
    await pg_store.connect()

    # 供 Bot 命令使用的查询函数
    async def get_positions():
        return await client.fetch_positions()

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
    )

    # 注入 Web Dashboard 依赖
    setup_web(get_positions, get_events, get_all_events)

    # Web 服务器配置
    web_config = uvicorn.Config(
        web_app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="warning",
    )
    web_server = uvicorn.Server(web_config)

    logger.info(f"Web Dashboard: http://{settings.web_host}:{settings.web_port}")

    # 并发运行：监控循环 + Bot 命令 + Web Dashboard
    try:
        await asyncio.gather(
            monitor_loop(client, redis_store, pg_store, bot),
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
