# API 可观测性（请求级追踪 / 结构化日志 / 指标与健康检查）

在 FastAPI 服务上实现的请求级可观测性能力，保证并发请求、异常链路与调用方标识异常等场景下，
追踪、日志与指标的结论一致且可解释。

## 观测模型

```
请求 → ObservabilityMiddleware（纯 ASGI）
        ├─ 解析/生成 X-Request-ID，解析 X-Caller-ID（contextvars 隔离）
        ├─ 路由处理（异常由分类处理器或中间件兜底）
        ├─ 响应头回写 X-Request-ID
        └─ 按 caller 维度记录指标（计数 + 耗时）
```

- **request_id**：调用方提供合法标识则沿用；缺失或非法时生成 32 位十六进制 UUID。
  合法字符集为 `[A-Za-z0-9._-]`，长度 1–64。响应头与错误响应体都携带同一标识，
  日志自动注入，可用它把响应与日志对应到同一次请求。
- **caller_id**：取自 `X-Caller-ID`，校验规则同上；缺失或非法时归一为 `unknown`，行为确定。
- **并发隔离**：标识存于 `contextvars`，每个请求在独立的异步上下文中执行，
  中间件在 `finally` 中复位 token，请求之间不串扰。
- **未预期异常**：在中间件内、上下文仍存活时捕获并记录日志，再返回不含内部细节的 500，
  因此异常日志与错误响应都带有正确的 request_id。

## 日志字段与脱敏规则

日志为单行 JSON（`app.observability.logging.StructuredFormatter`）：

| 字段 | 说明 |
| --- | --- |
| `level` / `logger` / `message` | 级别、logger 名、消息 |
| `request_id` / `caller_id` | 自动从上下文注入 |
| `fields` | 业务结构化字段，输出前递归脱敏 |
| `exception` | 异常原因链列表 `[{type, message}, ...]`，链尾为根因 |

**错误分类**（`error.category`，日志与响应一致）：

- `validation`：请求校验失败（422），含 FastAPI 参数校验与 `ValidationFailedError`；
- `dependency`：依赖不可用（503），`DependencyUnavailableError`，原因链保留下游根因；
- `unexpected`：未预期异常（500），对外只返回 `internal error`，不泄露内部细节。

**脱敏规则**：

- 默认敏感键（大小写不敏感，匹配任意嵌套层级的 dict 键）：
  `password`、`token`、`authorization`、`secret`、`api_key`、`apikey`；
- 命中键的值替换为 `***`；非敏感结构原样保留；
- 脱敏过程自身抛错时**不抛出、不丢日志**，整体以 `***` 占位；
- 嵌套深度超过 32 层时截断为 `***`，防御异常结构。

## 指标与健康检查语义

- **指标**（`GET /metrics`，可用 `?caller_id=` 过滤）：按 caller 分桶聚合
  `total` / `errors`（状态码 ≥ 400 计入）/ `latency_avg_ms` / `latency_max_ms`。
  注册表用锁保护，高并发下计数不丢失、不重复；不同 caller 互不影响。
  观测开销仅为一次字典更新与两次 contextvar 操作，不明显拖慢正常请求。
- **存活**（`GET /health/live`）：进程在运行即 200，不检查任何依赖。
- **就绪**（`GET /health/ready`）：所有通过 `register_readiness_check` 注册的依赖
  检查通过才返回 200 `ready`；任一失败返回 503 `not_ready`，并在 `checks` 中逐项列出结果。
  单个依赖检查抛异常只影响该项，不拖垮整个探针。

## 本地验证

```bash
uv sync                 # 安装依赖
uv run pytest -q -s     # 运行测试（-s 可看到每个用例打印的输入与判定依据）
uv run uvicorn main:app --reload   # 启动服务
```

手动验证示例：

```bash
# 标识透传
curl -i -H 'X-Request-ID: order-001' -H 'X-Caller-ID: alice' localhost:8000/echo
# 非法标识被替换为生成的 UUID
curl -i -H 'X-Request-ID: bad id!' localhost:8000/echo
# 三类错误分类
curl -i localhost:8000/boom/validation
curl -i localhost:8000/boom/dependency
curl -i localhost:8000/boom/unexpected
# 指标（按调用方过滤）与健康检查
curl 'localhost:8000/metrics?caller_id=alice'
curl -i localhost:8000/health/live
curl -i localhost:8000/health/ready
```

## 目录结构

```
app/
  main.py                     # 应用装配：中间件、异常处理、路由
  observability/
    context.py                # request_id / caller_id 的 contextvars
    middleware.py             # 标识透传、上下文隔离、指标记录、未预期异常兜底
    errors.py                 # 错误分类与 AppError 体系
    logging.py                # JSON 结构化日志、脱敏、异常原因链
    metrics.py                # 按调用方隔离的线程安全指标注册表
    health.py                 # 存活/就绪分离的健康检查
tests/                        # 标识隔离、错误分类、脱敏边界、指标与健康测试
```
