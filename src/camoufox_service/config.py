"""从环境变量加载并校验服务配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _integer(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _headless(name: str, default: bool) -> bool | str:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"virtual", "xvfb"}:
        return "virtual"
    return _boolean(name, default)


@dataclass(frozen=True, slots=True)
class Settings:
    """保存经过类型转换和边界校验的运行配置。"""

    host: str
    port: int
    auth_token: str | None
    workers: int
    queue_size: int
    task_timeout_seconds: int
    session_ttl_seconds: int
    max_jobs_per_worker: int
    max_worker_lifetime_seconds: int
    max_worker_rss_mb: int
    headless: bool | str

    @classmethod
    def from_env(cls) -> Settings:
        """读取环境变量，并用项目默认值补齐未设置项。"""

        return cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=_integer("PORT", 3000),
            auth_token=os.getenv("AUTH_TOKEN") or None,
            workers=_integer("CAMOUFOX_WORKERS", 1),
            queue_size=_integer("CAMOUFOX_QUEUE_SIZE", 8),
            task_timeout_seconds=_integer("CAMOUFOX_TASK_TIMEOUT_SECONDS", 120),
            session_ttl_seconds=_integer("CAMOUFOX_SESSION_TTL_SECONDS", 900),
            max_jobs_per_worker=_integer("CAMOUFOX_MAX_JOBS_PER_WORKER", 50),
            max_worker_lifetime_seconds=_integer("CAMOUFOX_MAX_WORKER_LIFETIME_SECONDS", 1800),
            max_worker_rss_mb=_integer("CAMOUFOX_MAX_WORKER_RSS_MB", 1536),
            headless=_headless("CAMOUFOX_HEADLESS", True),
        )
