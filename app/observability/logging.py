"""结构化日志：JSON 输出、敏感字段脱敏、异常原因链保留。"""
from __future__ import annotations

import json
import logging

from app.observability.context import get_caller_id, get_request_id

# 默认敏感字段名（大小写不敏感，匹配嵌套字典的键）
DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"password", "token", "authorization", "secret", "api_key", "apikey"}
)

MASK = "***"


def mask_sensitive(data: object, sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS) -> object:
    """递归脱敏；任何异常都不得抛出，失败时返回整体掩码占位。"""
    try:
        return _mask(data, sensitive_keys, depth=0)
    except Exception:
        # 脱敏失败不得丢失日志：用整体掩码占位，保证调用方仍能记录
        return MASK


def _mask(data: object, sensitive_keys: frozenset[str], depth: int) -> object:
    if depth > 32:  # 防御异常深的嵌套结构
        return MASK
    if isinstance(data, dict):
        masked: dict[object, object] = {}
        for key, value in data.items():
            if isinstance(key, str) and key.lower() in sensitive_keys:
                masked[key] = MASK
            else:
                masked[key] = _mask(value, sensitive_keys, depth + 1)
        return masked
    if isinstance(data, (list, tuple)):
        return [_mask(item, sensitive_keys, depth + 1) for item in data]
    return data


def format_exception_chain(exc: BaseException) -> list[dict[str, str]]:
    """把异常及其 __cause__/__context__ 链展开为可追溯的列表。"""
    chain: list[dict[str, str]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(
            {
                "type": type(current).__name__,
                "message": str(current),
            }
        )
        # 优先显式原因（raise ... from ...），否则回溯隐式上下文
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return chain


class StructuredFormatter(logging.Formatter):
    """把 LogRecord 格式化为单行 JSON，自动附带 request_id / caller_id。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
            "caller_id": get_caller_id(),
        }
        # extra 传入的结构化字段先脱敏再并入
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload["fields"] = mask_sensitive(extra)
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = format_exception_chain(record.exc_info[1])
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            # 序列化失败也不得丢失日志
            return json.dumps(
                {"level": record.levelname, "message": record.getMessage()},
                ensure_ascii=False,
            )


def get_logger(name: str = "app") -> logging.Logger:
    """获取配置了结构化输出的 logger（幂等）。"""
    logger = logging.getLogger(name)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
