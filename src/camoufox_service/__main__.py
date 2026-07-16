"""命令行入口：读取配置并启动 Uvicorn。"""

from __future__ import annotations

import uvicorn

from .config import Settings


def main() -> None:
    """从环境加载监听参数并启动 FastAPI 应用。"""

    settings = Settings.from_env()
    uvicorn.run(
        "camoufox_service.app:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
