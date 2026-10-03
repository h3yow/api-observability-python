"""指标：按调用方维度隔离的请求计数与耗时聚合，线程安全。

错误按类别拆开统计（校验失败 / 依赖不可用 / 未预期异常），
慢请求单独计数；所有更新在同一把锁内完成，保证
``total = 成功数 + errors`` 且
``errors = validation_errors + dependency_errors + unexpected_errors``
在高并发下仍然成立。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

# 慢请求阈值：延迟达到该值即单独计入 slow_requests（成功/失败都计）
SLOW_REQUEST_THRESHOLD_MS = 500.0

# 错误类别取值与 errors.ErrorCategory.value 对齐
CATEGORY_VALIDATION = "validation"
CATEGORY_DEPENDENCY = "dependency"
CATEGORY_UNEXPECTED = "unexpected"
_ERROR_CATEGORIES = (
    CATEGORY_VALIDATION,
    CATEGORY_DEPENDENCY,
    CATEGORY_UNEXPECTED,
)


@dataclass
class CallerMetrics:
    """单个调用方的聚合指标。"""

    total: int = 0
    errors: int = 0
    validation_errors: int = 0
    dependency_errors: int = 0
    unexpected_errors: int = 0
    slow_requests: int = 0
    latency_sum_ms: float = 0.0
    latency_max_ms: float = 0.0

    @property
    def latency_avg_ms(self) -> float:
        return self.latency_sum_ms / self.total if self.total else 0.0

    @property
    def successes(self) -> int:
        return self.total - self.errors


class MetricsRegistry:
    """按 caller_id 分桶的注册表；高并发下计数不丢失、不重复。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, CallerMetrics] = {}

    def record(
        self,
        caller_id: str,
        latency_ms: float,
        *,
        is_error: bool,
        category: str | None = None,
        is_slow: bool | None = None,
    ) -> None:
        """记录一次请求；同一 caller 聚合，不同 caller 互不影响。

        - ``category``：validation/dependency/unexpected；错误但未指明类别时
          归入 unexpected，保证各类别之和与 errors 始终对得上；
        - ``is_slow``：不传时按 ``latency_ms`` 与阈值自动判定。
        """
        if is_slow is None:
            is_slow = latency_ms >= SLOW_REQUEST_THRESHOLD_MS
        with self._lock:
            bucket = self._buckets.setdefault(caller_id, CallerMetrics())
            # 一次计数的所有字段在同一临界区内更新，不丢不重、和必然对得上
            bucket.total += 1
            if is_error:
                bucket.errors += 1
                if category not in _ERROR_CATEGORIES:
                    category = CATEGORY_UNEXPECTED
                if category == CATEGORY_VALIDATION:
                    bucket.validation_errors += 1
                elif category == CATEGORY_DEPENDENCY:
                    bucket.dependency_errors += 1
                else:
                    bucket.unexpected_errors += 1
            if is_slow:
                bucket.slow_requests += 1
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
                    "successes": m.successes,
                    "errors": m.errors,
                    "validation_errors": m.validation_errors,
                    "dependency_errors": m.dependency_errors,
                    "unexpected_errors": m.unexpected_errors,
                    "slow_requests": m.slow_requests,
                    "latency_avg_ms": m.latency_avg_ms,
                    "latency_max_ms": m.latency_max_ms,
                }
                for cid, m in items
            }


registry = MetricsRegistry()
