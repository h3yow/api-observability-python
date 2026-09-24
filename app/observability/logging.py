"""结构化日志：JSON 输出、敏感字段脱敏、异常原因链保留。"""
from __future__ import annotations

import logging

# 默认敏感字段名（大小写不敏感，匹配嵌套字典的键）
DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"password", "token", "authorization", "secret", "api_key", "apikey"}
)

MASK = "***"


def mask_sensitive(data: object, sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS) -> object:
    """递归脱敏；任何异常都不得抛出，失败时返回整体掩码占位。"""
    raise NotImplementedError


def format_exception_chain(exc: BaseException) -> list[dict[str, str]]:
    """把异常及其 __cause__/__context__ 链展开为可追溯的列表。"""
    raise NotImplementedError


class StructuredFormatter(logging.Formatter):
    """把 LogRecord 格式化为单行 JSON，自动附带 request_id / caller_id。"""

    def format(self, record: logging.LogRecord) -> str:
        raise NotImplementedError


def get_logger(name: str = "app") -> logging.Logger:
    """获取配置了结构化输出的 logger（幂等）。"""
    raise NotImplementedError
