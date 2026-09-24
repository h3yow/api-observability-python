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
    raise NotImplementedError
