"""语义化脱敏：任意命名风格/深层嵌套/请求头形态的敏感字段都掩码，普通字段不误伤。"""
from __future__ import annotations

import pytest

from app.observability.logging import MASK, is_sensitive_name, mask_sensitive


# ---------- 名称判定 ----------

@pytest.mark.parametrize(
    "name",
    [
        # 标准命名（历史行为不变）
        "password", "Password", "PASSWORD", "token", "Authorization",
        "secret", "api_key", "apiKey",
        # 前后缀 + 各种分隔/命名风格：密码
        "userPassword", "user-password", "user_password", "USER_PASSWORD",
        "db.pwd", "oldPasswd", "loginPwd", "account_passcode",
        # 密钥
        "my_secret", "clientSecretValue", "secretKey", "x-api-key",
        "access_key_id", "privateKeyPem", "signing-key", "security_key",
        # 令牌 / 鉴权
        "accessToken", "refresh_token", "X-Auth-Token", "authCode",
        "basicAuthenticationHeader", "jwtTokenValue", "credential",
        "caller_credentials",
        # 连写
        "apikey2026", "authTokenHeader", "accessTokenCache",
        # 中文语义
        "登录密码", "用户口令", "私钥内容", "访问令牌", "鉴权头", "临时凭证",
    ],
)
def test_sensitive_names_recognized(name: str) -> None:
    actual = is_sensitive_name(name)
    print(f"输入: 字段名 {name!r} -> 期望敏感, 实际={actual}")
    assert actual is True


@pytest.mark.parametrize(
    "name",
    [
        "username", "primary_key", "partition_key", "sort_key", "keyboard_layout",
        "author", "authority_level", "tokenize_enabled", "product_name",
        "key_version", "bucket_name", "user_id", "orderNo",
    ],
)
def test_plain_names_not_flagged(name: str) -> None:
    actual = is_sensitive_name(name)
    print(f"输入: 普通业务字段 {name!r} -> 期望不敏感, 实际={actual}")
    assert actual is False


# ---------- 递归脱敏 ----------

def test_mask_deep_nesting_arrays_and_headers() -> None:
    data = {
        "outer": [
            {"inner": [{"dbPassword": "p"}, {"note": "keep"}]},
            [["authorization", "Bearer abc"]],  # 请求头二元组形态
        ],
        "traceHeaders": [["X-Auth-Token", "t"], ["X-Trace-Id", "keep-1"]],
        "safe": {"primary_key": 7, "author": "carol"},
    }
    masked = mask_sensitive(data)
    print(f"输入: {data}")
    print(f"判定依据: 深层数组/请求头内敏感值置 {MASK}，普通键保留, 实际={masked}")
    assert masked["outer"][0]["inner"][0] == {"dbPassword": MASK}
    assert masked["outer"][0]["inner"][1] == {"note": "keep"}
    assert masked["outer"][1] == [["authorization", MASK]]
    assert masked["traceHeaders"] == [["X-Auth-Token", MASK], ["X-Trace-Id", "keep-1"]]
    assert masked["safe"] == {"primary_key": 7, "author": "carol"}


def test_mask_standard_names_unchanged_behavior() -> None:
    data = {"password": "1", "token": "2", "authorization": "3",
            "secret": "4", "api_key": "5", "apikey": "6", "name": "n"}
    masked = mask_sensitive(data)
    print(f"输入: {data}")
    print(f"判定依据: 六个标准命名全部 {MASK}，普通字段保留, 实际={masked}")
    assert masked == {
        "password": MASK, "token": MASK, "authorization": MASK,
        "secret": MASK, "api_key": MASK, "apikey": MASK, "name": "n",
    }


class _ExplodingList(list):
    """遍历时抛错的损坏 list，模拟脱敏处理某个子树时出问题。"""

    def __iter__(self):
        raise RuntimeError("iter boom")


def test_mask_failure_still_outputs_other_fields() -> None:
    data = {"ok_field": "kept", "bad_list": _ExplodingList([1]), "after": 1}
    masked = mask_sensitive(data)
    print(f"输入: 含遍历即抛错对象的 dict, 实际={masked}")
    print("判定依据: 异常不向上抛、整条日志不丢；出错子树掩码，其余字段保留")
    assert isinstance(masked, dict)
    assert masked["ok_field"] == "kept"
    assert masked["after"] == 1
    assert masked["bad_list"] == MASK


def test_mask_self_referential_structure_safe() -> None:
    data: dict = {"name": "loop"}
    data["self"] = data  # 循环引用：依赖深度上限截断，不能无限递归
    masked = mask_sensitive(data)
    print(f"输入: 自引用 dict, 实际类型={type(masked).__name__}")
    print("判定依据: 不抛异常（深度超限时截断），日志仍可输出")
    assert masked["name"] == "loop"
