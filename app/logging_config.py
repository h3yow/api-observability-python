"""结构化日志配置。

- 单行 JSON 输出，字段固定且可机器解析；
- 自动注入当前请求的 ``request_id``（来自 ContextVar，并发安全）；
- ``extra`` 中的业务字段经脱敏后并入 ``fields``；
- 异常记录输出完整原因链 ``exc_chain``，便于追溯；
- 序列化失败时降级为纯文本行，保证日志不丢失。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.errors import cause_chain
from app.masking import mask_mapping
from app.request_id import get_request_id

LOGGER_NAME = "api.observability"

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    """将日志记录渲染为单行 JSON，并注入请求标识。"""

    def format(self, record: logging.LogRecord) -> str:
        try:
            payload: dict[str, Any] = {
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "request_id": getattr(record, "request_id", None) or get_request_id(),
            }
            fields = {
                key: value
                for key, value in vars(record).items()
                if key not in _RESERVED and not key.startswith("_")
            }
            fields.pop("request_id", None)
            if fields:
                payload["fields"] = mask_mapping(fields)
            if record.exc_info and record.exc_info[1] is not None:
                payload["exc_chain"] = cause_chain(record.exc_info[1])
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            # 脱敏/序列化失败时降级，日志绝不丢失。
            return f"{record.levelname} {record.name}: {record.getMessage()} (unstructured)"


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """配置并返回应用日志器（幂等）。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
