"""结构化日志：JSON 输出、敏感字段脱敏、异常原因链保留。"""
from __future__ import annotations

import json
import logging
import re

from app.observability.context import get_caller_id, get_request_id

# 默认敏感字段名（大小写不敏感，精确匹配，行为与早期版本保持一致）
DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"password", "token", "authorization", "secret", "api_key", "apikey"}
)

# 语义敏感词：归一化后的字段名只要拆出这些词中的任意一个即视为敏感
# （password/passwd/pwd=密码，secret=密钥，token=令牌，auth*=鉴权，credential=凭证）
SENSITIVE_TOKENS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "passcode",
        "secret",
        "token",
        "auth",
        "authentication",
        "authorization",
        "credential",
        "credentials",
    }
)

# 与 key 组合后表示密钥的限定词：access_key / api-key / user.secretKey 等
KEY_QUALIFIERS: frozenset[str] = frozenset(
    {"api", "access", "secret", "private", "encryption", "signing", "security"}
)

# 连写形式（无分隔符且全小写，驼峰拆分切不开），子串命中即敏感
_GLUED_SENSITIVE_WORDS: tuple[str, ...] = (
    "apikey",
    "accesskey",
    "secretkey",
    "privatekey",
    "authtoken",
    "accesstoken",
    "refreshtoken",
)

# 中文语义关键字（子串命中即敏感）：密码/口令、密钥/私钥、令牌、鉴权、凭证
_SENSITIVE_ZH: tuple[str, ...] = (
    "密码", "口令", "密钥", "私钥", "令牌", "鉴权", "凭证",
)

MASK = "***"

# 驼峰边界：userPassword -> user Password；APIKey -> API Key
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# 非字母数字统一视为分隔符：连字符、下划线、点、空白等
_NON_TOKEN = re.compile(r"[^a-z0-9]+")


def is_sensitive_name(name: str, sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS) -> bool:
    """判断字段名/请求头名是否表达敏感含义。

    兼容连字符、下划线、驼峰及任意前后缀（如 ``userPassword``、``X-Auth-Token``、
    ``db.private_key``），但只按整词匹配，``keyboard``/``author``/``primary_key``
    等普通业务词不会被误伤。
    """
    if not isinstance(name, str):
        return False
    lowered = name.lower()
    # 1) 标准命名精确匹配，行为与历史版本完全一致
    if lowered in sensitive_keys:
        return True
    # 2) 中文语义子串
    if any(zh in name for zh in _SENSITIVE_ZH):
        return True
    # 3) 连写形式（apikey / access_key 去掉分隔符后也由此覆盖）
    compact = _NON_TOKEN.sub("", lowered)
    if any(glued in compact for glued in _GLUED_SENSITIVE_WORDS):
        return True
    # 4) 驼峰拆分 + 分隔符归一化后的整词匹配
    spaced = _CAMEL_BOUNDARY.sub(" ", name).lower()
    tokens = set(_NON_TOKEN.sub(" ", spaced).split())
    if tokens & SENSITIVE_TOKENS:
        return True
    # 5) key 单独出现不敏感（如 primary_key），必须带密钥类限定词
    if "key" in tokens and (tokens & KEY_QUALIFIERS):
        return True
    return False


def mask_sensitive(data: object, sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS) -> object:
    """递归脱敏；任何异常都不得抛出，失败时返回整体掩码占位。"""
    try:
        return _mask(data, sensitive_keys, depth=0)
    except Exception:
        # 脱敏失败不得丢失日志：用整体掩码占位，保证调用方仍能记录
        return MASK


def _mask(data: object, sensitive_keys: frozenset[str], depth: int) -> object:
    if depth > 32:  # 防御异常深的嵌套结构（含循环引用）
        return MASK
    try:
        if isinstance(data, dict):
            masked: dict[object, object] = {}
            for key, value in data.items():
                if isinstance(key, str) and is_sensitive_name(key, sensitive_keys):
                    masked[key] = MASK
                else:
                    masked[key] = _mask(value, sensitive_keys, depth + 1)
            return masked
        if isinstance(data, (list, tuple)):
            # 请求头常见的 (name, value) 二元数组：首元素为敏感名时掩码其值
            if (
                len(data) == 2
                and isinstance(data[0], str)
                and is_sensitive_name(data[0], sensitive_keys)
            ):
                return [data[0], MASK]
            return [_mask(item, sensitive_keys, depth + 1) for item in data]
        return data
    except Exception:
        # 单个子树脱敏失败只掩码该子树，尽量保留同条日志的其他字段
        return MASK


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
