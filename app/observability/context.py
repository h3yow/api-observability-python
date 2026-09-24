"""请求级上下文：request_id 与调用方标识的隔离存储（基于 contextvars）。"""
from __future__ import annotations

import contextvars

# 当前请求的关联标识；未设置时为 None
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
# 当前请求的调用方标识；未设置时为 None
caller_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "caller_id", default=None
)


def get_request_id() -> str | None:
    """返回当前上下文的 request_id。"""
    return request_id_var.get()


def get_caller_id() -> str | None:
    """返回当前上下文的调用方标识。"""
    return caller_id_var.get()
