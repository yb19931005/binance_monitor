from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message
from loguru import logger
from typing import Dict, Any, Callable, Awaitable, Optional
import asyncio
import time
from collections import defaultdict


class RateLimiter:
    """简单内存限流：每用户每命令在窗口期内最多 N 次"""

    def __init__(self, max_calls: int = 5, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window = window_seconds
        # user_id -> command -> list of timestamps
        self.records: Dict[int, Dict[str, list]] = defaultdict(lambda: defaultdict(list))

    def is_allowed(self, user_id: int, command: str) -> bool:
        now = time.time()
        timestamps = self.records[user_id][command]

        # 清理过期记录
        timestamps[:] = [t for t in timestamps if now - t < self.window]

        if len(timestamps) >= self.max_calls:
            return False

        timestamps.append(now)
        return True

    def remaining(self, user_id: int, command: str) -> int:
        now = time.time()
        timestamps = self.records[user_id][command]
        valid = [t for t in timestamps if now - t < self.window]
        return max(0, self.max_calls - len(valid))


class TelegramBot:
    """同时负责主动推送和命令响应"""

    def __init__(
        self,
        token: str,
        chat_id: str,
        get_positions: Callable[[], Awaitable[list]],
        get_events: Callable[[int], Awaitable[list]],
        get_stats: Callable[[], Awaitable[dict]],
        rate_limit_calls: int = 5,
        rate_limit_window: int = 60,
        proxy: Optional[str] = None,
    ):
        session = AiohttpSession(proxy=proxy) if proxy else None
        self.bot = Bot(
            token=token,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        if proxy:
            logger.info(f"Telegram Bot 已启用代理: {proxy}")
        self.chat_id = chat_id
        self.dp = Dispatcher()

        self.get_positions = get_positions
        self.get_events = get_events
        self.get_stats = get_stats

        # 限流器：默认每用户每命令 60 秒内最多 5 次
        self.limiter = RateLimiter(max_calls=rate_limit_calls, window_seconds=rate_limit_window)

        self._register_handlers()

    def _check_rate_limit(self, message: Message, command: str) -> bool:
        user_id = message.from_user.id if message.from_user else 0
        if not self.limiter.is_allowed(user_id, command):
            remaining = self.limiter.remaining(user_id, command)
            asyncio.create_task(
                message.answer(
                    f"⏳ 操作过于频繁，请稍后再试\n"
                    f"（{command} 每分钟限 {self.limiter.max_calls} 次）"
                )
            )
            logger.warning(f"Rate limit hit: user={user_id} cmd={command}")
            return False
        return True

    def _register_handlers(self):
        @self.dp.message(Command("start", "help"))
        async def cmd_help(message: types.Message):
            if not self._check_rate_limit(message, "help"):
                return
            text = (
                "<b>熬鹰资本仓位监控 Bot</b>\n\n"
                "可用命令：\n"
                "/positions - 当前持仓\n"
                "/history [数量] - 最近开平仓记录（默认20条）\n"
                "/stats - 监控统计\n"
                "/help - 帮助\n\n"
                "系统会自动推送开仓、平仓、加仓/减仓事件。\n"
                "命令限流：每用户每分钟最多 5 次。"
            )
            await message.answer(text)

        @self.dp.message(Command("positions"))
        async def cmd_positions(message: types.Message):
            if not self._check_rate_limit(message, "positions"):
                return
            try:
                positions = await self.get_positions()
                if not positions:
                    await message.answer("📭 当前无持仓")
                    return

                lines = ["📋 <b>当前持仓</b>\n"]
                total_notional = 0
                total_pnl = 0
                for p in sorted(positions, key=lambda x: x.get("notional", 0), reverse=True):
                    lines.append(self._format_position(p))
                    lines.append("")
                    total_notional += p.get("notional", 0)
                    total_pnl += p.get("unrealized_pnl", 0)

                lines.append(f"📊 合计名义: {total_notional:,.0f} U | 浮盈: {total_pnl:+,.0f} U")
                await message.answer("\n".join(lines))
            except Exception as e:
                logger.exception(e)
                await message.answer("获取持仓失败，请稍后再试")

        @self.dp.message(Command("history"))
        async def cmd_history(message: types.Message):
            if not self._check_rate_limit(message, "history"):
                return
            try:
                args = message.text.split()
                limit = 20
                if len(args) > 1 and args[1].isdigit():
                    limit = min(int(args[1]), 50)

                events = await self.get_events(limit)
                if not events:
                    await message.answer("暂无历史记录")
                    return

                lines = [f"📜 <b>最近 {len(events)} 条事件</b>\n"]
                for e in events:
                    emoji = {"open": "🚀", "close": "🏁", "change": "📊"}.get(e["event_type"], "•")
                    side = "多" if e.get("side") == "LONG" else "空"
                    time_str = e["created_at"].strftime("%m-%d %H:%M") if e.get("created_at") else ""
                    notional = float(e.get("notional") or 0)
                    lines.append(
                        f"{emoji} {time_str} {e['symbol']} {side} {e.get('leverage',1)}x "
                        f"{notional:,.0f}U"
                    )

                await message.answer("\n".join(lines))
            except Exception as e:
                logger.exception(e)
                await message.answer("查询历史失败")

        @self.dp.message(Command("stats"))
        async def cmd_stats(message: types.Message):
            if not self._check_rate_limit(message, "stats"):
                return
            try:
                stats = await self.get_stats()
                text = (
                    "<b>监控统计</b>\n\n"
                    f"开仓事件: {stats.get('opens', 0)}\n"
                    f"平仓事件: {stats.get('closes', 0)}\n"
                    f"变化事件: {stats.get('changes', 0)}\n"
                    f"总事件数: {stats.get('total', 0)}\n"
                    f"最近事件: {stats.get('last_event') or '无'}"
                )
                await message.answer(text)
            except Exception as e:
                logger.exception(e)
                await message.answer("获取统计失败")

    def _format_position(self, pos: Dict[str, Any]) -> str:
        side_emoji = "🟢" if pos.get("side") == "LONG" else "🔴"
        side_text = "多" if pos.get("side") == "LONG" else "空"
        notional = pos.get("notional", 0)
        pnl = pos.get("unrealized_pnl", 0)
        pnl_sign = "+" if pnl >= 0 else ""

        return (
            f"{side_emoji} <b>{pos['symbol']}</b> {side_text} {pos.get('leverage', 1)}x\n"
            f"持仓: {notional:,.0f} U | 开仓 {pos.get('entry_price', 0):,.4f}\n"
            f"浮盈: {pnl_sign}{pnl:,.2f} U ({pos.get('roe', 0)*100:+.2f}%)"
        )

    # ========== 主动推送方法 ==========
    async def send_open(self, pos: Dict[str, Any]):
        text = "🚀 <b>熬鹰新开仓</b>\n\n" + self._format_position(pos)
        await self._send(text)

    async def send_close(self, pos: Dict[str, Any]):
        text = "🏁 <b>熬鹰平仓</b>\n\n" + self._format_position(pos)
        await self._send(text)

    async def send_change(self, change: Dict[str, Any]):
        new = change["new"]
        delta = change.get("delta_amount", 0)
        action = "加仓" if delta > 0 else "减仓"
        text = (
            f"📊 <b>熬鹰{action}</b>\n\n"
            f"{self._format_position(new)}\n"
            f"数量变化: {delta:+,.2f}"
        )
        await self._send(text)

    async def send_order_event(self, order: Dict[str, Any]):
        """基于订单记录推送（与 App 最新交易记录一致）"""
        action = order.get("action") or ""
        emoji = {
            "开多": "🚀",
            "开空": "🔻",
            "平多": "🏁",
            "平空": "🏁",
        }.get(action, "📊")
        pnl = float(order.get("realized_pnl") or 0)
        lines = [
            f"{emoji} <b>熬鹰{action}</b>",
            "",
            f"<b>{order.get('symbol')}</b> 永续",
            f"均价: {float(order.get('price') or 0):,.4f}",
            f"数量: {float(order.get('qty') or 0):,.4f}",
            f"名义: {float(order.get('notional') or 0):,.2f} U",
        ]
        if order.get("order_type"):
            lines.append(f"类型: {order.get('order_type')}")
        if abs(pnl) > 1e-8:
            lines.append(f"已实现盈亏: {pnl:+,.2f} U")
        await self._send("\n".join(lines))

    async def send_snapshot(self, positions: list):
        if not positions:
            text = "📭 熬鹰当前无公开持仓（已启用订单记录监控）"
        else:
            lines = ["📋 <b>启动快照</b>\n"]
            for p in sorted(positions, key=lambda x: x.get("notional", 0), reverse=True):
                lines.append(self._format_position(p))
                lines.append("")
            text = "\n".join(lines)
        await self._send(text)

    async def send_health_alert(self, fail_count: int, error: str):
        """连续失败告警"""
        text = (
            f"⚠️ <b>监控健康告警</b>\n\n"
            f"连续失败 <b>{fail_count}</b> 次\n"
            f"最近错误: <code>{error[:200]}</code>\n\n"
            f"请检查网络、代理或币安接口状态。"
        )
        await self._send(text)

    async def send_health_recovery(self, fail_count: int):
        """恢复通知"""
        text = (
            f"✅ <b>监控已恢复</b>\n\n"
            f"此前连续失败 {fail_count} 次，现已正常拉取。"
        )
        await self._send(text)

    async def _send(self, text: str):
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                disable_web_page_preview=True,
            )
            logger.info("TG推送成功")
        except Exception as e:
            logger.error(f"TG推送失败: {e}")

    async def start_polling(self):
        """启动命令监听（应与监控循环并发运行）"""
        logger.info("Telegram Bot 命令监听已启动")
        await self.dp.start_polling(self.bot)

    async def close(self):
        await self.bot.session.close()
