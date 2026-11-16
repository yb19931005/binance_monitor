from aiogram import Bot
from aiogram.enums import ParseMode
from loguru import logger
from typing import Dict, Any


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id

    async def close(self):
        await self.bot.session.close()

    def _format_position(self, pos: Dict[str, Any], prefix: str = "") -> str:
        side_emoji = "🟢" if pos.get("side") == "LONG" else "🔴"
        side_text = "多" if pos.get("side") == "LONG" else "空"
        notional = pos.get("notional", 0)
        pnl = pos.get("unrealized_pnl", 0)
        pnl_sign = "+" if pnl >= 0 else ""

        return (
            f"{prefix}{side_emoji} <b>{pos['symbol']}</b> {side_text} {pos.get('leverage', 1)}x\n"
            f"持仓量: {notional:,.0f} U\n"
            f"开仓价: {pos.get('entry_price', 0):,.4f}\n"
            f"标记价: {pos.get('mark_price', 0):,.4f}\n"
            f"浮盈: {pnl_sign}{pnl:,.2f} U ({pos.get('roe', 0)*100:+.2f}%)"
        )

    async def send_open(self, pos: Dict[str, Any]):
        text = "🚀 <b>熬鹰新开仓</b>\n\n" + self._format_position(pos)
        await self._send(text)

    async def send_close(self, pos: Dict[str, Any]):
        text = "🏁 <b>熬鹰平仓</b>\n\n" + self._format_position(pos)
        await self._send(text)

    async def send_change(self, change: Dict[str, Any]):
        old = change["old"]
        new = change["new"]
        delta = change.get("delta_amount", 0)
        action = "加仓" if delta > 0 else "减仓"
        text = (
            f"📊 <b>熬鹰{action}</b>\n\n"
            f"{self._format_position(new, '当前: ')}\n"
            f"变化数量: {delta:+,.2f}"
        )
        await self._send(text)

    async def send_snapshot(self, positions: list):
        if not positions:
            text = "📭 熬鹰当前无持仓"
        else:
            lines = ["📋 <b>熬鹰当前持仓快照</b>\n"]
            for p in sorted(positions, key=lambda x: x.get("notional", 0), reverse=True):
                lines.append(self._format_position(p))
                lines.append("")
            text = "\n".join(lines)
        await self._send(text)

    async def _send(self, text: str):
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info("TG消息发送成功")
        except Exception as e:
            logger.error(f"TG发送失败: {e}")
