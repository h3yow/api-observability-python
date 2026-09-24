"""异常分类：三类错误正确归类、原因链可追溯、对外不泄露内部细节。"""
from __future__ import annotations

import pytest

from app.errors import (
    AppError,
    DependencyUnavailableError,
    ErrorCategory,
    ValidationFailedError,
    cause_chain,
)
from tests.conftest import test_logger


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_category"),
    [
        ("/demo/validation", 422, "validation_error"),
        ("/demo/dependency", 503, "dependency_unavailable"),
        ("/demo/unexpected", 500, "internal_error"),
    ],
)
def test_error_classification(
    client, path: str, expected_status: int, expected_category: str
) -> None:
    response = client.get(path, headers={"X-Request-ID": f"rid-{expected_category}"})
    body = response.json()
    test_logger.info(
        "输入=GET %s 判定依据=状态码与 error.category 必须匹配分类 -> "
        "期望=(%d, %s) 实际=(%d, %s)",
        path, expected_status, expected_category,
        response.status_code, body["error"]["category"],
    )
    assert response.status_code == expected_status
    assert body["error"]["category"] == expected_category
    # 错误响应必须能通过 request id 对应到同一次请求。
    assert body["error"]["request_id"] == f"rid-{expected_category}"
    assert response.headers["x-request-id"] == f"rid-{expected_category}"


def test_response_does_not_leak_internals(client) -> None:
    response = client.get("/demo/unexpected")
    body = response.json()
    test_logger.info(
        "输入=GET /demo/unexpected 判定依据=对外响应不得包含内部实现细节 -> 响应=%s", body
    )
    assert body["error"]["message"] == "internal error"
    assert "pricing engine" not in str(body)
    assert "detail" not in body["error"]


def test_cause_chain_preserved() -> None:
    try:
        try:
            raise ConnectionError("db refused")
        except ConnectionError as exc:
            raise DependencyUnavailableError(detail="repo unreachable", cause=exc)
    except AppError as exc:
        chain = cause_chain(exc)
        test_logger.info(
            "输入=ConnectionError 包装为 DependencyUnavailableError "
            "判定依据=原因链自外向内完整可追溯 -> 链=%s", chain,
        )
    assert chain[0].startswith("DependencyUnavailableError")
    assert any("ConnectionError" in link and "db refused" in link for link in chain[1:])


def test_categories_are_distinct() -> None:
    categories = {
        ValidationFailedError().category,
        DependencyUnavailableError().category,
        AppError().category,
    }
    test_logger.info(
        "输入=三种异常类型 判定依据=分类枚举互不相同 -> 实际=%s",
        {c.value for c in categories},
    )
    assert categories == {
        ErrorCategory.VALIDATION,
        ErrorCategory.DEPENDENCY,
        ErrorCategory.UNEXPECTED,
    }


def test_fastapi_validation_error_classified(client) -> None:
    """FastAPI 自身的请求校验错误也归入 validation_error。"""
    response = client.post("/demo/ok", content=b"not-json", headers={"Content-Type": "application/json"})
    body = response.json()
    test_logger.info(
        "输入=POST /demo/ok 非法JSON 判定依据=归入 validation_error 且状态422 -> "
        "实际=(%d, %s)", response.status_code, body["error"]["category"],
    )
    assert response.status_code == 422
    assert body["error"]["category"] == "validation_error"
