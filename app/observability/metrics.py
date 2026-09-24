"""指标：按调用方维度隔离的请求计数与耗时聚合，线程安全。"""
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

    @property
    def latency_avg_ms(self) -> float:
        return self.latency_sum_ms / self.total if self.total else 0.0


class MetricsRegistry:
    """按 caller_id 分桶的注册表；高并发下计数不丢失、不重复。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, CallerMetrics] = {}

    def record(self, caller_id: str, latency_ms: float, *, is_error: bool) -> None:
        """记录一次请求；同一 caller 聚合，不同 caller 互不影响。"""
        raise NotImplementedError

    def snapshot(self, caller_id: str | None = None) -> dict[str, dict[str, float]]:
        """返回指标快照；指定 caller_id 时只返回该调用方。"""
        raise NotImplementedError


registry = MetricsRegistry()
