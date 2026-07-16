"""浏览器启动参数、Cookie 导出与通用页面信息读取。"""

from __future__ import annotations

from typing import Any

from .models import BrowserOptions, Cookie

_BROWSER_CRASH_MARKERS = (
    "browser has been closed",
    "browser closed",
    "browser disconnected",
    "target page, context or browser has been closed",
    "connection closed",
    "playwright driver",
)


def is_browser_crash_error(exc: Exception) -> bool:
    """判断异常是否表示浏览器或 Playwright 驱动连接已经失效。"""

    message = str(exc).lower()
    return any(marker in message for marker in _BROWSER_CRASH_MARKERS)


def context_options(options: BrowserOptions) -> dict[str, Any]:
    """把公共 API 浏览器选项转换为 Camoufox Context 参数。"""

    result: dict[str, Any] = {"locale": options.locale}
    if options.userAgent:
        result["user_agent"] = options.userAgent
    if options.timezone:
        result["timezone_id"] = options.timezone
    if options.proxy:
        proxy = {"server": options.proxy.server()}
        if options.proxy.username:
            proxy["username"] = options.proxy.username
        if options.proxy.password:
            proxy["password"] = options.proxy.password
        result["proxy"] = proxy
    return result


def cookies_from_context(context) -> list[Cookie]:
    """将 Playwright Cookie 转换为稳定的 API Cookie 模型。"""

    cookies = []
    for raw in context.cookies():
        cookies.append(
            Cookie(
                name=str(raw.get("name") or ""),
                value=str(raw.get("value") or ""),
                domain=str(raw.get("domain") or ""),
                path=str(raw.get("path") or "/"),
                expires=float(raw.get("expires", -1)),
                httpOnly=bool(raw.get("httpOnly", False)),
                secure=bool(raw.get("secure", False)),
                sameSite=raw.get("sameSite"),
            )
        )
    return cookies


def page_user_agent(page) -> str | None:
    value = page.evaluate("() => navigator.userAgent")
    return str(value) if value else None


def response_status(response) -> int | None:
    return int(response.status) if response is not None and response.status is not None else None
