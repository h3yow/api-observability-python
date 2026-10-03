"""语义化敏感字段识别：多团队自由命名、深层嵌套、请求头形态、脱敏失败不丢日志。"""
from __future__ import annotations

from app.observability.logging import MASK, is_sensitive_name, mask_sensitive


# ---------- 语义命名识别 ----------

def test_sensitive_name_variants() -> None:
    cases = {
        # 连字符 / 下划线 / 点 / 空格 / 大小写
        "user-password": True,
        "db_password_hash": True,
        "X-Auth-Token": True,
        "client.secret.value": True,
        "SERVICE PASSWORD": True,
        # 驼峰与全大写
        "userPassword": True,
        "apiKey": True,
        "accessToken": True,
        "refreshToken": True,
        "HTTPPassword": True,
        # 全小写黏连
        "dbpassword": True,
        "userpwd": True,
        "mysecretkey": True,
        # 复数
        "tokens": True,
        "credentials": True,
        # 中文语义
        "登录密码": True,
        "用户口令": True,
        "签名密钥": True,
        "访问令牌": True,
        "鉴权信息": True,
        "临时凭证": True,
        "用户凭据": True,
        # 标准名保持行为
        "password": True,
        "token": True,
        "Authorization": True,
        "api_key": True,
        "apikey": True,
    }
    for name, expected in cases.items():
        actual = is_sensitive_name(name)
        print(f"输入: 字段名={name!r} -> 期望敏感={expected}, 实际={actual}")
        assert actual is expected, name


def test_plain_business_fields_not_masked() -> None:
    cases = [
        "username",
        "user_id",
        "order_tokenization_scheme",  # 含 token 子串但不是令牌字段
        "method",
        "product_name",
        "author_note",
        "keystone_config",            # key 子串的普通配置
        "monopoly_score",
        "created_at",
        "手机号",
        "订单编号",
    ]
    for name in cases:
        print(f"输入: 业务字段名={name!r} -> 必须不敏感, 实际={is_sensitive_name(name)}")
        assert is_sensitive_name(name) is False, name


# ---------- 深度嵌套 / 数组 / 请求头 ----------

def test_mask_deeply_nested_and_arrays() -> None:
    data = {
        "outer": [
            {"inner": [{"userPassword": "p"}, {"name": "keep"}]},
            {"config": {"db.private_key": "k", "region": "cn"}},
        ],
        "n": 42,
    }
    masked = mask_sensitive(data)
    print(f"输入: {data}")
    print(f"判定依据: 任意深度的敏感键都置 {MASK}，业务字段保留, 实际={masked}")
    assert masked["outer"][0]["inner"][0] == {"userPassword": MASK}
    assert masked["outer"][0]["inner"][1] == {"name": "keep"}
    assert masked["outer"][1]["config"]["db.private_key"] == MASK
    assert masked["outer"][1]["config"]["region"] == "cn"
    assert masked["n"] == 42


def test_mask_header_pairs() -> None:
    # 请求头形态：二元组/二元数组，名字敏感则掩码其值
    data = {
        "headers": [
            ("Authorization", "Bearer abc.def"),
            ["x-api-token", "t-1"],
            ("X-Trace-Id", "trace-1"),
        ],
    }
    masked = mask_sensitive(data)
    print(f"输入: {data}")
    print(f"判定依据: 敏感请求头的值置 {MASK}，普通头保留, 实际={masked}")
    assert masked["headers"][0] == ["Authorization", MASK]
    assert masked["headers"][1] == ["x-api-token", MASK]
    assert masked["headers"][2] == ["X-Trace-Id", "trace-1"]


def test_mask_header_pairs_not_confused_with_business_pairs() -> None:
    # 普通业务二元数组（值不是字符串）不得被当成请求头掩码
    data = {"series": [["token_count", 5], ["retries", 0]]}
    masked = mask_sensitive(data)
    print(f"输入: {data} -> 业务计数数组应原样保留, 实际={masked}")
    assert masked == {"series": [["token_count", 5], ["retries", 0]]}


def test_mask_headers_as_dict_keys_too() -> None:
    # 头部也可能以 dict 形式出现
    data = {"headers": {"X-User-Password": "p", "X-Request-Id": "r1"}}
    masked = mask_sensitive(data)
    print(f"输入: {data}, 实际={masked}")
    assert masked["headers"]["X-User-Password"] == MASK
    assert masked["headers"]["X-Request-Id"] == "r1"


# ---------- 脱敏健壮性 ----------

def test_mask_failure_still_emits_placeholder() -> None:
    class BadValue:
        def __repr__(self):  # 序列化/处理过程中抛错
            raise RuntimeError("repr boom")

    class BrokenList(list):
        def __iter__(self):  # 遍历即失败
            raise RuntimeError("iter boom")

    for data in (BrokenList([1, 2]), {"a": BrokenList([1])}):
        result = mask_sensitive(data)
        print(f"输入: 处理时会抛错的结构 -> 必须返回整体 {MASK}, 实际={result!r}")
        assert result == MASK


def test_standard_keys_behaviour_unchanged() -> None:
    data = {
        "password": "p",
        "token": "t",
        "authorization": "a",
        "secret": "s",
        "api_key": "k1",
        "apikey": "k2",
        "name": "bob",
    }
    masked = mask_sensitive(data)
    print(f"输入: 标准字段名集合 -> 六个敏感键全掩码, 实际={masked}")
    assert masked == {
        "password": MASK,
        "token": MASK,
        "authorization": MASK,
        "secret": MASK,
        "api_key": MASK,
        "apikey": MASK,
        "name": "bob",
    }
