"""请求标识：合法性判定、生成回退与并发隔离。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.request_id import is_valid_request_id, resolve_request_id
from tests.conftest import test_logger


@pytest.mark.parametrize(
    ("provided", "expected_valid"),
    [
        ("req-001", True),
        ("ABC_def.123:xyz", True),
        ("a" * 128, True),
        ("", False),
        ("a" * 129, False),
        ("has space", False),
        ("含中文", False),
        ("-leading-dash", False),
        ("ctrl\tchar", False),
    ],
)
def test_validity_rules(provided: str, expected_valid: bool) -> None:
    actual = is_valid_request_id(provided)
    test_logger.info(
        "输入=%r 判定依据=合法字符集且长度1~128 -> 期望=%s 实际=%s",
        provided, expected_valid, actual,
    )
    assert actual is expected_valid


def test_resolve_deterministic_fallback() -> None:
    reused = resolve_request_id("caller-given-1")
    generated_a = resolve_request_id(None)
    generated_b = resolve_request_id("bad id")
    test_logger.info(
        "输入=('caller-given-1', None, 'bad id') 判定依据=合法沿用/非法或缺失则生成 -> "
        "沿用=%s 生成A=%s 生成B=%s", reused, generated_a, generated_b,
    )
    assert reused == "caller-given-1"
    assert generated_a != generated_b  # 每次生成确定性地产生新标识
    assert is_valid_request_id(generated_a) and is_valid_request_id(generated_b)


def test_concurrent_requests_no_crosstalk(client) -> None:
    """并发请求各自携带唯一标识，响应头与上下文日志必须一一对应。"""
    ids = [f"concurrent-rid-{i:03d}" for i in range(32)]

    def call(rid: str) -> tuple[str, str, str | None]:
        response = client.get("/demo/context", headers={"X-Request-ID": rid})
        return rid, response.headers["x-request-id"], response.json()["context_request_id"]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(call, ids))

    for sent, echoed, ctx in results:
        test_logger.info(
            "输入=X-Request-ID:%s 判定依据=响应头与上下文变量都必须等于该标识 -> "
            "响应头=%s 上下文=%s", sent, echoed, ctx,
        )
        assert echoed == sent
        assert ctx == sent
    assert len({echoed for _, echoed, _ in results}) == len(ids)
