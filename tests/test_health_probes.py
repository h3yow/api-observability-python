"""就绪探针返回约定兼容、逐项结论、超时兜底、异常隔离。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.observability import health


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def clean_readiness_checks():
    saved = dict(health._readiness_checks)
    health._readiness_checks.clear()
    yield
    health._readiness_checks.clear()
    health._readiness_checks.update(saved)


# ---------- 返回约定归一化 ----------

@pytest.mark.parametrize(
    "value, expected_ok",
    [
        (None, True),
        (True, True),
        (False, False),
        ("ok", True),
        ("", True),
        ("UP", True),
        ("ready", True),
        ("healthy", True),
        ("connection refused 10.0.0.8:5432", False),
        ((True, "lag 20ms"), True),
        ((False, "pool exhausted"), False),
        ([False], False),
        ({"ok": True}, True),
        ({"healthy": False}, False),
        ({"status": "ok"}, True),
        ({"status": "down", "detail": "disk full"}, False),
        (0, False),
        (1, True),
    ],
)
def test_normalize_probe_result(value, expected_ok: bool) -> None:
    ok, detail = health.normalize_probe_result(value)
    print(f"输入: 探针返回={value!r} -> 期望可用={expected_ok}, 实际=({ok}, {detail!r})")
    assert ok is expected_ok


def test_normalize_bool_before_int() -> None:
    # bool 是 int 子类，必须按布尔语义处理而非数值真值对象
    ok, _ = health.normalize_probe_result(True)
    print(f"输入: True -> 必须按布尔判定为可用, 实际={ok}")
    assert ok is True


# ---------- 端到端：各形态 + HTTP 语义 ----------

def test_probe_shapes_over_http(client: TestClient, clean_readiness_checks) -> None:
    health.register_readiness_check("raises", lambda: (_ for _ in ()).throw(ConnectionError("refused")))
    health.register_readiness_check("returns_false", lambda: False)
    health.register_readiness_check("returns_tuple_bad", lambda: (False, "pool exhausted"))
    health.register_readiness_check("returns_reason", lambda: "disk full")
    health.register_readiness_check("returns_dict_bad", lambda: {"ok": False, "detail": "replication lag"})
    health.register_readiness_check("healthy_none", lambda: None)
    health.register_readiness_check("healthy_true", lambda: True)
    health.register_readiness_check("healthy_tuple", lambda: (True, "lag 20ms"))

    r = client.get("/health/ready")
    body = r.json()
    print(f"输入: 5 个失败形态 + 3 个成功形态, 实际={r.status_code} {body}")
    print("判定依据: 任一失败信号 -> 503 not_ready；每项可看出依赖与结果")
    assert r.status_code == 503
    assert body["status"] == "not_ready"
    assert body["checks"]["raises"].startswith("unavailable: ConnectionError")
    assert body["checks"]["returns_false"] == "unavailable"
    assert body["checks"]["returns_tuple_bad"] == "unavailable: pool exhausted"
    assert body["checks"]["returns_reason"] == "unavailable: disk full"
    assert body["checks"]["returns_dict_bad"] == "unavailable: replication lag"
    assert body["checks"]["healthy_none"] == "ok"
    assert body["checks"]["healthy_true"] == "ok"
    assert body["checks"]["healthy_tuple"] == "ok: lag 20ms"


def test_all_probes_healthy(client: TestClient, clean_readiness_checks) -> None:
    health.register_readiness_check("db", lambda: True)
    health.register_readiness_check("cache", lambda: None)
    r = client.get("/health/ready")
    print(f"输入: 全部探针健康, 实际={r.status_code} {r.json()}")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_legacy_exception_probe_compatible(client: TestClient, clean_readiness_checks) -> None:
    # 旧行为：无返回值表示正常，抛 TimeoutError 表示未就绪，结果仍以 unavailable: 类型名呈现
    health.register_readiness_check("db", lambda: None)
    health.register_readiness_check("cache", lambda: (_ for _ in ()).throw(TimeoutError("t")))
    r = client.get("/health/ready")
    body = r.json()
    print(f"输入: 旧版探针(None / 抛 TimeoutError), 实际={r.status_code} {body}")
    assert r.status_code == 503
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["cache"] == "unavailable: TimeoutError"


# ---------- 超时兜底 ----------

def test_hanging_probe_times_out_and_fails(client: TestClient, clean_readiness_checks) -> None:
    def hang() -> None:
        time.sleep(5)

    health.register_readiness_check("slowdb", hang, timeout=0.2)
    start = time.perf_counter()
    r = client.get("/health/ready")
    elapsed = time.perf_counter() - start
    body = r.json()
    print(f"输入: 探针 sleep(5)，超时 0.2s；接口实际耗时={elapsed:.2f}s, body={body}")
    print("判定依据: 超时按失败算 -> 503；接口必须在超时量级快速返回")
    assert r.status_code == 503
    assert body["status"] == "not_ready"
    assert "timeout" in body["checks"]["slowdb"]
    assert elapsed < 1.0, f"接口被卡住 {elapsed:.2f}s"


def test_timeout_runs_concurrent_with_others(client: TestClient, clean_readiness_checks) -> None:
    def hang() -> None:
        time.sleep(5)

    health.register_readiness_check("slow", hang, timeout=0.3)
    health.register_readiness_check("fast-down", lambda: False, timeout=0.3)
    start = time.perf_counter()
    r = client.get("/health/ready")
    elapsed = time.perf_counter() - start
    body = r.json()
    print(f"输入: 卡住探针与普通探针同时注册, 耗时={elapsed:.2f}s, body={body}")
    print("判定依据: 探针并发执行，总耗时不叠加（< 0.9s），两项都有结论")
    assert r.status_code == 503
    assert "timeout" in body["checks"]["slow"]
    assert body["checks"]["fast-down"] == "unavailable"
    assert elapsed < 0.9
