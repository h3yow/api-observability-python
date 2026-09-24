"""脱敏边界：敏感键命中、嵌套结构、内嵌键值对、脱敏失败降级。"""
from __future__ import annotations

import pytest

from app.masking import MASK, mask_mapping, mask_value
from tests.conftest import test_logger


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"password": "p@ssw0rd"}, {"password": MASK}),
        ({"Authorization": "Bearer abc"}, {"Authorization": MASK}),
        ({"x-api-key": "k"}, {"x-api-key": MASK}),
        ({"user_token_id": "t"}, {"user_token_id": MASK}),  # 子串命中 token
        ({"username": "alice"}, {"username": "alice"}),  # 非敏感键保留
        ({"note": "plain text"}, {"note": "plain text"}),
        (
            {"outer": {"secret": "s", "keep": 1}},
            {"outer": {"secret": MASK, "keep": 1}},
        ),
        (
            {"items": [{"token": "t1"}, {"token": "t2"}]},
            {"items": [{"token": MASK}, {"token": MASK}]},
        ),
        ({"count": 42, "ok": True}, {"count": 42, "ok": True}),  # 非字符串原样保留
    ],
)
def test_masking_rules(data: dict, expected: dict) -> None:
    actual = mask_mapping(data)
    test_logger.info(
        "输入=%s 判定依据=敏感键(大小写不敏感/子串)整体置%s，其余保留 -> 实际=%s",
        data, MASK, actual,
    )
    assert actual == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("token=abc123 end", f"token={MASK} end"),
        ("password: hunter2", f"password:{MASK}"),
        ("user=alice token=t1", f"user=alice token={MASK}"),
        ("no secrets here", "no secrets here"),
    ],
)
def test_embedded_kv_masking(text: str, expected: str) -> None:
    actual = mask_value(text)
    test_logger.info(
        "输入=%r 判定依据=字符串内嵌敏感键值对仅脱敏值部分 -> 实际=%r", text, actual
    )
    assert actual == expected


def test_masking_failure_does_not_lose_log() -> None:
    """脱敏过程自身异常时降级为安全占位，绝不抛出、绝不丢失记录。"""

    class BrokenMapping(dict):
        def items(self):  # 模拟遍历时爆炸的异常对象
            raise RuntimeError("iteration boom")

    result = mask_mapping(BrokenMapping())
    test_logger.info(
        "输入=遍历时抛异常的字典 判定依据=降级为安全占位且不抛出 -> 实际=%s", result
    )
    assert result == {"_masking_error": MASK}


def test_non_string_keys_and_values() -> None:
    data = {1: "one", ("k",): {"password": "p"}}
    actual = mask_mapping(data)
    test_logger.info(
        "输入=%s 判定依据=非字符串键不命中敏感词，值仍递归脱敏 -> 实际=%s", data, actual
    )
    assert actual[1] == "one"
    assert actual[("k",)] == {"password": MASK}
