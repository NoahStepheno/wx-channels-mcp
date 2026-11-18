"""取数层错误类型：每个都可读、可退避，不做静默降级。"""

from __future__ import annotations


class WxError(Exception):
    """取数失败的基类，`reason` 直接进状态桶。"""

    reason = "parse_failed"

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail

    @property
    def readable(self) -> str:
        return f"{self.reason}: {self}"


class LoginRequired(WxError):
    """登录态失效（401/403、被重定向到登录页、返回体是登录 HTML）。"""

    reason = "login_required"


class RateLimited(WxError):
    """被限速或风控（429 等），需要退避冷却。"""

    reason = "rate_limited"

    def __init__(self, message: str, *, cooldown_until: str | None = None, detail: str = "") -> None:
        super().__init__(message, detail=detail)
        self.cooldown_until = cooldown_until


class ParseFailed(WxError):
    """返回体不是预期 JSON（含 HTML 被当 JSON 解析的典型静默失败）。"""

    reason = "parse_failed"
