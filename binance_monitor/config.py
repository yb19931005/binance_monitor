from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    portfolio_id: str = "5075281354358777856"
    poll_interval: int = 45

    telegram_bot_token: str
    telegram_chat_id: str

    redis_url: str = "redis://localhost:6379/0"
    database_url: str

    log_level: str = "INFO"
    min_notional_usdt: float = 50000.0

    # 可选代理，例如 http://127.0.0.1:7890 或 socks5://...
    proxy: Optional[str] = None

    # Web Dashboard
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    # 健康检查：连续失败多少次后推送告警
    health_fail_threshold: int = 3

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
