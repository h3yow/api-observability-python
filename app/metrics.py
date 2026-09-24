"""按调用方维度隔离的指标聚合。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class CallerMetrics:
    """单个调用方的聚合指标。"""

    total: int = 0
    errors: int = 0
    latency_sum_ms: float = 0.0
    latency_max_ms: float = 0.0

    def snapshot(self) -> dict:
        raise NotImplementedError


class MetricsRegistry:
    """线程安全的调用方维度指标注册表。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_caller: dict[str, CallerMetrics] = {}

    def record(self, caller: str, status_code: int, latency_ms: float) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict[str, dict]:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


registry = MetricsRegistry()
