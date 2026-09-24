"""错误分类与异常体系。

三类错误：
- ``VALIDATION``：请求校验失败（客户端问题，4xx）；
- ``DEPENDENCY``：下游依赖不可用（可重试，503）；
- ``UNEXPECTED``：未预期异常（内部问题，500，对外不泄露细节）。

所有 ``AppError`` 支持 ``cause=`` 显式原因链，日志可追溯完整链路。
"""
from __future__ import annotations

import enum


class ErrorCategory(enum.Enum):
    """错误分类：校验失败 / 依赖不可用 / 未预期异常。"""

    VALIDATION = "validation_error"
    DEPENDENCY = "dependency_unavailable"
    UNEXPECTED = "internal_error"


class AppError(Exception):
    """应用异常基类，携带分类、HTTP 状态码与对外安全信息。"""

    category: ErrorCategory = ErrorCategory.UNEXPECTED
    status_code: int = 500
    public_message: str = "internal error"

    def __init__(
        self,
        public_message: str | None = None,
        *,
        detail: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.public_message = public_message or type(self).public_message
        # detail 仅供内部日志使用，绝不出现在对外响应中。
        self.detail = detail
        if cause is not None:
            super().__init__(self.public_message)
            self.__cause__ = cause
        else:
            super().__init__(self.public_message)


def cause_chain(exc: BaseException) -> list[str]:
    """展开异常原因链，返回从外到内的类型与信息列表。"""
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return chain


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
