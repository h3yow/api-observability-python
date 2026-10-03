"""健康检查：存活（liveness）与就绪（readiness）语义分离。

就绪探针兼容多种返回约定：抛异常 / 返回布尔 / 返回 (布尔, 说明) / 仅返回说明
字符串 / dict；任一依赖给出不可用信号即判未就绪。探针在独立守护线程中并发
执行，并受单项超时保护，卡住的探针按失败算，不拖垮整个就绪接口。
"""
from __future__ import annotations

import threading
from collections.abc import Callable

# 就绪探针：名称 -> (检查函数, 超时秒)
_readiness_checks: dict[str, tuple[Callable[[], object], float]] = {}

# 单项探针默认超时：探针不得把就绪接口无限期挂住
DEFAULT_PROBE_TIMEOUT = 2.0

# 说明字符串中表示健康的取值
_HEALTHY_WORDS = frozenset({"", "ok", "up", "ready", "healthy", "alive"})


def register_readiness_check(
    name: str,
    check: Callable[[], object],
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> None:
    """注册一个就绪依赖检查；timeout 为该探针的单项超时秒数。"""
    _readiness_checks[name] = (check, timeout)


def liveness() -> dict[str, str]:
    """存活：进程在运行即通过，不检查依赖。"""
    return {"status": "alive"}


def normalize_probe_result(value: object) -> tuple[bool, str | None]:
    """把探针的各种返回形态归一为 (是否可用, 说明)。

    兼容：None/True/False、(bool[, 说明])、纯说明字符串、
    {"ok"/"healthy"/"status": ..., "detail"/"message": ...} 等常见约定。
    """
    # bool 必须先于 int 判断（bool 是 int 的子类）
    if isinstance(value, bool):
        return value, None
    if value is None:
        return True, None
    if isinstance(value, str):
        text = value.strip()
        healthy = text.lower() in _HEALTHY_WORDS
        # 健康词不重复拼进结果，其他文本作为不可用原因
        return healthy, (None if healthy else text)
    if isinstance(value, (tuple, list)) and value:
        ok = bool(value[0])
        detail: str | None = None
        if len(value) > 1 and value[1] is not None:
            detail = str(value[1])
        return ok, detail
    if isinstance(value, dict):
        if "ok" in value:
            ok = bool(value["ok"])
        elif "healthy" in value:
            ok = bool(value["healthy"])
        elif "status" in value:
            status = value["status"]
            ok = status if isinstance(status, bool) else str(status).strip().lower() in _HEALTHY_WORDS
        else:
            ok = bool(value)
        detail = value.get("detail", value.get("message"))
        return ok, (None if detail is None else str(detail))
    # 其他形态按真值兜底
    return bool(value), None


def _start_probe(name: str, check: Callable[[], object]) -> tuple[threading.Thread, dict[str, object]]:
    """在守护线程中启动单个探针，立即返回线程与结果容器。"""
    box: dict[str, object] = {}

    def target() -> None:
        try:
            box["value"] = check()
        except BaseException as exc:  # 探针抛异常 = 明确的不可用信号
            box["error"] = exc

    worker = threading.Thread(target=target, name=f"readiness-{name}", daemon=True)
    worker.start()
    return worker, box


def _collect_probe(
    worker: threading.Thread, box: dict[str, object], timeout: float
) -> tuple[bool, str]:
    """等待单个探针到其超时上限，把结果归一为 (是否可用, 结果文本)。"""
    worker.join(timeout)
    if worker.is_alive():
        # 探针卡住：不等待线程结束（daemon，进程退出时回收），接口立即给结论
        return False, f"unavailable: timeout after {timeout:g}s"
    if "error" in box:
        return False, f"unavailable: {type(box['error']).__name__}"
    ok, detail = normalize_probe_result(box.get("value"))
    if ok:
        return True, ("ok" if not detail else f"ok: {detail}")
    return False, ("unavailable" if not detail else f"unavailable: {detail}")


def readiness() -> tuple[int, dict[str, object]]:
    """就绪：所有依赖检查通过才就绪；返回 (http_status, body)。

    所有探针并发执行、各有超时上限，接口总耗时只取决于最慢的一项，
    不会被单个卡住的依赖无限期挂住。
    """
    # 先全部启动，再逐项收取，保证探针之间真正并发
    pending = [
        (name, *_start_probe(name, check), timeout)
        for name, (check, timeout) in _readiness_checks.items()
    ]
    results: dict[str, str] = {}
    ready = True
    for name, worker, box, timeout in pending:
        ok, result = _collect_probe(worker, box, timeout)
        results[name] = result
        if not ok:
            ready = False
    status = 200 if ready else 503
    return status, {"status": "ready" if ready else "not_ready", "checks": results}
