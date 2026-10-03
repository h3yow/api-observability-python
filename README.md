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
  这六个标准命名的行为与历史版本完全一致。
- **语义化识别**（多团队自由命名同样命中，普通业务字段不误伤）：
  - 大小写不敏感，名字前后可带任意缀词，分隔符支持连字符、下划线、点、空格、
    驼峰（如 `userPassword`、`X-Auth-Token`、`client.secret.value`）以及全小写
    黏连写法（如 `dbpassword`、`mysecretkey`）；
  - 语义词根覆盖密码（`password/passwd/pwd/passphrase`）、密钥
    （`secret/private key/access key/api key`）、令牌（`token/access token/refresh token`）、
    鉴权（`auth/authorization/credential(s)`）；支持复数；
  - 中文语义子串：`密码`、`口令`、`密钥`、`令牌`、`鉴权`、`凭证`、`凭据`；
  - 裸 `key`、`primary key` 等非敏感词不会被掩码；`tokenization` 这类仅含词根子串的
    普通字段也不会误伤；
  - 对嵌套对象与数组递归生效；请求头形态的二元组/二元数组
    `(name, value)`（值为字符串）按头部名掩码其值，dict 形式的头部同样生效。
- 命中键的值替换为 `***`；非敏感结构原样保留；
- 脱敏过程自身抛错时**不抛出、不丢日志**，整体以 `***` 占位；
- 嵌套深度超过 32 层时截断为 `***`，防御异常结构。
- 可用 `app.observability.logging.is_sensitive_name(name)` 单独判定字段名是否敏感。

## 指标与健康检查语义

- **指标**（`GET /metrics`，可用 `?caller_id=` 过滤）：按 caller 分桶聚合
  `total` / `errors`（状态码 ≥ 400 计入）/ `validation_errors` /
  `dependency_errors` / `unexpected_errors` / `slow` /
  `latency_avg_ms` / `latency_max_ms`。
  - 错误按类别拆分：校验失败（4xx）、依赖不可用（503）、未预期异常（其余 5xx）；
    类别由错误处理器显式写入、中间件按状态码兜底，保证
    **三类计数之和恒等于 `errors`**，`total = 成功 + errors`；
  - 慢请求单独计入 `slow`（与成败正交），默认阈值 500ms，可在构造
    `MetricsRegistry(slow_threshold_ms=...)` 时调整，也可在 `record(..., slow=True)`
    显式标记；
  - 注册表用锁保护，高并发下计数不丢失、不重复；不同 caller 互不影响。
    观测开销仅为一次带锁字典更新与两次 contextvar 操作，不明显拖慢正常请求。
  - `record()` 同时保留旧的 `is_error=` 调用方式，旧调用方无需改动。
- **存活**（`GET /health/live`）：进程在运行即 200，不检查任何依赖。
- **就绪**（`GET /health/ready`）：所有通过 `register_readiness_check` 注册的依赖
  检查通过才返回 200 `ready`；任一失败返回 503 `not_ready`，并在 `checks` 中逐项列出结果。
  - **探针返回约定兼容**：抛异常、`None`/`True`/`False`、
    `(bool[, 说明])`、纯说明字符串（`ok/up/ready/healthy` 或空串视为健康，
    其他文本作为不可用原因）、`{"ok"/"healthy"/"status": ..., "detail"/"message": ...}`
    均可识别；旧版“返回 None 正常、抛异常未就绪”的探针无需改动；
  - **超时兜底**：`register_readiness_check(name, check, timeout=2.0)` 可给每个探针
    单独设置超时；探针在守护线程中**并发**执行，卡住的探针按失败计
    （`unavailable: timeout after Ns`），接口总耗时只取决于最慢的一项，
    不会被单个依赖无限期挂住；单个探针异常只影响该项，不拖垮整个探针。

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

测试覆盖：新增 `tests/test_masking_semantic.py`（语义脱敏、深层嵌套/请求头形态、
普通字段不误伤、脱敏失败兜底）、`tests/test_health_probes.py`（探针多形态、逐项
结论、超时兜底与并发）、`tests/test_metrics_categories.py`（错误分类、慢请求、
高并发对账、调用方隔离）；运行后在输出中可看到每条用例打印的输入与判定依据。

## 目录结构

```
app/
  main.py                     # 应用装配：中间件、异常处理、路由
  observability/
    context.py                # request_id / caller_id 的 contextvars
    middleware.py             # 标识透传、上下文隔离、指标记录、错误类别贯通
    errors.py                 # 错误分类与 AppError 体系
    logging.py                # JSON 结构化日志、语义化脱敏、异常原因链
    metrics.py                # 按调用方隔离、带错误分类与慢请求的线程安全指标
    health.py                 # 存活/就绪分离、探针多形态兼容与并发超时
tests/                        # 标识隔离、错误分类、脱敏（含语义化）、
                              # 指标（含分类/慢请求）与健康（含超时）测试
```
