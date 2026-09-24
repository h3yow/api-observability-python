"""指标：按调用方维度隔离的请求计数与耗时聚合，线程安全。"""
from __future__ import annotations

import threading
from dataclasses import dataclass


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
        with self._lock:
            bucket = self._buckets.setdefault(caller_id, CallerMetrics())
            bucket.total += 1
            if is_error:
                bucket.errors += 1
            bucket.latency_sum_ms += latency_ms
            if latency_ms > bucket.latency_max_ms:
                bucket.latency_max_ms = latency_ms

    def snapshot(self, caller_id: str | None = None) -> dict[str, dict[str, float]]:
        """返回指标快照；指定 caller_id 时只返回该调用方。"""
        with self._lock:
            items = (
                [(caller_id, self._buckets.get(caller_id, CallerMetrics()))]
                if caller_id is not None
                else list(self._buckets.items())
            )
            return {
                cid: {
                    "total": m.total,
                    "errors": m.errors,
                    "latency_avg_ms": m.latency_avg_ms,
                    "latency_max_ms": m.latency_max_ms,
                }
                for cid, m in items
            }


registry = MetricsRegistry()
