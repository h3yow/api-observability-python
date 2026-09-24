"""敏感字段脱敏。"""
from __future__ import annotations

from typing import Any

SENSITIVE_KEYS: frozenset[str] = frozenset()
MASK = "***"


def mask_value(value: Any) -> str:
    """对单个值做脱敏，返回脱敏后的字符串。"""
    raise NotImplementedError


def mask_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """对字典中的敏感字段递归脱敏；脱敏失败时不得丢失日志。"""
    raise NotImplementedError
