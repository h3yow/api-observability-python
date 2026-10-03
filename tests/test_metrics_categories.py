"""错误分类别计数、慢请求计数、高并发对账、调用方隔离、端到端类别归类。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app
from app.observability.metrics import (
    CATEGORY_DEPENDENCY,
    CATEGORY_UNEXPECTED,
    CATEGORY_VALIDATION,
    MetricsRegistry,
    SLOW_REQUEST_THRESHOLD_MS,
    registry,
)


def test_category_counts_and_totals_reconcile() -> None:
    reg = MetricsRegistry()
    reg.record("c", 1.0, is_error=False)
    reg.record("c", 2.0, is_error=True, category=CATEGORY_VALIDATION)
    reg.record("c", 3.0, is_error=True, category=CATEGORY_DEPENDENCY)
    reg.record("c", 4.0, is_error=True, category=CATEGORY_UNEXPECTED)
    reg.record("c", 9.0, is_error=False)  # 成功的慢请求，不计错误
    reg.record("c", 5.0, is_error=True)  # 错误但未标类别，归入 unexpected
    m = reg.snapshot("c")["c"]
    print(f"输入: 2 成功/4 错误（validation/dependency/unexpected/未标类别）, 实际={m}")
    print("判定依据: total=6, successes+errors=6, 三类错误之和=errors=4")
    assert m["total"] == 6
    assert m["successes"] + m["errors"] == m["total"]
    assert m["validation_errors"] == 1
    assert m["dependency_errors"] == 1
    assert m["unexpected_errors"] == 2
    assert (
        m["validation_errors"] + m["dependency_errors"] + m["unexpected_errors"]
        == m["errors"]
    )


def test_unknown_category_is_counted_as_unexpected() -> None:
    reg = MetricsRegistry()
    reg.record("c", 1.0, is_error=True, category="typo-category")
    m = reg.snapshot("c")["c"]
    print(f"输入: 错误类别拼写错误, 实际={m}")
    print("判定依据: 未识别类别不丢计数，归入 unexpected 且总和仍对得上")
    assert m["errors"] == 1 and m["unexpected_errors"] == 1


def test_slow_requests_counted_for_success_and_failure() -> None:
    reg = MetricsRegistry()
    reg.record("c", SLOW_REQUEST_THRESHOLD_MS - 1, is_error=False)
    reg.record("c", SLOW_REQUEST_THRESHOLD_MS, is_error=False)       # 恰好达到阈值
    reg.record("c", SLOW_REQUEST_THRESHOLD_MS + 50, is_error=True,
               category=CATEGORY_DEPENDENCY)
    reg.record("c", 10.0, is_error=False, is_slow=False)             # 显式非慢
    m = reg.snapshot("c")["c"]
    print(f"输入: 阈值={SLOW_REQUEST_THRESHOLD_MS}ms，1 快/1 临界/1 慢失败/1 显式非慢, 实际={m}")
    print("判定依据: 达到阈值即慢，慢请求与成功/失败正交，slow_requests=2")
    assert m["slow_requests"] == 2


def test_caller_isolation_remains() -> None:
    reg = MetricsRegistry()
    reg.record("team-a", 1.0, is_error=True, category=CATEGORY_VALIDATION)
    reg.record("team-b", 1.0, is_error=False)
    a = reg.snapshot("team-a")["team-a"]
    b = reg.snapshot("team-b")["team-b"]
    print(f"判定依据: team-a 校验错误不串到 team-b, a={a} b={b}")
    assert a["validation_errors"] == 1 and b["validation_errors"] == 0
    assert b["errors"] == 0 and b["total"] == 1


def test_concurrent_category_counts_no_loss() -> None:
    reg = MetricsRegistry()
    categories = [CATEGORY_VALIDATION, CATEGORY_DEPENDENCY, CATEGORY_UNEXPECTED, None]
    threads = 16
    per_thread = 500
    barrier = threading.Barrier(threads)
    print(f"输入: {threads} 线程 × {per_thread} 次，4 种类别/成功混合并发写入")

    def worker(tid: int) -> None:
        barrier.wait()  # 尽量同时开写，制造真实竞争
        for i in range(per_thread):
            cat = categories[i % len(categories)]
            if cat is None:
                reg.record(f"bulk-{tid % 3}", 1.0, is_error=False)
            else:
                reg.record(f"bulk-{tid % 3}", 1.0, is_error=True, category=cat)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(worker, range(threads)))

    snap = reg.snapshot()
    total = sum(m["total"] for m in snap.values())
    errors = sum(m["errors"] for m in snap.values())
    cat_sum = sum(
        m["validation_errors"] + m["dependency_errors"] + m["unexpected_errors"]
        for m in snap.values()
    )
    successes = sum(m["successes"] for m in snap.values())
    print(f"判定依据: total={total}(期望{threads*per_thread}), "
          f"errors={errors}, 类别之和={cat_sum}, successes={successes}")
    assert total == threads * per_thread, "并发下总数丢失或重复"
    assert cat_sum == errors, "类别明细之和必须等于错误总数"
    assert successes + errors == total


# ---------- 端到端：HTTP 归类进入指标 ----------

def test_http_error_categories_recorded_per_caller() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    cid = "metric-cat-e2e"
    client.get("/echo", headers={"X-Caller-ID": cid})
    client.get("/boom/validation", headers={"X-Caller-ID": cid})
    client.get("/boom/dependency", headers={"X-Caller-ID": cid})
    client.get("/boom/unexpected", headers={"X-Caller-ID": cid})
    m = registry.snapshot(cid)[cid]
    print(f"输入: 1 成功 + 三类错误各 1 次, 实际={m}")
    print("判定依据: 三类错误各计 1，total=4，successes=1")
    assert m["total"] == 4 and m["successes"] == 1 and m["errors"] == 3
    assert m["validation_errors"] == 1
    assert m["dependency_errors"] == 1
    assert m["unexpected_errors"] == 1


def test_uncaught_fastapi_4xx_counts_as_validation() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    cid = "metric-4xx"
    before = registry.snapshot(cid)[cid]
    # /echo 无必填参数；触发 FastAPI 路由级 404（非业务异常），按 4xx 归校验类
    client.get("/no-such-path", headers={"X-Caller-ID": cid})
    after = registry.snapshot(cid)[cid]
    print(f"输入: 未匹配路由 404, 之前={before} 之后={after}")
    print("判定依据: 4xx 兜底归 validation，计数 +1")
    assert after["errors"] == before["errors"] + 1
    assert after["validation_errors"] == before["validation_errors"] + 1
