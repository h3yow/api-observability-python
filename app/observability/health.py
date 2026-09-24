"""健康检查：存活（liveness）与就绪（readiness）语义分离。"""
from __future__ import annotations

from collections.abc import Callable

# 就绪探针：名称 -> 检查函数，返回 None 表示正常，抛异常表示未就绪
_readiness_checks: dict[str, Callable[[], None]] = {}


def register_readiness_check(name: str, check: Callable[[], None]) -> None:
    """注册一个就绪依赖检查。"""
    _readiness_checks[name] = check


def liveness() -> dict[str, str]:
    """存活：进程在运行即通过，不检查依赖。"""
    return {"status": "alive"}


def readiness() -> tuple[int, dict[str, object]]:
    """就绪：所有依赖检查通过才就绪；返回 (http_status, body)。"""
    results: dict[str, str] = {}
    ready = True
    for name, check in _readiness_checks.items():
        try:
            check()
            results[name] = "ok"
        except Exception as exc:  # 依赖异常只影响该检查项，不拖垮整个探针
            results[name] = f"unavailable: {type(exc).__name__}"
            ready = False
    status = 200 if ready else 503
    return status, {"status": "ready" if ready else "not_ready", "checks": results}
