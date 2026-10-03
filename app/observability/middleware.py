"""ASGI 中间件：request_id 生成与透传、上下文隔离、指标记录。"""
from __future__ import annotations

import json
import re
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.context import caller_id_var, request_id_var
from app.observability.logging import get_logger
from app.observability.metrics import (
    CATEGORY_DEPENDENCY,
    CATEGORY_UNEXPECTED,
    CATEGORY_VALIDATION,
    registry,
)

logger = get_logger("app.observability")

REQUEST_ID_HEADER = "x-request-id"
CALLER_ID_HEADER = "x-caller-id"
UNKNOWN_CALLER = "unknown"

# 异常处理器把已判定的错误类别写入 scope（同一 scope 引用贯穿整条 ASGI 链）
SCOPE_ERROR_CATEGORY = "obs_error_category"

# 合法标识：1-64 位的字母数字、横线、下划线、点
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def is_valid_request_id(value: str | None) -> bool:
    """判断调用方提供的标识是否合法可沿用。"""
    return bool(value) and bool(_VALID_ID.match(value))


def category_for_status(status_code: int) -> str | None:
    """无显式类别时按状态码兜底归类，保证每个错误都能落入三类之一。"""
    if status_code < 400:
        return None
    if status_code == 503:
        return CATEGORY_DEPENDENCY
    if status_code >= 500:
        return CATEGORY_UNEXPECTED
    return CATEGORY_VALIDATION  # 其余 4xx 视为客户端/校验类问题


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
        except Exception as exc:
            # 未预期异常：在上下文仍存活时记录日志，再返回不含内部细节的 500
            status_code = 500
            scope[SCOPE_ERROR_CATEGORY] = CATEGORY_UNEXPECTED
            logger.error(
                "unexpected error",
                extra={"fields": {"category": "unexpected"}},
                exc_info=exc,
            )
            body = json.dumps(
                {
                    "error": {
                        "category": "unexpected",
                        "message": "internal error",
                        "request_id": request_id,
                    }
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (REQUEST_ID_HEADER.encode(), request_id.encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
        finally:
            latency_ms = (time.perf_counter() - start) * 1000.0
            category = scope.get(SCOPE_ERROR_CATEGORY) or category_for_status(status_code)
            registry.record(
                caller_id,
                latency_ms,
                is_error=status_code >= 400,
                category=category,
            )
            request_id_var.reset(rid_token)
            caller_id_var.reset(cid_token)
