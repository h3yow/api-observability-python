"""存活与就绪探针。"""
from __future__ import annotations

from collections.abc import Callable

ReadinessCheck = Callable[[], bool]


class HealthState:
    """维护存活/就绪状态；存活恒定可用，就绪依赖外部检查。"""

    def __init__(self) -> None:
        self._readiness_checks: dict[str, ReadinessCheck] = {}

    def register_readiness(self, name: str, check: ReadinessCheck) -> None:
        raise NotImplementedError

    def is_alive(self) -> bool:
        raise NotImplementedError

    def readiness(self) -> tuple[bool, dict[str, bool]]:
        raise NotImplementedError


health_state = HealthState()
