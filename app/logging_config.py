"""结构化日志配置。"""
from __future__ import annotations

import logging

LOGGER_NAME = "api.observability"


class JsonFormatter(logging.Formatter):
    """将日志记录渲染为单行 JSON，并注入请求标识。"""

    def format(self, record: logging.LogRecord) -> str:
        raise NotImplementedError


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """配置并返回应用日志器（幂等）。"""
    raise NotImplementedError


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
