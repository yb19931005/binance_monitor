# -*- coding: utf-8 -*-
import time
import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
from typing import List, Dict, Any, Optional


class BinanceCopyTradeClient:
    """非官方 bapi 客户端：公开持仓 + 订单记录"""

    BASE_URL = "https://www.binance.com/bapi/futures/v1"
    # 持仓 REST 易触发 90801003，本地缓存降低请求频率
    POSITIONS_CACHE_TTL = 180
    POSITIONS_RATE_LIMIT_COOLDOWN = 300

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "clienttype": "web",
        "lang": "zh-CN",
        "Origin": "https://www.binance.com",
        "Referer": "https://www.binance.com/zh-CN/copy-trading/lead-details",
        "Content-Type": "application/json",
    }

    def __init__(self, portfolio_id: str, proxy: Optional[str] = None):
        self.portfolio_id = portfolio_id
        proxy = proxy.strip() if isinstance(proxy, str) else proxy
        proxy = proxy or None
        client_kwargs = dict(
            timeout=20.0,
            headers=self.HEADERS,
            follow_redirects=True,
            trust_env=False,
        )
        if proxy:
            client_kwargs["proxy"] = proxy
        self.client = httpx.AsyncClient(**client_kwargs)
        if proxy:
            logger.info(f"Binance 客户端已启用代理: {proxy}")

        self._positions_cache: List[Dict[str, Any]] = []
        self._positions_cache_at: float = 0.0
        self._positions_rate_limited_until: float = 0.0

    async def fetch_positions(self, force: bool = False) -> List[Dict[str, Any]]:
        """拉取当前公开持仓（带缓存，避免 REST 限流）"""
        now = time.time()
        if not force:
            if now < self._positions_rate_limited_until:
                logger.debug("持仓接口冷却中，返回缓存")
                return list(self._positions_cache)
            if now - self._positions_cache_at < self.POSITIONS_CACHE_TTL:
                return list(self._positions_cache)

        url = f"{self.BASE_URL}/friendly/future/copy-trade/lead-data/positions"
        params = {"portfolioId": self.portfolio_id}

        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            code = str(data.get("code") or "")

            if code == "90801003":
                # 请求过多，进入冷却，绝不重试加码
                self._positions_rate_limited_until = now + self.POSITIONS_RATE_LIMIT_COOLDOWN
                logger.warning(
                    f"持仓接口限流(90801003)，{self.POSITIONS_RATE_LIMIT_COOLDOWN}s 内改用缓存；"
                    f"主监控仍走订单接口"
                )
                return list(self._positions_cache)

            if code != "000000":
                logger.warning(f"持仓接口返回非成功: {data}")
                return list(self._positions_cache)

            raw_positions = data.get("data") or []
            if not raw_positions:
                logger.info("接口返回空持仓（可能已隐藏或当前无仓）")
                self._positions_cache = []
                self._positions_cache_at = now
                return []

            if raw_positions and not hasattr(self, "_logged_keys"):
                logger.debug(f"原始字段: {list(raw_positions[0].keys())}")
                self._logged_keys = True

            positions = []
            for p in raw_positions:
                try:
                    amount = float(p.get("positionAmount") or 0)
                    if abs(amount) < 1e-8:
                        continue

                    entry = float(p.get("entryPrice") or 0)
                    mark = float(p.get("markPrice") or 0)
                    upnl = float(p.get("unrealizedProfit") or 0)

                    roe = p.get("unrealizedProfitRatio")
                    if roe is None and entry > 0 and amount != 0:
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

            self._positions_cache = positions
            self._positions_cache_at = now
            logger.info(f"拉取到 {len(positions)} 个有效仓位")
            return list(positions)
        except Exception as e:
            logger.error(f"拉取持仓失败: {e}")
            if self._positions_cache:
                logger.warning("持仓请求异常，返回缓存")
                return list(self._positions_cache)
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_orders(self, page_size: int = 10) -> List[Dict[str, Any]]:
        """
        拉取最新订单记录（与 App「最新交易记录」一致：开多/平多/开空/平空）。
        """
        url = f"{self.BASE_URL}/friendly/future/copy-trade/lead-portfolio/order-history"
        try:
            resp = await self.client.post(
                url,
                json={
                    "portfolioId": self.portfolio_id,
                    "pageNumber": 1,
                    "pageSize": page_size,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "000000":
                raise RuntimeError(f"订单接口非成功: {data.get('code')} {data.get('message')}")

            raw_list = (data.get("data") or {}).get("list") or []
            orders = []
            for o in raw_list:
                try:
                    qty = float(o.get("executedQty") or 0)
                    if abs(qty) < 1e-12:
                        continue
                    price = float(o.get("avgPrice") or 0)
                    position_side = (o.get("positionSide") or "").upper()
                    side = (o.get("side") or "").upper()
                    # 与 App 文案一致
                    if position_side == "LONG":
                        action = "开多" if side == "BUY" else "平多"
                        event_type = "open" if side == "BUY" else "close"
                    elif position_side == "SHORT":
                        action = "开空" if side == "SELL" else "平空"
                        event_type = "open" if side == "SELL" else "close"
                    else:
                        action = side
                        event_type = "open" if side == "BUY" else "close"

                    orders.append({
                        "order_time": int(o.get("orderTime") or 0),
                        "update_time": int(o.get("orderUpdateTime") or o.get("orderTime") or 0),
                        "symbol": o.get("symbol"),
                        "side": side,
                        "position_side": position_side,
                        "order_type": o.get("type") or "",
                        "action": action,
                        "event_type": event_type,
                        "price": price,
                        "qty": qty,
                        "notional": abs(qty * price),
                        "realized_pnl": float(o.get("totalPnl") or 0),
                        # 兼容事件落库字段
                        "entry_price": price,
                        "position_amount": qty,
                        "unrealized_pnl": float(o.get("totalPnl") or 0),
                        "leverage": 1,
                    })
                except (ValueError, TypeError) as e:
                    logger.debug(f"解析订单失败: {o} | {e}")
                    continue

            logger.info(f"拉取到 {len(orders)} 条订单")
            return orders
        except Exception as e:
            logger.error(f"拉取订单失败: {e}")
            raise

    async def close(self):
        await self.client.aclose()
