"""ASGI 中间件：request_id 生成与透传、上下文隔离、指标记录。"""
from __future__ import annotations

import re
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.context import caller_id_var, request_id_var
from app.observability.metrics import registry

REQUEST_ID_HEADER = "x-request-id"
CALLER_ID_HEADER = "x-caller-id"
UNKNOWN_CALLER = "unknown"

# 合法标识：1-64 位的字母数字、横线、下划线、点
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def is_valid_request_id(value: str | None) -> bool:
    """判断调用方提供的标识是否合法可沿用。"""
    return bool(value) and bool(_VALID_ID.match(value))


class ObservabilityMiddleware:
    """纯 ASGI 中间件：借助 contextvars 保证并发请求间上下文不串扰。

    - 调用方提供合法 X-Request-ID 则沿用，缺失或非法时生成新的 UUID；
    - X-Caller-ID 缺失或非法时归一为 "unknown"，行为确定；
    - 响应头回写 X-Request-ID，错误响应与日志可用同一标识关联；
    - 按 caller 维度记录请求计数与耗时，开销仅为一次字典更新。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}

        request_id = headers.get(REQUEST_ID_HEADER)
        if not is_valid_request_id(request_id):
            request_id = uuid4().hex
        caller_id = headers.get(CALLER_ID_HEADER)
        if not is_valid_request_id(caller_id):
            caller_id = UNKNOWN_CALLER

        rid_token = request_id_var.set(request_id)
        cid_token = caller_id_var.set(caller_id)
        start = time.perf_counter()
        status_code = 500  # 异常穿透时按 500 计入

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                extra = list(message.get("headers", []))
                extra.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message = {**message, "headers": extra}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            latency_ms = (time.perf_counter() - start) * 1000.0
            registry.record(caller_id, latency_ms, is_error=status_code >= 400)
            request_id_var.reset(rid_token)
            caller_id_var.reset(cid_token)
