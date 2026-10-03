"""健康检查：存活（liveness）与就绪（readiness）语义分离。

就绪探针兼容多种团队约定的返回形态：

- 返回 ``None``：正常（历史约定，保持兼容）；
- 抛异常：未就绪，异常类型与消息记入结果（历史约定）；
- 返回 ``bool``：``True`` 就绪 / ``False`` 未就绪；
- 返回 ``(ok, detail)`` 二元组或列表：``ok`` 真假 + 说明；
- 返回 dict：``ok``/``healthy`` 布尔键或 ``status`` 状态键，
  搭配 ``detail``/``message``/``reason``/``error`` 说明键。

探针卡住时按单探针超时兜底：超时按失败算，就绪接口不会被无限期挂住。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable

# 就绪探针：名称 -> 检查函数
_readiness_checks: dict[str, Callable[[], object]] = {}

# 单探针默认超时秒数；超时按未就绪处理，但接口整体很快给出结论
DEFAULT_CHECK_TIMEOUT = 1.0

# 复用的守护线程池：探针在线程中执行，主线程用 future.result(timeout) 兜底
_executor: ThreadPoolExecutor | None = None

# dict 形态探针中，表示健康与否 / 状态文本 / 说明的常见键
_BOOL_KEYS = ("ok", "healthy", "ready")
_STATUS_KEYS = ("status", "state")
_DETAIL_KEYS = ("detail", "message", "reason", "error")
_UP_TOKENS = frozenset({"ok", "up", "ready", "available", "healthy", "pass", "passed"})
_DOWN_TOKENS = frozenset(
    {"down", "error", "fail", "failed", "failure", "unavailable", "unhealthy", "not_ready"}
)


def register_readiness_check(name: str, check: Callable[[], object]) -> None:
    """注册一个就绪依赖检查。"""
    _readiness_checks[name] = check


def liveness() -> dict[str, str]:
    """存活：进程在运行即通过，不检查依赖。"""
    return {"status": "alive"}


def _normalize_result(raw: object) -> tuple[bool, str]:
    """把探针的各种约定返回值归一为 (是否就绪, 说明)。"""
    if raw is None:
        return True, ""
    if isinstance(raw, bool):
        return raw, ("" if raw else "check returned false")
    if isinstance(raw, (tuple, list)) and len(raw) == 2 and isinstance(raw[0], bool):
        ok = bool(raw[0])
        detail = "" if raw[1] is None else str(raw[1])
        return ok, (detail if detail else ("" if ok else "check returned false"))
    if isinstance(raw, dict):
        ok: bool | None = None
        for key in _BOOL_KEYS:
            value = raw.get(key)
            if isinstance(value, bool):
                ok = value
                break
        if ok is None:
            for key in _STATUS_KEYS:
                value = raw.get(key)
                if isinstance(value, str):
                    token = value.strip().lower()
                    if token in _UP_TOKENS:
                        ok = True
                        break
                    if token in _DOWN_TOKENS:
                        ok = False
                        break
        detail = ""
        for key in _DETAIL_KEYS:
            value = raw.get(key)
            if value:
                detail = str(value)
                break
        if ok is None:
            # 无法识别的约定形态：显式暴露为失败，而不是默默当健康
            return False, f"unrecognized check result: {raw!r}"
        return ok, (detail if detail else ("" if ok else "check reported not ready"))
    # 其他形态（如裸字符串/数字）语义不明确，按失败暴露
    return False, f"unrecognized check result: {raw!r}"


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="readiness")
    return _executor


def readiness(timeout: float = DEFAULT_CHECK_TIMEOUT) -> tuple[int, dict[str, object]]:
    """就绪：所有依赖检查通过才就绪；返回 (http_status, body)。

    每个探针最多等待 ``timeout`` 秒；异常或超时只影响该检查项，
    不拖垮整个探针，接口整体耗时有上界。
    """
    results: dict[str, str] = {}
    ready = True
    for name, check in _readiness_checks.items():
        try:
            future = _get_executor().submit(check)
            try:
                raw = future.result(timeout=timeout)
            except TimeoutError:
                # future.result 超时与探针自己抛的 TimeoutError 都是内建
                # TimeoutError；用 future 是否完成区分，避免把依赖异常误判成超时
                if future.done():
                    raise
                future.cancel()
                ok, detail = False, f"timeout after {timeout:g}s"
            else:
                ok, detail = _normalize_result(raw)
        except Exception as exc:  # 依赖异常只影响该检查项，不拖垮整个探针
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        if ok:
            results[name] = "ok"
        else:
            results[name] = f"unavailable: {detail}" if detail else "unavailable"
            ready = False
    status = 200 if ready else 503
    return status, {"status": "ready" if ready else "not_ready", "checks": results}
