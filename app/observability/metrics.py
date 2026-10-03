"""指标：按调用方维度隔离的请求计数、错误分类计数、慢请求计数与耗时聚合，线程安全。"""
from __future__ import annotations

import threading
from dataclasses import dataclass

# 错误类别（与 app.observability.errors.ErrorCategory 的值保持一致，
# 此处以字符串常量解耦，指标层不依赖错误层）
CATEGORY_VALIDATION = "validation"
CATEGORY_DEPENDENCY = "dependency"
CATEGORY_UNEXPECTED = "unexpected"
ERROR_CATEGORIES: tuple[str, ...] = (
    CATEGORY_VALIDATION,
    CATEGORY_DEPENDENCY,
    CATEGORY_UNEXPECTED,
)

# 慢请求阈值（毫秒）：不强制要求调用方提供慢标记，默认按阈值统计
DEFAULT_SLOW_THRESHOLD_MS = 500.0


@dataclass
class CallerMetrics:
    """单个调用方的聚合指标。"""

    total: int = 0
    errors: int = 0
    latency_sum_ms: float = 0.0
    latency_max_ms: float = 0.0
    # 按错误类别拆分的计数；三类之和恒等于 errors
    validation_errors: int = 0
    dependency_errors: int = 0
    unexpected_errors: int = 0
    # 慢请求单独计数（成功/失败都可能慢）
    slow: int = 0

    @property
    def latency_avg_ms(self) -> float:
        return self.latency_sum_ms / self.total if self.total else 0.0


class MetricsRegistry:
    """按 caller_id 分桶的注册表；高并发下计数不丢失、不重复。"""

    def __init__(self, slow_threshold_ms: float = DEFAULT_SLOW_THRESHOLD_MS) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, CallerMetrics] = {}
        self._slow_threshold_ms = slow_threshold_ms

    def record(
        self,
        caller_id: str,
        latency_ms: float,
        *,
        is_error: bool | None = None,
        category: str | None = None,
        slow: bool | None = None,
    ) -> None:
        # 类别优先：给了 category 即按错误记录；否则回落到旧的 is_error 语义
        if category is not None:
            is_error = True
        elif is_error is None:
            is_error = False
        if slow is None:
            slow = latency_ms > self._slow_threshold_ms

        with self._lock:
            bucket = self._buckets.setdefault(caller_id, CallerMetrics())
            bucket.total += 1
            if is_error:
                bucket.errors += 1
            if category == CATEGORY_VALIDATION:
                bucket.validation_errors += 1
            elif category == CATEGORY_DEPENDENCY:
                bucket.dependency_errors += 1
            elif category == CATEGORY_UNEXPECTED:
                bucket.unexpected_errors += 1
            if slow:
                bucket.slow += 1
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
                    "validation_errors": m.validation_errors,
                    "dependency_errors": m.dependency_errors,
                    "unexpected_errors": m.unexpected_errors,
                    "slow": m.slow,
                    "latency_avg_ms": m.latency_avg_ms,
                    "latency_max_ms": m.latency_max_ms,
                }
                for cid, m in items
            }


registry = MetricsRegistry()
