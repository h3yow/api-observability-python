"""请求标识：合法沿用、非法/缺失时确定行为、并发隔离、错误响应可关联。"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.observability.middleware import is_valid_request_id

UUID_HEX = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_valid_request_id_is_reused(client: TestClient) -> None:
    print("输入: X-Request-ID=order-2026.09_25（合法字符集）")
    r = client.get("/echo", headers={"X-Request-ID": "order-2026.09_25"})
    print(f"判定依据: 响应体与响应头都必须原样沿用该标识, 实际={r.json()}")
    assert r.json()["request_id"] == "order-2026.09_25"
    assert r.headers["x-request-id"] == "order-2026.09_25"


@pytest.mark.parametrize("bad", ["has space", "café-01", "x" * 65, ""])
def test_invalid_request_id_is_replaced(client: TestClient, bad: str) -> None:
    print(f"输入: X-Request-ID={bad!r}（非法：含空格/非ASCII/超长/空）")
    r = client.get("/echo", headers={"X-Request-ID": bad})
    rid = r.json()["request_id"]
    print(f"判定依据: 非法标识不得沿用，应生成 32 位十六进制 UUID, 实际={rid}")
    assert rid != bad
    assert UUID_HEX.match(rid), rid
    assert r.headers["x-request-id"] == rid


def test_missing_request_id_is_generated(client: TestClient) -> None:
    print("输入: 不提供 X-Request-ID")
    r = client.get("/echo")
    rid = r.json()["request_id"]
    print(f"判定依据: 缺失时确定性生成 UUID 并回写响应头, 实际={rid}")
    assert UUID_HEX.match(rid)
    assert r.headers["x-request-id"] == rid


def test_is_valid_request_id_boundaries() -> None:
    cases = {"a": True, "A-1._x": True, None: False, "": False, "a" * 64: True, "a" * 65: False, "a/b": False}
    for value, expected in cases.items():
        actual = is_valid_request_id(value)
        print(f"输入: {value!r} -> 期望={expected}, 实际={actual}")
        assert actual is expected


def test_concurrent_requests_no_crosstalk(client: TestClient) -> None:
    pairs = [(f"req-{i:03d}", f"caller-{i % 4}") for i in range(40)]
    print(f"输入: 并发 {len(pairs)} 个请求，各自携带唯一 request_id 与 4 种 caller_id，随机延迟制造交错")

    def call(pair: tuple[str, str]) -> tuple[str, str, dict]:
        rid, cid = pair
        r = client.get(
            "/echo",
            params={"delay": "0.01"},
            headers={"X-Request-ID": rid, "X-Caller-ID": cid},
        )
        return rid, cid, r.json()

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(call, pairs))

    for expected_rid, expected_cid, body in results:
        print(f"判定依据: 期望 rid={expected_rid} cid={expected_cid}, 实际={body}")
        assert body["request_id"] == expected_rid, "request_id 发生串扰"
        assert body["caller_id"] == expected_cid, "caller_id 发生串扰"


def test_error_response_carries_same_request_id(client: TestClient) -> None:
    print("输入: 触发未预期异常，X-Request-ID=req-err-500")
    r = client.get("/boom/unexpected", headers={"X-Request-ID": "req-err-500"})
    body = r.json()
    print(f"判定依据: 500 响应体与响应头的 request_id 必须等于 req-err-500, 实际={body}")
    assert r.status_code == 500
    assert body["error"]["request_id"] == "req-err-500"
    assert r.headers["x-request-id"] == "req-err-500"
