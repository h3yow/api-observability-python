"""应用装配：中间件、异常处理、路由与健康检查。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.observability import health
from app.observability.context import get_caller_id, get_request_id
from app.observability.errors import (
    AppError,
    DependencyUnavailableError,
    ErrorCategory,
    ValidationFailedError,
    classify,
)
from app.observability.logging import get_logger
from app.observability.metrics import registry
from app.observability.middleware import SCOPE_ERROR_CATEGORY, ObservabilityMiddleware

logger = get_logger("app")


def _error_body(category: ErrorCategory, message: str) -> dict[str, object]:
    # 对外只暴露分类、安全消息与关联标识，不含内部实现细节
    return {
        "error": {
            "category": category.value,
            "message": message,
            "request_id": get_request_id(),
        }
    }


def create_app() -> FastAPI:
    app = FastAPI(title="api-observability")
    app.add_middleware(ObservabilityMiddleware)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request.scope[SCOPE_ERROR_CATEGORY] = exc.category.value
        logger.warning(
            "request failed: %s",
            exc.public_message,
            extra={"fields": {"category": exc.category.value, "detail": exc.detail}},
            exc_info=exc,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.category, exc.public_message),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request.scope[SCOPE_ERROR_CATEGORY] = ErrorCategory.VALIDATION.value
        logger.info(
            "request validation failed",
            extra={"fields": {"category": ErrorCategory.VALIDATION.value}},
        )
        return JSONResponse(
            status_code=422,
            content=_error_body(ErrorCategory.VALIDATION, "request validation failed"),
        )

    @app.get("/")
    def read_root() -> dict[str, str]:
        return {"Hello": "World"}

    @app.get("/echo")
    async def echo(delay: float = 0) -> dict[str, str | None]:
        # 用于验证标识透传与上下文隔离；delay 可制造并发交错
        if delay > 0:
            import asyncio

            await asyncio.sleep(delay)
        return {"request_id": get_request_id(), "caller_id": get_caller_id()}

    @app.get("/boom/{kind}")
    def boom(kind: str) -> None:
        # 演示三类错误：校验失败 / 依赖不可用 / 未预期异常
        if kind == "validation":
            raise ValidationFailedError(detail="field 'amount' must be positive")
        if kind == "dependency":
            try:
                raise ConnectionError("db connect timeout 10.0.0.8:5432")
            except ConnectionError as cause:
                raise DependencyUnavailableError(
                    detail="postgres unreachable"
                ) from cause
        raise RuntimeError("bug: NoneType has no attribute 'x'")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return health.liveness()

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        status, body = health.readiness()
        return JSONResponse(status_code=status, content=body)

    @app.get("/metrics")
    def metrics(caller_id: str | None = None) -> dict[str, object]:
        return {"callers": registry.snapshot(caller_id)}

    return app


app = create_app()
