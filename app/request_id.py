"""请求关联标识（request id）的生成、校验与透传。

规则：
- 调用方通过 ``X-Request-ID`` 提供的标识，若合法则原样沿用；
- 缺失或非法（空串、超长、含空白/控制字符等）时，确定性地生成新的 UUID4 标识；
- 标识存放在 ``ContextVar`` 中，并发请求之间互不串扰。
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"

# 可打印、无空白、便于在日志系统中检索的字符集；长度 1~128。
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def is_valid_request_id(value: str) -> bool:
    """判断调用方提供的标识是否合法。"""
    if not isinstance(value, str) or not value:
        return False
    return _REQUEST_ID_PATTERN.fullmatch(value) is not None


def new_request_id() -> str:
    """生成一个新的请求标识（UUID4 十六进制）。"""
    return uuid.uuid4().hex


def resolve_request_id(provided: str | None) -> str:
    """决定本次请求使用的标识：合法则沿用，否则生成。"""
    if provided is not None and is_valid_request_id(provided):
        return provided
    return new_request_id()


def get_request_id() -> str | None:
    """读取当前请求上下文中的标识。"""
    return _request_id_ctx.get()


def set_request_id(value: str):
    """将标识写入当前请求上下文，返回可复位的 token。"""
    return _request_id_ctx.set(value)


def reset_request_id(token) -> None:
    """复位请求上下文，避免并发/连接复用场景下串扰。"""
    _request_id_ctx.reset(token)
