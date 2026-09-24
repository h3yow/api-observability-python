"""指标按调用方隔离、并发计数不丢失、存活/就绪语义区分。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.observability import health
from app.observability.metrics import MetricsRegistry, registry


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------- 指标 ----------

def test_metrics_isolated_per_caller(client: TestClient) -> None:
    print("输入: caller=mt-a 成功 2 次；caller=mt-b 触发 1 次校验错误")
    for _ in range(2):
        client.get("/echo", headers={"X-Caller-ID": "mt-a"})
    client.get("/boom/validation", headers={"X-Caller-ID": "mt-b"})

    a = registry.snapshot("mt-a")["mt-a"]
    b = registry.snapshot("mt-b")["mt-b"]
    print(f"判定依据: mt-a 应 total=2/errors=0, 实际={a}")
    assert a["total"] == 2 and a["errors"] == 0
    print(f"判定依据: mt-b 应 total=1/errors=1（4xx 计入错误）, 实际={b}")
    assert b["total"] == 1 and b["errors"] == 1
    assert a["latency_avg_ms"] >= 0 and b["latency_max_ms"] >= 0


def test_metrics_concurrent_no_loss() -> None:
    reg = MetricsRegistry()
    callers = [f"conc-{i}" for i in range(6)]
    per_caller = 500
    print(f"输入: {len(callers)} 个调用方 × {per_caller} 次记录，多线程并发写入")

    def worker(cid: str) -> None:
        for i in range(per_caller):
            reg.record(cid, 1.0, is_error=(i % 2 == 0))

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(worker, callers))

    snap = reg.snapshot()
    for cid in callers:
        m = snap[cid]
        print(f"判定依据: {cid} 应 total={per_caller}/errors={per_caller // 2}/avg=1.0, 实际={m}")
        assert m["total"] == per_caller, "并发下计数丢失"
        assert m["errors"] == per_caller // 2
        assert m["latency_avg_ms"] == 1.0


def test_metrics_endpoint_filters_by_caller(client: TestClient) -> None:
    client.get("/echo", headers={"X-Caller-ID": "filter-me"})
    r = client.get("/metrics", params={"caller_id": "filter-me"})
    body = r.json()
    print(f"输入: GET /metrics?caller_id=filter-me, 实际={body}")
    print("判定依据: 只返回指定调用方，不混入其他 caller 的数据")
    assert list(body["callers"].keys()) == ["filter-me"]


def test_unknown_caller_bucket(client: TestClient) -> None:
    before = registry.snapshot("unknown")["unknown"]["total"]
    print(f"输入: 不带 X-Caller-ID 与带非法 X-Caller-ID 各 1 次, 之前 unknown.total={before}")
    client.get("/echo")
    client.get("/echo", headers={"X-Caller-ID": "bad caller!"})
    after = registry.snapshot("unknown")["unknown"]["total"]
    print(f"判定依据: 缺失与非法 caller 都归入 unknown, 之后 unknown.total={after}")
    assert after == before + 2


# ---------- 健康检查 ----------

@pytest.fixture()
def clean_readiness_checks():
    saved = dict(health._readiness_checks)
    health._readiness_checks.clear()
    yield
    health._readiness_checks.clear()
    health._readiness_checks.update(saved)


def test_liveness_ignores_dependencies(client: TestClient, clean_readiness_checks) -> None:
    health.register_readiness_check("db", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    r = client.get("/health/live")
    print(f"输入: 依赖 db 宕机时调用 /health/live, 实际={r.status_code} {r.json()}")
    print("判定依据: 存活只看进程是否运行，不受依赖影响，必须 200")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_readiness_reflects_dependencies(client: TestClient, clean_readiness_checks) -> None:
    r = client.get("/health/ready")
    print(f"输入: 无依赖检查时 /health/ready, 实际={r.status_code} {r.json()}")
    assert r.status_code == 200 and r.json()["status"] == "ready"

    health.register_readiness_check("db", lambda: None)
    health.register_readiness_check("cache", lambda: (_ for _ in ()).throw(TimeoutError("t")))
    r = client.get("/health/ready")
    body = r.json()
    print(f"输入: db 正常 / cache 超时, 实际={r.status_code} {body}")
    print("判定依据: 任一依赖失败则 503 not_ready，且逐项给出检查结果")
    assert r.status_code == 503
    assert body["status"] == "not_ready"
    assert body["checks"]["db"] == "ok"
    assert "unavailable" in body["checks"]["cache"]
