"""结构化日志：字段注入、脱敏、异常原因链、降级不丢失。"""
from __future__ import annotations

import io
import json
import logging

import pytest

from app.logging_config import JsonFormatter, get_logger
from tests.conftest import test_logger


@pytest.fixture()
def log_buffer():
    """给应用日志器临时挂一个内存 handler，捕获结构化输出。"""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    logger = get_logger()
    logger.addHandler(handler)
    yield buffer
    logger.removeHandler(handler)


def _json_lines(buffer: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buffer.getvalue().strip().splitlines()]


def test_access_log_carries_request_id_and_masked_fields(client, log_buffer) -> None:
    client.post(
        "/demo/ok",
        json={"username": "alice", "password": "p@ssw0rd"},
        headers={"X-Request-ID": "rid-log-1"},
    )
    records = _json_lines(log_buffer)
    test_logger.info(
        "输入=POST /demo/ok 含敏感字段 判定依据=每条日志带 request_id 且敏感字段脱敏 -> 实际=%s",
        records,
    )
    assert records, "日志不得丢失"
    for record in records:
        assert record["request_id"] == "rid-log-1"
    business = next(r for r in records if r["msg"] == "demo ok")
    assert business["fields"]["payload"]["password"] == "***"
    assert business["fields"]["payload"]["username"] == "alice"


def test_dependency_error_log_has_cause_chain(client, log_buffer) -> None:
    client.get("/demo/dependency", headers={"X-Request-ID": "rid-chain"})
    records = _json_lines(log_buffer)
    error = next(r for r in records if r["level"] == "ERROR")
    test_logger.info(
        "输入=GET /demo/dependency 判定依据=ERROR 日志含分类与完整原因链 -> 实际=%s",
        error,
    )
    assert error["request_id"] == "rid-chain"
    assert error["fields"]["category"] == "dependency_unavailable"
    chain = error["exc_chain"]
    assert chain[0].startswith("DependencyUnavailableError")
    assert any("ConnectionError" in link for link in chain)


def test_log_degrades_but_never_lost() -> None:
    """字段对象在序列化/脱敏时爆炸，日志仍降级输出一行。"""

    class Exploding:
        def __repr__(self) -> str:
            raise RuntimeError("repr boom")

        def __str__(self) -> str:
            raise RuntimeError("str boom")

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    record = logging.LogRecord(
        "api.observability", logging.INFO, __file__, 1, "fallback-test", (), None
    )
    record.broken = Exploding()
    line = handler.format(record)
    test_logger.info(
        "输入=序列化即抛异常的字段 判定依据=降级为纯文本行且不抛出 -> 实际=%r", line
    )
    assert "fallback-test" in line  # 日志没有丢失
