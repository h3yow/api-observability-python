"""按调用方维度隔离的指标聚合。

- 以调用方标识（``X-Caller-Id``，缺省 ``anonymous``）为 key 独立计数，绝不混算；
- 记录请求总数、错误数（status >= 500 计为服务端错误，4xx 单独计数）、耗时和与最大值；
- 单把锁保护字典与计数器，高并发下不丢失、不重复；
- 记录路径为 O(1) 纯内存操作，对正常请求的开销可忽略。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class CallerMetrics:
    """单个调用方的聚合指标。"""

    total: int = 0
    client_errors: int = 0  # 4xx
    server_errors: int = 0  # 5xx
    latency_sum_ms: float = 0.0
    latency_max_ms: float = 0.0

    def snapshot(self) -> dict:
        avg = self.latency_sum_ms / self.total if self.total else 0.0
        return {
            "total": self.total,
            "client_errors": self.client_errors,
            "server_errors": self.server_errors,
            "latency_avg_ms": round(avg, 3),
            "latency_max_ms": round(self.latency_max_ms, 3),
        }


class MetricsRegistry:
    """线程安全的调用方维度指标注册表。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_caller: dict[str, CallerMetrics] = {}

    def record(self, caller: str, status_code: int, latency_ms: float) -> None:
        with self._lock:
            metrics = self._by_caller.setdefault(caller, CallerMetrics())
            metrics.total += 1
            if 400 <= status_code < 500:
                metrics.client_errors += 1
            elif status_code >= 500:
                metrics.server_errors += 1
            metrics.latency_sum_ms += latency_ms
            if latency_ms > metrics.latency_max_ms:
                metrics.latency_max_ms = latency_ms

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {caller: m.snapshot() for caller, m in self._by_caller.items()}

    def reset(self) -> None:
        with self._lock:
            self._by_caller.clear()


registry = MetricsRegistry()
