# -*- coding: utf-8 -*-
"""
订单流监控（替代公开持仓 REST）。

说明：币安不对「他人带单」提供公开 WebSocket；持仓 REST 易触发 90801003。
本模块以 order-history 为唯一信号源，长驻轮询 + 断线/限流退避，
并把新订单推到 TG / DB / 可选 WebSocket 广播。
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, List, Optional

from loguru import logger

from config import settings
from core.binance_client import BinanceCopyTradeClient
from core.diff import filter_new_orders, order_signature
from core.telegram_bot import TelegramBot
from db.postgres_store import PostgresStore
from db.redis_store import RedisStore

OrderCallback = Callable[[dict], Awaitable[None]]


async def run_order_monitor(
    client: BinanceCopyTradeClient,
    redis_store: RedisStore,
    pg_store: PostgresStore,
    bot: TelegramBot,
    on_order: Optional[OrderCallback] = None,
):
    """长驻订单监控循环。"""
    consecutive_failures = 0
    alert_sent = False
    threshold = settings.health_fail_threshold
    interval = max(15, settings.poll_interval)

    # 启动种子
    try:
        await bot.send_snapshot([])
        await _seed_orders(client, redis_store)
    except Exception as e:
        logger.error(f"订单监控启动失败: {e}")

    while True:
        try:
            pushed = await _poll_once(client, redis_store, pg_store, bot, on_order)
            if consecutive_failures >= threshold and alert_sent:
                await bot.send_health_recovery(consecutive_failures)
            consecutive_failures = 0
            alert_sent = False
            # 有推送时稍作间隔，避免 TG/接口打太猛
            sleep_for = interval if pushed == 0 else max(10, interval // 2)
        except Exception as e:
            consecutive_failures += 1
            error_msg = str(e)
            logger.warning(f"订单监控异常 (连续第 {consecutive_failures} 次): {error_msg}")
            if consecutive_failures >= threshold and not alert_sent:
                try:
                    await bot.send_health_alert(consecutive_failures, error_msg)
                    alert_sent = True
                except Exception as alert_err:
                    logger.error(f"发送健康告警失败: {alert_err}")
            # 限流/繁忙时指数退避
            sleep_for = min(300, interval * min(consecutive_failures, 4))

        await asyncio.sleep(sleep_for)


async def _seed_orders(client: BinanceCopyTradeClient, redis_store: RedisStore):
    orders = await client.fetch_orders(page_size=10)
    seen = await redis_store.get_seen_orders(settings.portfolio_id)
    if seen:
        logger.info(f"订单监控已有 {len(seen)} 条指纹，跳过种子")
        return
    sigs = [order_signature(o) for o in orders]
    await redis_store.add_seen_orders(settings.portfolio_id, sigs)
    logger.info(f"订单监控种子完成，已记录 {len(sigs)} 条指纹")


async def _poll_once(
    client: BinanceCopyTradeClient,
    redis_store: RedisStore,
    pg_store: PostgresStore,
    bot: TelegramBot,
    on_order: Optional[OrderCallback],
) -> int:
    orders = await client.fetch_orders(page_size=10)
    seen = await redis_store.get_seen_orders(settings.portfolio_id)
    new_orders, new_sigs = filter_new_orders(orders, seen)

    if not seen:
        seed_sigs = new_sigs or [order_signature(o) for o in orders]
        await redis_store.add_seen_orders(settings.portfolio_id, seed_sigs)
        logger.info(f"订单监控种子完成，已记录 {len(seed_sigs)} 条指纹")
        return 0

    if not new_sigs:
        return 0

    await redis_store.add_seen_orders(settings.portfolio_id, new_sigs)

    pushed = 0
    for order in new_orders:
        if float(order.get("notional") or 0) < settings.min_notional_usdt:
            continue
        await bot.send_order_event(order)
        await pg_store.save_event(
            settings.portfolio_id,
            order.get("event_type") or "open",
            order,
            extra={
                "source": "order_history",
                "action": order.get("action"),
                "order_type": order.get("order_type"),
                "realized_pnl": order.get("realized_pnl"),
            },
        )
        if on_order:
            try:
                await on_order(order)
            except Exception as e:
                logger.debug(f"订单回调失败: {e}")
        pushed += 1

    if pushed:
        logger.info(f"订单监控推送 {pushed} 条（新订单 {len(new_orders)}）")
    return pushed
