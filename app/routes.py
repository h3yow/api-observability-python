"""演示与验证用路由。

- ``/``：根路径；
- ``/demo/ok``：正常请求，body 中的敏感字段仅用于演示日志脱敏；
- ``/demo/validation`` | ``/demo/dependency`` | ``/demo/unexpected``：分别触发三类错误；
- ``/health/live`` | ``/health/ready``：存活与就绪探针；
- ``/metrics``：按调用方维度隔离的指标快照。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Response, status

from app.errors import DependencyUnavailableError, ValidationFailedError
from app.health import health_state
from app.logging_config import get_logger
from app.metrics import registry
from app.request_id import get_request_id

router = APIRouter()
logger = get_logger()


@router.get("/")
def read_root() -> dict[str, str]:
    return {"Hello": "World"}


@router.post("/demo/ok")
def demo_ok(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    # 业务字段经脱敏后进入结构化日志。
    logger.info("demo ok", extra={"payload": payload})
    return {"received_keys": sorted(payload.keys())}


@router.get("/demo/context")
def demo_context() -> dict[str, str | None]:
    # 直接回读 ContextVar，用于验证并发下标识不串扰。
    return {"context_request_id": get_request_id()}


@router.get("/demo/validation")
def demo_validation() -> None:
    raise ValidationFailedError(detail="field 'amount' must be positive, got -3")


@router.get("/demo/dependency")
def demo_dependency() -> None:
    try:
        raise ConnectionError("postgres: connection refused at 10.0.0.8:5432")
    except ConnectionError as exc:
        raise DependencyUnavailableError(
            detail="user repository unreachable", cause=exc
        )


@router.get("/demo/unexpected")
def demo_unexpected() -> None:
    raise RuntimeError("unexpected null pointer in pricing engine v3")


@router.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "alive" if health_state.is_alive() else "dead"}


@router.get("/health/ready")
def health_ready(response: Response) -> dict[str, Any]:
    ready, checks = health_state.readiness()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}


@router.get("/metrics")
def metrics_snapshot() -> dict[str, dict]:
    return registry.snapshot()
