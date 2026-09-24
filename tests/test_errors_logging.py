"""错误分类、异常原因链、敏感字段脱敏边界、对外不泄露内部细节。"""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.observability.context import request_id_var
from app.observability.errors import (
    DependencyUnavailableError,
    ErrorCategory,
    ValidationFailedError,
    classify,
)
from app.observability.logging import (
    MASK,
    StructuredFormatter,
    format_exception_chain,
    mask_sensitive,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------- 错误分类 ----------

def test_classify_known_and_unknown() -> None:
    cases = [
        (ValidationFailedError(), ErrorCategory.VALIDATION),
        (DependencyUnavailableError(), ErrorCategory.DEPENDENCY),
        (RuntimeError("bug"), ErrorCategory.UNEXPECTED),
        (ValueError("bad"), ErrorCategory.UNEXPECTED),
    ]
    for exc, expected in cases:
        actual = classify(exc)
        print(f"输入: {type(exc).__name__} -> 期望={expected}, 实际={actual}")
        assert actual is expected


@pytest.mark.parametrize(
    "kind, status, category",
    [("validation", 422, "validation"), ("dependency", 503, "dependency"), ("unexpected", 500, "unexpected")],
)
def test_http_error_classification(client: TestClient, kind: str, status: int, category: str) -> None:
    print(f"输入: GET /boom/{kind}")
    r = client.get(f"/boom/{kind}")
    body = r.json()
    print(f"判定依据: 状态码={status} 且 error.category={category}, 实际={r.status_code} {body}")
    assert r.status_code == status
    assert body["error"]["category"] == category


def test_response_does_not_leak_internals(client: TestClient) -> None:
    print("输入: 依赖错误(detail含内网地址) 与 未预期异常(含内部类型名)")
    for kind, leaked in [("dependency", "10.0.0.8"), ("unexpected", "NoneType")]:
        r = client.get(f"/boom/{kind}")
        raw = r.text
        print(f"判定依据: /boom/{kind} 响应不得包含 {leaked!r}, 实际响应={raw}")
        assert leaked not in raw


# ---------- 异常原因链 ----------

def test_exception_chain_preserved() -> None:
    try:
        try:
            raise ConnectionError("db connect timeout")
        except ConnectionError as cause:
            raise DependencyUnavailableError(detail="postgres unreachable") from cause
    except DependencyUnavailableError as exc:
        chain = format_exception_chain(exc)
    print(f"输入: DependencyUnavailableError from ConnectionError, 链={chain}")
    print("判定依据: 链首为包装异常，链尾为根因 ConnectionError")
    assert [e["type"] for e in chain] == ["DependencyUnavailableError", "ConnectionError"]
    assert chain[-1]["message"] == "db connect timeout"


# ---------- 脱敏边界 ----------

def test_mask_nested_and_case_insensitive() -> None:
    data = {
        "Password": "p1",
        "profile": {"api_key": "k2", "name": "alice"},
        "tokens": [{"token": "t3"}, "plain"],
    }
    masked = mask_sensitive(data)
    print(f"输入: {data}")
    print(f"判定依据: 大小写不敏感、嵌套 dict/list 内的敏感键都置为 {MASK}, 实际={masked}")
    assert masked == {
        "Password": MASK,
        "profile": {"api_key": MASK, "name": "alice"},
        "tokens": [{"token": MASK}, "plain"],
    }


def test_mask_non_dict_passthrough() -> None:
    for value in ["text", 42, None, ["a", 1]]:
        actual = mask_sensitive(value)
        print(f"输入: {value!r} -> 非敏感结构应原样返回, 实际={actual!r}")
        assert actual == value


def test_mask_failure_returns_placeholder_not_exception() -> None:
    class BrokenDict(dict):
        def items(self):  # 模拟脱敏过程中抛错
            raise RuntimeError("boom")

    result = mask_sensitive(BrokenDict(password="x"))
    print(f"输入: items() 会抛异常的 dict 子类, 实际={result!r}")
    print(f"判定依据: 脱敏失败不得抛出、不得丢日志，返回整体掩码 {MASK}")
    assert result == MASK


# ---------- 结构化日志 ----------

def _make_record(fields: dict, exc: BaseException | None = None) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.WARNING, __file__, 1, "something failed", (), None)
    record.fields = fields
    if exc is not None:
        try:
            raise exc
        except BaseException:
            import sys

            record.exc_info = sys.exc_info()
    return record


def test_formatter_masks_fields_and_includes_request_id() -> None:
    token = request_id_var.set("req-log-1")
    try:
        record = _make_record({"username": "bob", "token": "secret-t"})
        out = StructuredFormatter().format(record)
    finally:
        request_id_var.reset(token)
    payload = json.loads(out)
    print(f"输入: fields={{'username': 'bob', 'token': 'secret-t'}}, request_id=req-log-1")
    print(f"判定依据: token 脱敏、username 保留、request_id 注入, 实际={payload}")
    assert payload["fields"] == {"username": "bob", "token": MASK}
    assert payload["request_id"] == "req-log-1"


def test_formatter_includes_exception_chain() -> None:
    exc = DependencyUnavailableError(detail="pg down")
    exc.__cause__ = ConnectionError("socket closed")
    record = _make_record({"category": "dependency"}, exc=exc)
    payload = json.loads(StructuredFormatter().format(record))
    print(f"输入: 带原因链的依赖异常, 实际 exception={payload['exception']}")
    print("判定依据: 日志保留完整原因链 [DependencyUnavailableError, ConnectionError]")
    assert [e["type"] for e in payload["exception"]] == [
        "DependencyUnavailableError",
        "ConnectionError",
    ]
