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

- 除默认敏感键（大小写不敏感、任意嵌套层级精确匹配，行为保持不变）：
  `password`、`token`、`authorization`、`secret`、`api_key`、`apikey` 外，
  **凡字段名表达密码/密钥/令牌/鉴权/凭证含义的一律掩码**，兼容各团队的命名习惯：
  - 任意前后缀与连字符、下划线、点、驼峰：`userPassword`、`db-pwd`、
    `login_pwd`、`oldPasswd`、`X-Auth-Token`、`accessToken`、`refresh_token`、
    `clientSecretValue`、`privateKeyPem`、`caller_credentials` 等；
  - 密钥类组合：`x-api-key`、`access_key_id`、`signing-key`、`security_key` 等
    （单独的 `key`/`primary_key` 不敏感，必须带 `api/access/secret/private/...`
    限定词，避免误伤普通业务键）；
  - 中文语义：字段名含“密码/口令/密钥/私钥/令牌/鉴权/凭证”即掩码；
  - 支持深层嵌套对象、数组，以及请求头常见的 `[name, value]` 二元组形态：
    头部名命中时只掩码其值；
  - 整词匹配而非子串：`keyboard`、`author`、`tokenize` 等普通词不会被误伤。
- 命中键的值替换为 `***`；非敏感结构原样保留；
- 脱敏过程自身抛错时**不抛出、不丢日志**：出错的那个子树以 `***` 占位，
  同条日志的其他字段照常输出；最外层兜底同样保证整条日志不丢；
- 嵌套深度超过 32 层（含循环引用）时截断为 `***`，防御异常结构。

## 指标与健康检查语义

- **指标**（`GET /metrics`，可用 `?caller_id=` 过滤）：按 caller 分桶聚合。
  除原有 `total` / `errors`（状态码 ≥ 400 计入）/ `latency_avg_ms` /
  `latency_max_ms` 外，新增：
  - `successes`：成功数，恒等于 `total - errors`；
  - `validation_errors` / `dependency_errors` / `unexpected_errors`：
    三类错误分别计数，三者之和恒等于 `errors`；
  - `slow_requests`：延迟达到阈值（默认 500ms，见
    `metrics.SLOW_REQUEST_THRESHOLD_MS`）即计入，成功与失败都算，
    记录时也可用 `is_slow=` 显式指定。
  错误类别由异常处理器（`AppError` / 请求校验）写入请求上下文，中间件读取；
  没有处理器归类的响应按状态码兜底（4xx→validation，5xx→dependency），
  未识别类别归入 unexpected，因此类别明细永不漏统、总和永远对得上。
  一次记录的全部字段在同一临界区内更新，高并发下计数不丢失、不重复；
  不同 caller 互不影响；单次记录开销约 0.5µs，不明显拖慢正常请求。
- **存活**（`GET /health/live`）：进程在运行即 200，不检查任何依赖。
- **就绪**（`GET /health/ready`）：所有通过 `register_readiness_check` 注册的依赖
  检查通过才返回 200 `ready`；任一信号表示不可用即返回 503 `not_ready`，
  并在 `checks` 中逐项列出 `ok` / `unavailable: 说明`。兼容探针的多种返回约定：
  返回 `None`（旧约定）、抛异常（旧约定，记录异常类型与消息）、返回布尔、
  `(ok, detail)` 二元组/列表、或含
  `ok`/`healthy`/`ready`/`status`/`state` 与
  `detail`/`message`/`reason`/`error` 键的 dict（如
  `{"status": "UP"}`、`{"ok": false, "reason": "queue full"}`）；
  无法识别的返回形态显式判失败，不会默默当作健康。
  每个探针在线程中执行并受**单探针超时**保护（默认 1s，
  `health.DEFAULT_CHECK_TIMEOUT`，可在调用 `readiness(timeout=...)` 时覆盖）：
  探针卡住不会无限期挂住就绪接口，超时按失败计
  （`unavailable: timeout after ...`），接口整体耗时有上界。
  单个依赖检查异常或超时只影响该项，不拖垮整个探针。

## 本地验证

```bash
uv sync                 # 安装依赖
uv run pytest -q -s     # 运行测试（-s 可看到每个用例打印的输入与判定依据）
uv run uvicorn main:app --reload   # 启动服务
```

测试覆盖（共 108 个用例，历史用例全部保留）：

- `tests/test_request_id.py`：标识透传、非法替换、并发隔离、错误关联（历史）；
- `tests/test_errors_logging.py`：错误分类、原因链、标准脱敏键、日志不丢字段（历史）；
- `tests/test_sensitive_naming.py`：语义化命名（密码/密钥/令牌/鉴权/凭证、
  各种分隔与驼峰、中英文）、深层嵌套与请求头形态、普通字段不误伤、
  脱敏失败不丢日志、循环引用安全（新增）；
- `tests/test_metrics_health.py`：调用方隔离、并发计数、存活/就绪基础语义（历史）；
- `tests/test_probe_contracts.py`：探针各返回约定归一、逐项结果、
  失败状态码、超时兜底与整体快速结论（新增）；
- `tests/test_metrics_categories.py`：三类错误计数与总和对账、慢请求、
  高并发不丢不重、HTTP 端到端归类（新增）。

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
# 慢请求（延迟 >= 500ms 计入 slow_requests）后查看分类指标
curl -s 'localhost:8000/echo?delay=0.6' -H 'X-Caller-ID: alice' > /dev/null
curl 'localhost:8000/metrics?caller_id=alice'
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
    context.py                # request_id / caller_id / error_category 的 contextvars
    middleware.py             # 标识透传、上下文隔离、指标记录、未预期异常兜底
    errors.py                 # 错误分类与 AppError 体系
    logging.py                # JSON 结构化日志、语义化脱敏、异常原因链
    metrics.py                # 调用方隔离、错误分类别/慢请求计数的线程安全注册表
    health.py                 # 存活/就绪分离、多约定探针归一与超时兜底
tests/                        # 标识、错误分类、脱敏、指标、探针约定等测试
```
