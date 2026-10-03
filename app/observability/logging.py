"""结构化日志：JSON 输出、敏感字段脱敏、异常原因链保留。"""
from __future__ import annotations

import json
import logging
import re

from app.observability.context import get_caller_id, get_request_id

# 默认敏感字段名（大小写不敏感，精确匹配嵌套字典的键；历史行为保持不变）
DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"password", "token", "authorization", "secret", "api_key", "apikey"}
)

MASK = "***"

# 语义识别用的敏感词根：键名经驼峰/连字符/下划线切分后，token 精确命中即敏感
_SENSITIVE_TOKENS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "token",
        "tokens",
        "auth",
        "authorization",
        "authorizations",
        "credential",
        "credentials",
        "passphrase",
        "passphrases",
        "apikey",
    }
)

# 全小写黏连写法（无分隔符）中的敏感复合词；要求词尾边界，避免误伤 tokenization 等普通词
_GLUED_SENSITIVE = re.compile(
    r"(?:accesstoken|refreshtoken|secretkey|accesskey|privatekey|"
    r"passphrase|password|passwd|authorization|credential|apikey|"
    r"secret|token|pwd|auth)s?(?![a-z])"
)

# 相邻 token 二元组：带修饰词的 key 才视为密钥（裸 key/primary key 不误伤）
_SENSITIVE_TOKEN_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("private", "key"),
        ("secret", "key"),
        ("access", "key"),
        ("api", "key"),
        ("signing", "key"),
    }
)

# 中文语义标记：子串命中即敏感（密码/口令/密钥/令牌/鉴权/凭证/凭据）
_SENSITIVE_CN = ("密码", "口令", "密钥", "令牌", "鉴权", "凭证", "凭据")

# 驼峰切分：aA、AABb 边界处插分隔符，如 userPassword / apiKey / HTTPPassword
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_ASCII_WORDS = re.compile(r"[a-z0-9]+")


def is_sensitive_name(
    key: str, sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS
) -> bool:
    """判断字段名是否敏感。

    兼容多团队的自由命名：精确标准名优先（历史行为），其次做语义识别——
    大小写不敏感，支持前后任意缀词、连字符/下划线/点/空格/驼峰/全小写
    黏连写法，以及中文语义词；普通业务字段不得误伤。
    """
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    # 1) 标准字段名精确匹配，行为与历史完全一致
    if lowered in sensitive_keys:
        return True
    # 2) 中文语义子串
    if any(marker in key for marker in _SENSITIVE_CN):
        return True
    # 3) 驼峰边界展开后按非字母数字切词（同时覆盖连字符/下划线/点/空格）
    spread = _CAMEL_BOUNDARY.sub(" ", key).lower()
    tokens = _ASCII_WORDS.findall(spread)
    if any(tok in _SENSITIVE_TOKENS for tok in tokens):
        return True
    if any((tokens[i], tokens[i + 1]) in _SENSITIVE_TOKEN_PAIRS for i in range(len(tokens) - 1)):
        return True
    # 4) 全小写黏连复合词（如 dbpassword、mysecretkey），要求词尾边界
    if _GLUED_SENSITIVE.search(lowered):
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
    if depth > 32:  # 防御异常深的嵌套结构
        return MASK
    if isinstance(data, dict):
        masked: dict[object, object] = {}
        for key, value in data.items():
            if isinstance(key, str) and is_sensitive_name(key, sensitive_keys):
                masked[key] = MASK
            else:
                masked[key] = _mask(value, sensitive_keys, depth + 1)
        return masked
    if isinstance(data, (list, tuple)):
        result: list[object] = []
        for item in data:
            # 请求头形态：(name, value) / [name, value] 二元组且值为字符串，
            # 名字敏感则掩码其值；要求值是 str/bytes，避免误伤普通业务二元数组
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[0], str)
                and isinstance(item[1], (str, bytes))
                and is_sensitive_name(item[0], sensitive_keys)
            ):
                result.append([item[0], MASK])
            else:
                result.append(_mask(item, sensitive_keys, depth + 1))
        return result
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
