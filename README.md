# API 请求级可观测性

在 FastAPI 服务上实现请求级可观测性：并发请求、异常链路与调用方标识异常等场景下，
追踪、日志与指标的结论一致且可解释。

## 观测模型

每次请求经过统一的可观测性中间件（`app/main.py`）：

```
请求 ──> 解析/生成 request id ──> 解析调用方标识 ──> 业务处理 ──> 访问日志 + 指标记录
           (ContextVar 隔离)        (非法归 anonymous)                (按调用方聚合)
```

- **请求标识（request id）**：调用方经 `X-Request-ID` 提供的合法标识被原样沿用；
  缺失或非法时确定性地生成 UUID4。标识存入 `ContextVar` 并回写响应头，
  并发请求之间互不串扰；错误响应（含最外层 500）都带同一标识，可与日志精确对应。
- **调用方标识（caller id）**：经 `X-Caller-ID` 提供，缺失或非法时确定性归入
  `anonymous`，作为指标聚合的维度。
- **三类错误**：校验失败（`validation_error`, 4xx）、依赖不可用
  （`dependency_unavailable`, 503）、未预期异常（`internal_error`, 500）。
  异常通过 `cause=` 显式链接原因，日志输出完整原因链；对外响应只含
  `category / message / request_id`，不泄露内部实现细节。

## 日志字段与脱敏规则

单行 JSON 结构化日志（`app/logging_config.py`）：

| 字段 | 说明 |
| --- | --- |
| `ts` / `level` / `logger` / `msg` | 时间戳（UTC ISO8601）、级别、日志器名、消息 |
| `request_id` | 当前请求标识（来自 ContextVar 或显式传入） |
| `fields` | 业务字段（访问日志含 `method/path/caller/status/latency_ms`） |
| `exc_chain` | 异常原因链，自外向内（仅错误日志） |

脱敏规则（`app/masking.py`）：

- 敏感键名（`password`、`token`、`secret`、`api_key`、`authorization`、`cookie` 等，
  大小写不敏感、支持子串匹配）的值整体替换为 `***`；
- 字符串中内嵌的 `key=value` / `key: value` 敏感对仅脱敏值部分；
- 嵌套 dict / list / tuple 递归处理；
- 脱敏或序列化自身失败时降级为安全占位/纯文本行，**日志绝不因此丢失**。

## 指标与健康检查语义

- **指标**（`GET /metrics`）：按调用方维度隔离聚合 `total`、
  `client_errors`(4xx)、`server_errors`(5xx)、`latency_avg_ms`、`latency_max_ms`。
  单把锁保护计数，高并发下不丢失、不重复；记录路径为 O(1) 内存操作，
  对正常请求的开销可忽略。
- **存活**（`GET /health/live`）：进程可响应即存活，不依赖外部资源。
- **就绪**（`GET /health/ready`）：聚合所有已注册依赖检查，任一失败（含检查抛异常）
  则整体返回 503 并逐项列出结果；不就绪不影响存活判定。

## 本地验证

```bash
# 运行测试（覆盖并发标识隔离、异常分类、脱敏边界、指标隔离、健康语义）
uv run pytest tests/ -v

# 启动服务
uv run uvicorn main:app --reload

# 手动验证
curl -i -H 'X-Request-ID: my-req-1' -H 'X-Caller-ID: shop' http://127.0.0.1:8000/
curl -i http://127.0.0.1:8000/demo/dependency   # 503 + dependency_unavailable
curl -i http://127.0.0.1:8000/demo/unexpected   # 500 + internal_error（不泄露细节）
curl -s http://127.0.0.1:8000/metrics           # 按调用方隔离的指标
curl -i http://127.0.0.1:8000/health/ready      # 就绪探针
```

## 目录结构

```
app/
  request_id.py      # 标识生成/校验/透传（ContextVar）
  masking.py         # 敏感字段脱敏（失败降级）
  errors.py          # 错误分类与原因链
  logging_config.py  # 单行 JSON 结构化日志
  metrics.py         # 按调用方隔离的线程安全指标
  health.py          # 存活/就绪探针
  routes.py          # 演示与验证路由
  main.py            # 中间件 + 异常处理器装配
tests/               # 并发隔离、异常分类、脱敏边界、指标与健康测试
```
