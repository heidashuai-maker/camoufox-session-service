"""HTTP 请求、响应、Cookie、代理与浏览器选项模型。"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote, unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    """拒绝未声明字段，并允许按 API 别名填充字段的基础模型。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProxyConfig(StrictModel):
    """接收结构化代理信息，并生成 Playwright 使用的代理地址。"""

    host: str
    port: int = Field(ge=1, le=65535)
    protocol: Literal["http", "https", "socks4", "socks5"] = "http"
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_url(cls, value: str) -> ProxyConfig:
        """解析代理 URL，同时校验协议、主机和端口。"""

        parsed = urlparse(value)
        protocol = "socks5" if parsed.scheme.lower() == "socks5h" else parsed.scheme.lower()
        if protocol not in {"http", "https", "socks4", "socks5"}:
            raise ValueError(f"unsupported proxy protocol: {parsed.scheme or '<missing>'}")
        if not parsed.hostname or not parsed.port:
            raise ValueError("proxy must look like protocol://host:port")
        return cls(
            host=parsed.hostname,
            port=parsed.port,
            protocol=protocol,
            username=unquote(parsed.username) if parsed.username else None,
            password=unquote(parsed.password) if parsed.password else None,
        )

    def server(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"

    def requests_url(self) -> str:
        """生成 requests 使用的代理 URL；SOCKS5 默认由代理端解析域名。"""

        protocol = "socks5h" if self.protocol == "socks5" else self.protocol
        auth = ""
        if self.username is not None:
            auth = quote(self.username, safe="")
            if self.password is not None:
                auth += f":{quote(self.password, safe='')}"
            auth += "@"
        return f"{protocol}://{auth}{self.host}:{self.port}"


class BrowserOptions(StrictModel):
    """所有浏览器任务共享的 User-Agent、代理、区域和超时选项。"""

    proxy: ProxyConfig | None = None
    userAgent: str | None = None
    locale: str = "en-US"
    timezone: str | None = None
    timeoutMs: int = Field(default=120_000, ge=1_000, le=600_000)

    @field_validator("proxy", mode="before")
    @classmethod
    def normalize_proxy(cls, value: Any) -> Any:
        if value is None or isinstance(value, (ProxyConfig, dict)):
            return value
        if isinstance(value, str):
            return ProxyConfig.from_url(value)
        raise ValueError("proxy must be a URL string or proxy object")


class RecaptchaV2Request(BrowserOptions):
    url: HttpUrl
    siteKey: str = Field(min_length=1)
    sessionUrl: HttpUrl | None = None
    maxAudioAttempts: int = Field(default=3, ge=1, le=10)


class TurnstileRequest(BrowserOptions):
    url: HttpUrl
    siteKey: str | None = None
    strategy: Literal["minimal", "page"] = "minimal"
    action: str | None = Field(default=None, max_length=32)
    cData: str | None = Field(default=None, max_length=255)
    appearance: Literal["always", "execute", "interaction-only"] = "always"
    execution: Literal["render", "execute"] = "render"
    language: str = Field(default="auto", max_length=16)

    @model_validator(mode="after")
    def require_site_key_for_minimal(self) -> TurnstileRequest:
        if self.strategy == "minimal" and not self.siteKey:
            raise ValueError("siteKey is required for minimal strategy")
        return self


class ChallengeRequest(BrowserOptions):
    url: HttpUrl
    returnHtml: bool = False
    retainSession: bool = False
    ttlSeconds: int | None = Field(default=None, ge=30, le=86_400)


class SessionCreateRequest(BrowserOptions):
    ttlSeconds: int | None = Field(default=None, ge=30, le=86_400)
    cookies: list[Cookie] = Field(default_factory=list, max_length=100)

    @field_validator("cookies")
    @classmethod
    def require_cookie_domains(cls, cookies: list[Cookie]) -> list[Cookie]:
        if any(not cookie.domain for cookie in cookies):
            raise ValueError("session cookies require a domain")
        return cookies


class SessionRequest(StrictModel):
    method: Literal["GET", "POST"] = "GET"
    url: HttpUrl
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    returnHtml: bool = True
    timeoutMs: int = Field(default=120_000, ge=1_000, le=600_000)


class Cookie(StrictModel):
    name: str
    value: str
    domain: str = ""
    path: str = "/"
    expires: float = -1
    httpOnly: bool = False
    secure: bool = False
    sameSite: str | None = None


class ErrorInfo(StrictModel):
    type: str
    message: str
    retryable: bool = False
    stage: str | None = None


Outcome = Literal[
    "solved",
    "no_challenge",
    "blocked",
    "cloudflare_error",
    "timeout",
    "browser_crashed",
    "failed",
]


class TaskResult(StrictModel):
    """统一返回任务状态、令牌、Cookie、页面信息和错误详情。"""

    status: Outcome
    token: str | None = None
    sessionId: str | None = None
    finalUrl: str | None = None
    httpStatus: int | None = None
    cookies: list[Cookie] = Field(default_factory=list)
    userAgent: str | None = None
    html: str | None = None
    elapsedMs: int = Field(ge=0)
    error: ErrorInfo | None = None
