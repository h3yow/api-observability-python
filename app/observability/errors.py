"""错误分类：区分请求校验失败、依赖不可用与未预期异常。"""
from __future__ import annotations

import enum


class ErrorCategory(enum.Enum):
    """错误类别，用于日志与指标的统一归类。"""

    VALIDATION = "validation"      # 请求校验失败（客户端问题）
    DEPENDENCY = "dependency"      # 依赖不可用（下游/基础设施问题）
    UNEXPECTED = "unexpected"      # 未预期异常（服务内部缺陷）


class AppError(Exception):
    """应用内可预期错误的基类，携带分类与对外安全的消息。"""

    category: ErrorCategory = ErrorCategory.UNEXPECTED
    status_code: int = 500
    public_message: str = "internal error"

    def __init__(self, public_message: str | None = None, *, detail: str | None = None):
        # public_message: 对外可见；detail: 仅写入日志，不外泄
        super().__init__(detail or public_message or self.public_message)
        if public_message is not None:
            self.public_message = public_message
        self.detail = detail


class ValidationFailedError(AppError):
    category = ErrorCategory.VALIDATION
    status_code = 422
    public_message = "request validation failed"


class DependencyUnavailableError(AppError):
    category = ErrorCategory.DEPENDENCY
    status_code = 503
    public_message = "dependency unavailable"


def classify(exc: BaseException) -> ErrorCategory:
    """将任意异常归类为 ErrorCategory。"""
    if isinstance(exc, AppError):
        return exc.category
    return ErrorCategory.UNEXPECTED
