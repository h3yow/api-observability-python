"""就绪探针返回约定兼容、逐项结果、超时兜底、整体快速失败。"""
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


def _raise(exc: BaseException):
    def _check():
        raise exc
    return _check


# ---------- 返回约定归一 ----------

@pytest.mark.parametrize(
    "raw, expected_ok",
    [
        (None, True),
        (True, True),
        (False, False),
        ((True, ""), True),
        ((False, "lag"), False),
        ([False, "queue full"], False),
        ({"ok": True}, True),
        ({"healthy": False}, False),
        ({"status": "UP"}, True),
        ({"status": "down", "error": "disk ro"}, False),
        ({"state": "available", "message": "fine"}, True),
    ],
)
def test_probe_result_shapes_normalized(raw: object, expected_ok: bool) -> None:
    ok, detail = health._normalize_result(raw)
    print(f"输入: 探针返回 {raw!r} -> 期望就绪={expected_ok}, 实际=({ok}, {detail!r})")
    assert ok is expected_ok


def test_unrecognized_probe_result_is_failure() -> None:
    ok, detail = health._normalize_result("maybe?")
    print(f"输入: 裸字符串 'maybe?' -> 显式判失败而非默认健康, 实际=({ok}, {detail!r})")
    assert ok is False
    assert "unrecognized" in detail


# ---------- 逐项结果与状态码 ----------

def test_mixed_signals_make_overall_not_ready(client, clean_readiness_checks) -> None:
    health.register_readiness_check("db", lambda: None)
    health.register_readiness_check("cache", lambda: True)
    health.register_readiness_check("mq", lambda: (False, "broker 0.0.0.0:5672 refused"))
    health.register_readiness_check("blob", lambda: {"ok": False, "reason": "readonly"})
    health.register_readiness_check("queue", _raise(TimeoutError("connect")))

    status, body = health.readiness(timeout=1.0)
    print(f"输入: 5 个依赖，db/cache 正常，其余 3 个分别以 tuple/dict/异常 报失败")
    print(f"判定依据: 503 not_ready 且逐项可辨, 实际={status} {body}")
    assert status == 503
    assert body["status"] == "not_ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["cache"] == "ok"
    assert "broker 0.0.0.0:5672 refused" in body["checks"]["mq"]
    assert "readonly" in body["checks"]["blob"]
    assert "TimeoutError" in body["checks"]["queue"]
    for value in body["checks"].values():
        assert value == "ok" or value.startswith("unavailable")


def test_all_healthy_signals_ready(client, clean_readiness_checks) -> None:
    health.register_readiness_check("a", lambda: True)
    health.register_readiness_check("b", lambda: {"healthy": True})
    status, body = health.readiness()
    print(f"输入: 两个探针分别返回 True / {{healthy: True}}, 实际={status} {body}")
    assert status == 200 and body["status"] == "ready"
    assert body["checks"] == {"a": "ok", "b": "ok"}


def test_readiness_endpoint_returns_failure_status(client, clean_readiness_checks) -> None:
    health.register_readiness_check("downstream", lambda: False)
    r = client.get("/health/ready")
    print(f"输入: 探针返回 False 时 GET /health/ready, 实际={r.status_code} {r.json()}")
    print("判定依据: HTTP 状态码必须是 503，body 逐项说明")
    assert r.status_code == 503
    assert r.json()["checks"]["downstream"].startswith("unavailable")


# ---------- 超时兜底 ----------

def test_hanging_probe_times_out_and_counts_as_failure(client, clean_readiness_checks) -> None:
    health.register_readiness_check("slow", lambda: time.sleep(0.5))
    t0 = time.perf_counter()
    status, body = health.readiness(timeout=0.1)
    elapsed = time.perf_counter() - t0
    print(f"输入: 睡 0.5s 的探针 + 0.1s 超时, 实际耗时={elapsed:.2f}s 结果={body}")
    print("判定依据: 耗时接近超时上界而非 0.5s；该项为失败，整体 503")
    assert status == 503
    assert "timeout" in body["checks"]["slow"]
    assert elapsed < 0.35


def test_slow_probe_does_not_block_other_results(client, clean_readiness_checks) -> None:
    health.register_readiness_check("fast_ok", lambda: True)
    health.register_readiness_check("slow", lambda: time.sleep(0.5))
    t0 = time.perf_counter()
    status, body = health.readiness(timeout=0.1)
    elapsed = time.perf_counter() - t0
    print(f"输入: 正常探针 + 卡死探针, 实际耗时={elapsed:.2f}s 结果={body}")
    print("判定依据: 接口很快给出结论，且正常项结果不丢失")
    assert elapsed < 0.35
    assert body["checks"]["fast_ok"] == "ok"
    assert "timeout" in body["checks"]["slow"]
