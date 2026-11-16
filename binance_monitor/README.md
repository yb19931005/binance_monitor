# 熬鹰资本 币安仓位监控 + Telegram 实时推送

监控币安公开带单项目（熬鹰资本）的开仓、平仓、加仓/减仓，并通过 Telegram 推送。

## 特点

- 使用币安网页端非官方 bapi 拉取公开持仓
- Redis 存上一轮状态，做高效 diff
- PostgreSQL 持久化所有开平仓事件
- Telegram 推送格式化消息
- 可配置最小名义价值阈值，过滤小仓位噪音

## 快速开始

### 1. 安装依赖

```bash
cd binance_monitor
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入：

- `TELEGRAM_BOT_TOKEN`：BotFather 创建的 Bot Token
- `TELEGRAM_CHAT_ID`：目标群/频道/私聊 ID（可先让 Bot 发消息后通过 getUpdates 获取）
- `DATABASE_URL`：你的 PostgreSQL 连接串
- `REDIS_URL`：Redis 连接串（默认本地即可）

### 3. 创建数据库

```sql
CREATE DATABASE binance_monitor;
```

程序启动时会自动建表。

### 4. 运行

```bash
python main.py
```

## Bot 命令

启动后可在 Telegram 中使用以下命令：

| 命令 | 说明 |
|------|------|
| `/start` `/help` | 帮助信息 |
| `/positions` | 查询当前持仓（含合计名义与浮盈） |
| `/history [数量]` | 最近开平仓记录，默认20条，最多50条 |
| `/stats` | 监控事件统计 |

系统仍会自动推送开仓、平仓、加仓/减仓事件到配置的 `TELEGRAM_CHAT_ID`。

### 命令限流

默认每用户每命令 **60 秒内最多 5 次**。超出后会提示稍后再试。  
可在 `TelegramBot` 初始化时调整 `rate_limit_calls` 和 `rate_limit_window`。

## Web Dashboard

启动后访问：

```
http://localhost:8080
```

功能：
- 当前持仓（名义价值、浮盈、杠杆）
- 最近事件列表
- 交互式图表：持仓名义分布（环形图）、浮盈对比（柱状图）
- K 线蜡烛图（支持切换币种与周期：15m / 1h / 4h / 1d）
- WebSocket 实时标记价格（持仓列表显示现价）
- 历史事件导出 CSV（Dashboard 右上角「导出 CSV」）
- 自动每 15 秒刷新

Docker 部署时端口已映射到 `8080`。

## Docker 一键部署

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID
# Docker 场景建议：
# REDIS_URL=redis://redis:6379/0
# DATABASE_URL=postgresql://monitor:monitor_pass@postgres:5432/binance_monitor

# 2. 启动
docker compose up -d --build

# 3. 查看日志
docker compose logs -f monitor
```

停止：`docker compose down`

## 代理支持

在 `.env` 中设置（留空则直连）：

```
PROXY=http://127.0.0.1:7890
# 或 socks5://user:pass@host:port
```

## 配置说明

| 变量 | 说明 | 默认 |
|------|------|------|
| `PORTFOLIO_ID` | 带单项目 ID | 熬鹰当前公开项目 |
| `POLL_INTERVAL` | 轮询间隔（秒） | 45 |
| `MIN_NOTIONAL_USDT` | 只推送超过此名义价值的变化 | 50000 |
| `TELEGRAM_CHAT_ID` | 推送目标 | 必填 |
| `PROXY` | 可选代理 | 空 |
| `HEALTH_FAIL_THRESHOLD` | 连续失败多少次后推送告警 | 3 |

## 注意事项

- 接口为币安网页非官方 bapi，可能随时变更或限流
- 建议轮询间隔 ≥ 30 秒
- 数据有一定延迟（通常几十秒到几分钟）
- 仅做信号监控，不构成投资建议
- 请自行承担使用非官方接口的风险

## 项目结构

```
binance_monitor/
├── main.py
├── config.py
├── Dockerfile
├── docker-compose.yml
├── core/
│   ├── binance_client.py
│   ├── diff.py
│   └── telegram_bot.py
├── db/
│   ├── redis_store.py
│   └── postgres_store.py
├── requirements.txt
└── .env.example
```

## 后续可扩展

- 支持监控多个带单员
- 增加 Web Dashboard
- 接入代理池
- 推送历史查询命令（Bot 命令）
- 更精细的加仓/减仓百分比判断
