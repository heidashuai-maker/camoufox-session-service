from __future__ import annotations

from typing import Any, Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProxyConfig(StrictModel):
    host: str
    port: int = Field(ge=1, le=65535)
    protocol: Literal["http", "https", "socks4", "socks5"] = "http"
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_url(cls, value: str) -> "ProxyConfig":
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


class BrowserOptions(StrictModel):
    proxy: ProxyConfig | None = None
    userAgent: str | None = None
    locale: str = "en-US"
    timezone: str | None = None
    headless: bool | Literal["virtual", "xvfb"] | None = None
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
    submitUrl: HttpUrl | None = None
    maxAudioAttempts: int = Field(default=3, ge=1, le=10)
    query: str = Field(default="AAA", max_length=256)


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
    def require_site_key_for_minimal(self) -> "TurnstileRequest":
        if self.strategy == "minimal" and not self.siteKey:
            raise ValueError("siteKey is required for minimal strategy")
        return self


class ChallengeRequest(BrowserOptions):
    url: HttpUrl
    waitSeconds: int = Field(default=30, ge=1, le=180)
    returnHtml: bool = True


class SessionCreateRequest(BrowserOptions):
    ttlSeconds: int | None = Field(default=None, ge=30, le=86_400)


class SessionRequest(StrictModel):
    method: Literal["GET", "POST"] = "GET"
    url: HttpUrl
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    waitUntil: Literal["commit", "domcontentloaded", "load", "networkidle"] = "domcontentloaded"
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
    "challenge_present",
    "interactive_required",
    "timeout",
    "browser_crashed",
    "failed",
]


class TaskResult(StrictModel):
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
