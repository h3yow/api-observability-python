"""应用装配：可观测性中间件、异常处理、路由与健康检查。

中间件职责（按请求）：
1. 解析/生成 request id，写入 ContextVar 与 ``request.state``，并回写响应头；
2. 解析调用方标识（缺失/非法时确定性地归为 ``anonymous``）；
3. 记录结构化访问日志与按调用方隔离的指标；
4. 复位 ContextVar，杜绝并发/连接复用下的标识串扰。
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import AppError, ErrorCategory
from app.health import health_state
from app.logging_config import configure_logging, get_logger
from app.metrics import registry
from app.request_id import (
    REQUEST_ID_HEADER,
    is_valid_request_id,
    reset_request_id,
    resolve_request_id,
    set_request_id,
)
from app.routes import router

CALLER_ID_HEADER = "X-Caller-ID"
ANONYMOUS_CALLER = "anonymous"

logger = get_logger()


def resolve_caller_id(provided: str | None) -> str:
    """调用方标识缺失或非法时，确定性地归为 anonymous。"""
    if provided is not None and is_valid_request_id(provided):
        return provided
    return ANONYMOUS_CALLER


def _error_payload(category: ErrorCategory, message: str, request_id: str | None) -> dict:
    # 对外响应只含分类、安全信息与 request id，不泄露内部细节。
    return {
        "error": {
            "category": category.value,
            "message": message,
            "request_id": request_id,
        }
    }


def _error_response(
    category: ErrorCategory, message: str, request_id: str | None, status_code: int
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content=_error_payload(category, message, request_id),
    )
    if request_id:
        # 未预期异常由最外层 ServerErrorMiddleware 生成响应，不经过业务中间件，
        # 因此必须在处理器内回写响应头，保证错误响应也能用标识关联。
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="api-observability-python")

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        caller = resolve_caller_id(request.headers.get(CALLER_ID_HEADER))
        token = set_request_id(request_id)
        request.state.request_id = request_id
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            latency_ms = (time.perf_counter() - start) * 1000
            registry.record(caller, status_code, latency_ms)
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "caller": caller,
                    "status": status_code,
                    "latency_ms": round(latency_ms, 3),
                },
            )
            reset_request_id(token)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        level = logging.WARNING if exc.category is ErrorCategory.VALIDATION else logging.ERROR
        logger.log(
            level,
            "request failed",
            extra={
                "request_id": request_id,
                "category": exc.category.value,
                "detail": exc.detail,
            },
            exc_info=exc if exc.category is not ErrorCategory.VALIDATION else None,
        )
        return _error_response(exc.category, exc.public_message, request_id, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            "request validation failed",
            extra={
                "request_id": request_id,
                "category": ErrorCategory.VALIDATION.value,
                "errors": exc.errors(),
            },
        )
        return _error_response(
            ErrorCategory.VALIDATION, "request validation failed", request_id, 422
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.error(
            "unexpected error",
            extra={
                "request_id": request_id,
                "category": ErrorCategory.UNEXPECTED.value,
            },
            exc_info=exc,
        )
        return _error_response(ErrorCategory.UNEXPECTED, "internal error", request_id, 500)

    app.include_router(router)

    # 示例就绪检查：进程内状态可用即就绪；真实部署中替换为 DB/缓存探活。
    health_state.register_readiness("self", lambda: True)

    return app


app = create_app()
