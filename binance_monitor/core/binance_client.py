import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
from typing import List, Dict, Any, Optional


class BinanceCopyTradeClient:
    """非官方 bapi 客户端，用于拉取公开带单持仓"""

    BASE_URL = "https://www.binance.com/bapi/futures/v1"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "clienttype": "web",
        "lang": "zh-CN",
        "Origin": "https://www.binance.com",
        "Referer": "https://www.binance.com/zh-CN/copy-trading/lead-details",
    }

    def __init__(self, portfolio_id: str, proxy: Optional[str] = None):
        self.portfolio_id = portfolio_id
        self.client = httpx.AsyncClient(
            timeout=20.0,
            headers=self.HEADERS,
            follow_redirects=True,
            proxy=proxy,  # None 时不使用代理
        )
        if proxy:
            logger.info(f"Binance 客户端已启用代理: {proxy}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_positions(self) -> List[Dict[str, Any]]:
        """拉取当前公开持仓"""
        url = f"{self.BASE_URL}/friendly/future/copy-trade/lead-data/positions"
        params = {"portfolioId": self.portfolio_id}

        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != "000000":
                logger.warning(f"接口返回非成功: {data}")
                return []

            raw_positions = data.get("data") or []
            if not raw_positions:
                logger.info("接口返回空持仓（可能已隐藏或当前无仓）")
                return []

            # 调试：首次成功时打印原始字段名，便于核对
            if raw_positions and not hasattr(self, "_logged_keys"):
                logger.debug(f"原始字段: {list(raw_positions[0].keys())}")
                self._logged_keys = True

            positions = []

            for p in raw_positions:
                try:
                    amount = float(p.get("positionAmount") or 0)
                    if abs(amount) < 1e-8:
                        continue

                    # 实测字段（2026 公开 bapi）：
                    # symbol, positionAmount, entryPrice, markPrice,
                    # leverage, positionSide, unrealizedProfit
                    # 注意：ROE / updateTime 等字段可能不存在
                    entry = float(p.get("entryPrice") or 0)
                    mark = float(p.get("markPrice") or 0)
                    upnl = float(p.get("unrealizedProfit") or 0)

                    # ROE 优先用接口字段，否则自行计算
                    roe = p.get("unrealizedProfitRatio")
                    if roe is None and entry > 0 and amount != 0:
                        # 近似 ROE = unrealizedProfit / (abs(amount) * entry / leverage)
                        lev = float(p.get("leverage") or 1)
                        margin = abs(amount) * entry / max(lev, 1)
                        roe = upnl / margin if margin > 0 else 0
                    else:
                        roe = float(roe or 0)

                    positions.append({
                        "symbol": p.get("symbol"),
                        "side": p.get("positionSide") or ("LONG" if amount > 0 else "SHORT"),
                        "leverage": int(float(p.get("leverage") or 1)),
                        "position_amount": amount,
                        "entry_price": entry,
                        "mark_price": mark,
                        "unrealized_pnl": upnl,
                        "roe": roe,
                        "notional": abs(amount) * (mark or entry),
                        "update_time": p.get("updateTime") or p.get("update_time"),
                    })
                except (ValueError, TypeError) as e:
                    logger.debug(f"解析仓位失败: {p} | {e}")
                    continue

            logger.info(f"拉取到 {len(positions)} 个有效仓位")
            return positions

        except Exception as e:
            logger.error(f"拉取持仓失败: {e}")
            raise

    async def close(self):
        await self.client.aclose()
