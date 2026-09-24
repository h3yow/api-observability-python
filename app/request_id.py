"""请求关联标识（request id）的生成、校验与透传。"""
from __future__ import annotations

import re
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def is_valid_request_id(value: str) -> bool:
    """判断调用方提供的标识是否合法。"""
    raise NotImplementedError


def new_request_id() -> str:
    """生成一个新的请求标识。"""
    raise NotImplementedError


def resolve_request_id(provided: str | None) -> str:
    """决定本次请求使用的标识：合法则沿用，否则生成。"""
    raise NotImplementedError


def get_request_id() -> str | None:
    """读取当前请求上下文中的标识。"""
    return _request_id_ctx.get()


def set_request_id(value: str):
    """将标识写入当前请求上下文，返回可复位的 token。"""
    return _request_id_ctx.set(value)


def reset_request_id(token) -> None:
    """复位请求上下文，避免并发/复用场景下串扰。"""
    _request_id_ctx.reset(token)
