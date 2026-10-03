"""指标错误分类、慢请求计数、调用方隔离、高并发对账。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.observability.metrics import MetricsRegistry, registry


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_category_counts_and_reconciliation() -> None:
    reg = MetricsRegistry(slow_threshold_ms=1000)
    reg.record("c", 1.0)  # 成功
    reg.record("c", 1.0, category="validation")
    reg.record("c", 1.0, category="dependency")
    reg.record("c", 1.0, category="unexpected")
    m = reg.snapshot("c")["c"]
    print(f"输入: 1 成功 + 三类错误各 1, 实际={m}")
    print("判定依据: total=4/errors=3；三类计数各 1 且之和==errors；total==errors+成功")
    assert m["total"] == 4
    assert m["errors"] == 3
    assert m["validation_errors"] == 1
    assert m["dependency_errors"] == 1
    assert m["unexpected_errors"] == 1
    assert (
        m["validation_errors"] + m["dependency_errors"] + m["unexpected_errors"]
        == m["errors"]
    )


def test_legacy_is_error_still_supported() -> None:
    reg = MetricsRegistry()
    reg.record("old", 1.0, is_error=False)
    reg.record("old", 1.0, is_error=True)
    m = reg.snapshot("old")["old"]
    print(f"输入: 旧调用方式 is_error False/True 各 1, 实际={m}")
    print("判定依据: total=2/errors=1，旧调用方不分类也不报错")
    assert m["total"] == 2 and m["errors"] == 1
    assert m["validation_errors"] == m["dependency_errors"] == m["unexpected_errors"] == 0


def test_slow_requests_counted() -> None:
    reg = MetricsRegistry(slow_threshold_ms=10)
    reg.record("c", 1.0)                       # 快
    reg.record("c", 30.0)                      # 慢但成功
    reg.record("c", 50.0, category="dependency")  # 慢且失败
    reg.record("c", 5.0, category="validation")   # 快但失败
    m = reg.snapshot("c")["c"]
    print(f"输入: 阈值 10ms，延迟 1/30/50/5, 实际={m}")
    print("判定依据: slow=2；慢请求与成败正交")
    assert m["slow"] == 2
    assert m["errors"] == 2


def test_explicit_slow_flag_overrides_threshold() -> None:
    reg = MetricsRegistry(slow_threshold_ms=1000)
    reg.record("c", 1.0, slow=True)
    m = reg.snapshot("c")["c"]
    print(f"输入: 阈值很高但显式标记 slow=True, 实际={m}")
    assert m["slow"] == 1


def test_concurrent_counts_reconcile() -> None:
    reg = MetricsRegistry(slow_threshold_ms=0)  # 阈值 0：全部计为慢请求
    callers = [f"iso-{i}" for i in range(4)]
    per_caller = 300
    categories = ["validation", "dependency", "unexpected", None]
    print(f"输入: {len(callers)} 调用方 × {per_caller} 次，{threading.active_count()} 线程参与并发写入")

    def worker(cid: str) -> None:
        for i in range(per_caller):
            cat = categories[i % 4]
            reg.record(cid, 1.0, category=cat)

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(worker, callers))

    snap = reg.snapshot()
    for cid in callers:
        m = snap[cid]
        categorized = (
            m["validation_errors"] + m["dependency_errors"] + m["unexpected_errors"]
        )
        print(f"判定依据: {cid} total={per_caller} 类别各 {per_caller // 4} slow={per_caller}, 实际={m}")
        assert m["total"] == per_caller, "并发下计数丢失或重复"
        assert m["errors"] == categorized, "errors 与类别之和不一致"
        assert m["validation_errors"] == per_caller // 4
        assert m["dependency_errors"] == per_caller // 4
        assert m["unexpected_errors"] == per_caller // 4
        assert m["slow"] == per_caller


def test_caller_isolation_in_categorized_metrics(client: TestClient) -> None:
    client.get("/echo", headers={"X-Caller-ID": "cat-a"})
    client.get("/boom/validation", headers={"X-Caller-ID": "cat-b"})
    client.get("/boom/dependency", headers={"X-Caller-ID": "cat-c"})
    a = registry.snapshot("cat-a")["cat-a"]
    b = registry.snapshot("cat-b")["cat-b"]
    c = registry.snapshot("cat-c")["cat-c"]
    print(f"输入: cat-a 成功 / cat-b 校验失败 / cat-c 依赖失败")
    print(f"判定依据: 各桶互不串扰且类别正确: a={a} b={b} c={c}")
    assert a["errors"] == 0
    assert b["errors"] == 1 and b["validation_errors"] == 1
    assert c["errors"] == 1 and c["dependency_errors"] == 1
