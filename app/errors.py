"""错误分类与异常体系。"""
from __future__ import annotations

import enum


class ErrorCategory(enum.Enum):
    """错误分类：校验失败 / 依赖不可用 / 未预期异常。"""

    VALIDATION = "validation_error"
    DEPENDENCY = "dependency_unavailable"
    UNEXPECTED = "internal_error"


class AppError(Exception):
    """应用异常基类，携带分类与对外安全信息。"""

    category: ErrorCategory = ErrorCategory.UNEXPECTED
    status_code: int = 500
    public_message: str = "internal error"

    def __init__(self, public_message: str | None = None, *, cause: BaseException | None = None):
        raise NotImplementedError


class ValidationFailedError(AppError):
    """请求校验失败。"""

    category = ErrorCategory.VALIDATION
    status_code = 422
    public_message = "request validation failed"


class DependencyUnavailableError(AppError):
    """下游依赖不可用。"""

    category = ErrorCategory.DEPENDENCY
    status_code = 503
    public_message = "dependency unavailable"
