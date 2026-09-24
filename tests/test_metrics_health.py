"""指标按调用方隔离、并发计数准确、存活/就绪语义可区分。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.health import health_state
from app.metrics import registry
from tests.conftest import test_logger


def test_metrics_isolated_per_caller(client) -> None:
    client.get("/", headers={"X-Caller-ID": "caller-a"})
    client.get("/", headers={"X-Caller-ID": "caller-a"})
    client.get("/demo/validation", headers={"X-Caller-ID": "caller-b"})
    client.get("/")  # 无调用方标识 -> anonymous

    snap = registry.snapshot()
    test_logger.info(
        "输入=a×2正常 + b×1校验失败 + 匿名×1 判定依据=各调用方独立计数不混算 -> 实际=%s",
        snap,
    )
    assert snap["caller-a"]["total"] == 2
    assert snap["caller-a"]["client_errors"] == 0
    assert snap["caller-b"]["total"] == 1
    assert snap["caller-b"]["client_errors"] == 1
    assert snap["anonymous"]["total"] == 1


def test_invalid_caller_id_falls_back_to_anonymous(client) -> None:
    client.get("/", headers={"X-Caller-ID": "bad caller!!"})
    snap = registry.snapshot()
    test_logger.info(
        "输入=非法调用方标识 'bad caller!!' 判定依据=确定性归入 anonymous -> 实际=%s",
        snap,
    )
    assert snap["anonymous"]["total"] == 1
    assert "bad caller!!" not in snap


def test_concurrent_metrics_no_loss(client) -> None:
    callers = [f"svc-{i}" for i in range(8)]
    per_caller = 50

    def call(args) -> None:
        idx, caller = args
        path = "/demo/unexpected" if idx % 10 == 0 else "/"
        client.get(path, headers={"X-Caller-ID": caller})

    tasks = [(i, caller) for caller in callers for i in range(per_caller)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(call, tasks))

    snap = registry.snapshot()
    test_logger.info(
        "输入=8调用方×%d请求(10%%触发500) 判定依据=总数不丢失不重复且按调用方隔离 -> 实际总数=%s",
        per_caller, {k: v["total"] for k, v in snap.items()},
    )
    for caller in callers:
        assert snap[caller]["total"] == per_caller
        assert snap[caller]["server_errors"] == per_caller // 10
        assert snap[caller]["latency_max_ms"] >= snap[caller]["latency_avg_ms"] > 0


def test_liveness_vs_readiness(client) -> None:
    # 存活不依赖外部资源。
    live = client.get("/health/live")
    assert live.status_code == 200 and live.json()["status"] == "alive"

    # 注册一个失败的依赖后：不就绪但仍存活。
    health_state.register_readiness("broken-dep", lambda: False)
    ready = client.get("/health/ready")
    test_logger.info(
        "输入=就绪检查含失败依赖 判定依据=ready=503且逐项可见，live 仍=200 -> "
        "实际=(live=%d, ready=%d %s)",
        live.status_code, ready.status_code, ready.json(),
    )
    assert ready.status_code == 503
    assert ready.json()["checks"]["broken-dep"] is False
    assert client.get("/health/live").status_code == 200


def test_readiness_check_exception_counts_as_not_ready(client) -> None:
    def boom() -> bool:
        raise RuntimeError("check exploded")

    health_state.register_readiness("exploding", boom)
    ready = client.get("/health/ready")
    test_logger.info(
        "输入=就绪检查抛异常 判定依据=异常视为不就绪且不影响其他检查 -> 实际=%s",
        ready.json(),
    )
    assert ready.status_code == 503
    assert ready.json()["checks"]["exploding"] is False
