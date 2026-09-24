"""敏感字段脱敏。

规则：
- 命中敏感键名（大小写不敏感、支持子串匹配如 ``x-api-key``）的值整体替换为 ``***``；
- 字符串值若内嵌 ``key=value`` 形式的敏感对，仅脱敏 value 部分；
- 递归处理嵌套 dict / list / tuple；
- 任何脱敏异常都被捕获并降级为整体 ``***``，绝不因脱敏失败而丢失日志。
"""
from __future__ import annotations

import re
from typing import Any

MASK = "***"

SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "api-key",
        "authorization",
        "auth",
        "credential",
        "private_key",
        "access_key",
        "session",
        "cookie",
    }
)

# 匹配字符串内嵌的 "敏感键=值" 或 "敏感键: 值" 片段。
_KV_PATTERN = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_\-]*)\s*(?P<sep>[:=])\s*(?P<val>[^\s,&;]+)"
)


def _is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.strip().lower()
    return any(sensitive in normalized for sensitive in SENSITIVE_KEYS)


def _mask_embedded(text: str) -> str:
    """脱敏字符串中内嵌的 key=value / key: value 敏感片段。"""

    def _sub(match: re.Match[str]) -> str:
        if _is_sensitive_key(match.group("key")):
            return f"{match.group('key')}{match.group('sep')}{MASK}"
        return match.group(0)

    return _KV_PATTERN.sub(_sub, text)


def mask_value(value: Any) -> Any:
    """对单个值脱敏；失败时降级为 ``***``，绝不抛出。"""
    try:
        if isinstance(value, str):
            return _mask_embedded(value)
        if isinstance(value, dict):
            return mask_mapping(value)
        if isinstance(value, (list, tuple)):
            return type(value)(mask_value(item) for item in value)
        return value
    except Exception:
        return MASK


def mask_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """对字典中的敏感字段递归脱敏；任何异常都降级为安全结果。"""
    try:
        result: dict[str, Any] = {}
        for key, value in data.items():
            if _is_sensitive_key(key):
                result[key] = MASK
            else:
                result[key] = mask_value(value)
        return result
    except Exception:
        return {"_masking_error": MASK}
