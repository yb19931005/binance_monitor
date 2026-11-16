from fastapi import FastAPI, Request, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from typing import List, Dict, Any, Callable, Awaitable, Set
import httpx
import asyncio
import json
import websockets
import csv
import io
from datetime import datetime

app = FastAPI(title="熬鹰资本仓位监控", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory="web/templates")

# 运行时注入的依赖
_get_positions: Callable[[], Awaitable[List[Dict]]] = None
_get_events: Callable[[int], Awaitable[List[Dict]]] = None
_get_all_events: Callable[[int], Awaitable[List[Dict]]] = None
_proxy: str = None

# 独立的 httpx client 用于公开行情
_market_client: httpx.AsyncClient = None

# WebSocket 连接管理（前端）
_ws_clients: Set[WebSocket] = set()
_binance_ws_task: asyncio.Task = None
_subscribed_symbols: Set[str] = set()


def setup(get_positions, get_events, get_all_events=None, proxy: str = None):
    global _get_positions, _get_events, _get_all_events, _market_client, _proxy
    _get_positions = get_positions
    _get_events = get_events
    _get_all_events = get_all_events
    _proxy = proxy
    kwargs = dict(timeout=15.0, trust_env=False)
    if proxy:
        kwargs["proxy"] = proxy
    _market_client = httpx.AsyncClient(**kwargs)


async def broadcast_event(payload: Dict[str, Any]):
    """向所有前端 WS 广播订单事件"""
    if not _ws_clients:
        return
    dead = set()
    for client in list(_ws_clients):
        try:
            await client.send_json(payload)
        except Exception:
            dead.add(client)
    _ws_clients.difference_update(dead)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/positions")
async def api_positions():
    if not _get_positions:
        return []
    try:
        return await _get_positions()
    except Exception as e:
        logger.error(f"获取持仓失败: {e}")
        return []


@app.get("/api/events")
async def api_events(limit: int = 30):
    if not _get_events:
        return []
    try:
        limit = min(max(limit, 1), 100)
        events = await _get_events(limit)
        # 序列化 datetime
        for e in events:
            if e.get("created_at"):
                e["created_at"] = e["created_at"].isoformat()
            for k in ("position_amount", "entry_price", "notional", "unrealized_pnl"):
                if e.get(k) is not None:
                    e[k] = float(e[k])
        return events
    except Exception as e:
        logger.error(f"获取事件失败: {e}")
        return []


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/export")
async def api_export(limit: int = Query(500, ge=1, le=2000)):
    """导出历史事件为 CSV"""
    if not _get_all_events:
        return StreamingResponse(io.StringIO("无数据"), media_type="text/csv")
    try:
        events = await _get_all_events(limit)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "时间", "类型", "币种", "方向", "杠杆",
            "持仓数量", "开仓价", "名义价值", "浮盈", "额外"
        ])
        for e in events:
            writer.writerow([
                e.get("created_at").strftime("%Y-%m-%d %H:%M:%S") if e.get("created_at") else "",
                e.get("event_type", ""),
                e.get("symbol", ""),
                e.get("side", ""),
                e.get("leverage", ""),
                float(e.get("position_amount") or 0),
                float(e.get("entry_price") or 0),
                float(e.get("notional") or 0),
                float(e.get("unrealized_pnl") or 0),
                e.get("extra") or "",
            ])
        output.seek(0)
        filename = f"aoying_events_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"导出失败: {e}")
        return StreamingResponse(io.StringIO("导出失败"), media_type="text/csv")


@app.get("/api/klines")
async def api_klines(
    symbol: str = Query(..., min_length=4),
    interval: str = Query("1h"),
    limit: int = Query(100, ge=10, le=500),
):
    """获取币安合约 K 线（公开接口）"""
    if not _market_client:
        return []
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        resp = await _market_client.get(url, params=params)
        resp.raise_for_status()
        raw = resp.json()
        result = []
        for k in raw:
            result.append({
                "time": int(k[0] / 1000),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return result
    except Exception as e:
        logger.error(f"获取 K 线失败 {symbol}: {e}")
        return []


async def _binance_ws_loop():
    """
    连接币安合约行情 WebSocket（markPrice），推送到前端。
    使用 /market 路径（2026 迁移后的正确入口），支持 HTTP 代理。
    """
    global _subscribed_symbols
    while True:
        try:
            if not _subscribed_symbols:
                await asyncio.sleep(2)
                continue
            streams = "/".join(f"{s.lower()}@markPrice@1s" for s in sorted(_subscribed_symbols))
            # 优先新路由；失败再回退旧地址
            uris = [
                f"wss://fstream.binance.com/market/stream?streams={streams}",
                f"wss://fstream.binance.com/stream?streams={streams}",
            ]
            connected = False
            for uri in uris:
                try:
                    await _consume_mark_price_ws(uri)
                    connected = True
                    break
                except Exception as e:
                    logger.warning(f"Binance WS 连接失败 {uri}: {e}")
            if not connected:
                await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"Binance WS 循环异常: {e}，3秒后重连")
            await asyncio.sleep(3)


async def _consume_mark_price_ws(uri: str):
    """单次 WS 会话；断开则抛出由上层重连"""
    connect_kwargs = dict(ping_interval=20, ping_timeout=20, close_timeout=5)
    # websockets 13 无原生 HTTP 代理参数；有代理时走 aiohttp
    if _proxy:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(uri, proxy=_proxy, heartbeat=20) as ws:
                logger.info(f"Binance 行情 WS 已连接(代理): 订阅 {_subscribed_symbols}")
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    await _handle_mark_price_message(msg.data)
        raise ConnectionError("aiohttp ws closed")
    else:
        async with websockets.connect(uri, **connect_kwargs) as ws:
            logger.info(f"Binance 行情 WS 已连接: 订阅 {_subscribed_symbols}")
            async for raw in ws:
                await _handle_mark_price_message(raw)


async def _handle_mark_price_message(raw: str):
    data = json.loads(raw)
    payload = data.get("data", data)
    if payload.get("e") != "markPriceUpdate":
        return
    update = {
        "type": "markPrice",
        "symbol": payload.get("s"),
        "price": float(payload.get("p", 0)),
        "index": float(payload.get("i", 0)),
        "time": payload.get("E"),
    }
    dead = set()
    for client in list(_ws_clients):
        try:
            await client.send_json(update)
        except Exception:
            dead.add(client)
    _ws_clients.difference_update(dead)


@app.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket):
    """前端 WebSocket：接收实时标记价格"""
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info(f"前端 WS 连接，当前 {len(_ws_clients)} 个")
    try:
        while True:
            # 接收前端消息（可用于动态订阅）
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg)
                if data.get("action") == "subscribe" and "symbols" in data:
                    global _subscribed_symbols
                    _subscribed_symbols = set(s.upper() for s in data["symbols"])
                    logger.info(f"更新订阅: {_subscribed_symbols}")
            except Exception:
                pass
    except WebSocketDisconnect:
        _ws_clients.discard(websocket)
        logger.info(f"前端 WS 断开，剩余 {len(_ws_clients)}")


@app.on_event("startup")
async def startup():
    global _binance_ws_task
    _binance_ws_task = asyncio.create_task(_binance_ws_loop())


@app.on_event("shutdown")
async def shutdown():
    if _binance_ws_task:
        _binance_ws_task.cancel()
    if _market_client:
        await _market_client.aclose()
