"""存活（liveness）与就绪（readiness）探针。

- 存活：进程内事件循环可响应即为存活，不依赖外部资源；
- 就绪：聚合所有已注册的依赖检查，任一失败则整体不就绪；
- 单个检查异常视为该依赖不就绪，且不影响其他检查的结果。
"""
from __future__ import annotations

from collections.abc import Callable

ReadinessCheck = Callable[[], bool]


class HealthState:
    """维护存活/就绪状态；存活恒定可用，就绪依赖外部检查。"""

    def __init__(self) -> None:
        self._readiness_checks: dict[str, ReadinessCheck] = {}

    def register_readiness(self, name: str, check: ReadinessCheck) -> None:
        self._readiness_checks[name] = check

    def is_alive(self) -> bool:
        # 能执行到这里即代表进程可响应。
        return True

    def readiness(self) -> tuple[bool, dict[str, bool]]:
        results: dict[str, bool] = {}
        for name, check in self._readiness_checks.items():
            try:
                results[name] = bool(check())
            except Exception:
                results[name] = False
        return all(results.values()), results


health_state = HealthState()
