"""ASGI 中间件：request_id 生成与透传、上下文隔离、指标记录。"""
from __future__ import annotations

import re

from starlette.types import ASGIApp, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
CALLER_ID_HEADER = "x-caller-id"

# 合法标识：1-64 位的字母数字、横线、下划线、点
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def is_valid_request_id(value: str | None) -> bool:
    """判断调用方提供的标识是否合法可沿用。"""
    return bool(value) and bool(_VALID_ID.match(value))


class ObservabilityMiddleware:
    """纯 ASGI 中间件，保证并发请求间上下文不串扰。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise NotImplementedError
