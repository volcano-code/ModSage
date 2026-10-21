# v0.4 持久化推荐任务与实时进度

推荐页现在使用数据库任务队列。提交后立即取得任务标识，后台 worker 执行实际的 LangGraph 推荐流程；页面接收已经提交到数据库的阶段事件。关闭页面不删除任务，刷新页面可恢复当前浏览器保存的任务记录。

## 任务与执行边界

| 状态 | 含义 | 可执行操作 |
| --- | --- | --- |
| `queued` | 已持久化，等待 worker 领取 | 取消、恢复查看 |
| `running` | worker 在有效租约内执行 | 取消、恢复查看 |
| `completed` | 结果和完成事件已在同一事务中提交 | 查看结果 |
| `failed` | 执行失败或尝试预算耗尽 | 预算未耗尽时重试 |
| `cancelled` | 任务已取消，旧执行者不能提交完成结果 | 查看记录；需要时重新提交新需求 |

实际阶段为理解需求（`understand`）、检索筛选（`retrieve`）与来源核对（`verify`）。阶段事件在相应图节点开始时产生，不使用模拟百分比或人为延迟。规则/BM25 的小目录执行可能很快，界面可以直接收到完成结果。

每次领取消耗一次尝试，包括租约超时后的接管与用户重试。默认最多 3 次。普通执行失败保留失败状态，由用户选择是否重试；worker 退出或租约过期时，可由后续 worker 在剩余预算内恢复。任务恢复会重新执行有界推荐图，**没有实现 LangGraph 节点检查点恢复**。中断前已经发出的外部模型调用可能重复，因此不承诺外部调用恰好一次。

取消会立即使旧租约失效；活跃进程通过阶段检查或心跳发现取消并停止本地执行。已经发送给提供方的请求可能已经产生费用，取消不能撤回已经发生的外部工作。迟到结果不会覆盖已取消状态。

## 默认本地启动与独立 worker

快速启动命令不变：

```bash
.venv/bin/python -m uvicorn modsage.main:app --host 127.0.0.1 --port 8000
npm --prefix apps/web run dev
```

API 默认启动一个内嵌推荐 worker，方便本地运行。任务和事件仍在数据库中，不依赖 API 进程的内存保存。进程正常关闭时停止执行并释放可恢复任务；强制退出后通过租约到期接管。

上述立即释放路径适用于 API 的异步关闭流程。独立 CLI 暂未将 `SIGTERM` 转换为异步取消；容器停止或进程被终止后，其他 worker 依靠租约到期接管，不能承诺立即重新排队。

需要独立进程时，在 API 环境设置 `MODSAGE_RECOMMENDATION_WORKER_ENABLED=false`，再启动：

```bash
.venv/bin/python -m modsage.cli recommendation-worker
# 仅处理最多一个可领取任务；空队列正常退出。
.venv/bin/python -m modsage.cli recommendation-worker --once
```

Compose 默认启动独立的 `recommendation-worker` 服务，API 中的内嵌 worker 关闭。原来的 `worker` 服务仍负责每日目录刷新，继续使用 `automation` profile；两者职责不同。

一个推荐 worker 进程同时执行一个任务，可以运行多个进程共享数据库。PostgreSQL 采用行锁与 `SKIP LOCKED` 领取任务；SQLite 使用条件更新进行原子竞争。所有状态提交检查租约 token 和有效期，过期执行者即使恢复运行，也不能更新任务或发布结果。

## 任务身份、重复提交与隐私

浏览器在提交前生成独立的随机访问凭证和幂等标识，并先保存到本机记录中。任务接口通过 `X-Recommendation-Token` 请求头验证访问权；凭证不会进入 URL，也不会被返回为任务字段。数据库只保存凭证的 SHA-256 摘要。

`Idempotency-Key` 在同一访问凭证范围内唯一。同一个凭证、幂等标识和请求内容重复提交，返回已有任务；内容不同返回 409。这样即使服务器已接受任务、浏览器却丢失响应，恢复提交也不会再创建一份任务。

如果恢复提交时遇到 429 限流，浏览器仍会保留原凭证和幂等标识，因为限流可能发生在服务端查找已有任务之前。稍后继续恢复原请求即可核对结果。参数错误与数据库故障响应经过脱敏，不回显用户需求或访问凭证。

这是匿名任务的能力凭证，不是账号系统。本机记录上限为 20 条，保存在 `modsage:recommendation-jobs:v1`。清理浏览器数据或丢失凭证后，无法恢复该任务；其他设备不会自动同步。浏览器禁止本地存储时，当前页面仍可使用任务，但刷新恢复受到限制。记录和凭证不用于外部分析或日志。

新任务的结果只从受凭证保护的任务接口读取。即使结果中的 `run_id` 与任务 ID 相同，旧公开 `/runs/{id}` 接口也不能读取新任务结果。旧同步推荐及其历史运行接口保留兼容，不自动迁移为新任务。

## API 与 SSE 协议

| 接口 | 用途 |
| --- | --- |
| `POST /api/v1/recommendation-jobs` | 提交 `{query,limit}`，返回 202 与任务快照 |
| `GET /api/v1/recommendation-jobs/{id}` | 获取当前任务状态及可用结果 |
| `GET /api/v1/recommendation-jobs/{id}/events` | 从指定游标接收已提交事件，随后等待新事件 |
| `POST /api/v1/recommendation-jobs/{id}/cancel` | 取消排队或执行中的任务 |
| `POST /api/v1/recommendation-jobs/{id}/retry` | 在剩余预算内重新排队失败任务 |

所有新任务接口需要 `X-Recommendation-Token`；创建接口另需要 `Idempotency-Key`。两者为 32–128 位 URL-safe 字符，前端凭证使用密码学随机值。访问其他凭证的任务返回 404，不暴露任务是否存在。前端代理只转发明确允许的任务路径和请求头，管理接口继续不对外开放。

快照包括 `id`、`query`、`limit`、`status`、`stage`、`attempts`、`max_attempts`、创建/开始/完成时间、`last_event_id`、安全的 `error_code` 与可选 `result`。完成结果保留既有 `engine`、`constraints`、`items`、`warnings`、`retrieval` 结构。

每个 SSE 事件形如：

```text
id: 3
event: progress
data: {"id":"…","status":"running","stage":"understand","last_event_id":3,…}

```

事件 ID 是每个任务从 1 开始递增的整数。状态更新、序号增加和对应快照事件在同一事务中提交，服务端只发送已提交记录。客户端使用 `Last-Event-ID` 重连，也可使用 `after` 查询参数；同时存在时取较大值。只重放严格大于游标的事件，超前游标返回 409，非法游标返回 422。格式遵循 [HTML SSE 规范](https://html.spec.whatwg.org/multipage/server-sent-events.html)。

服务端发送心跳注释，连接默认最多保持 25 秒后由客户端续接。终态及其事件发送完毕后结束连接；历史失败事件不会让已经重试的任务流提前结束。前端使用带凭证请求头的 fetch 流读取，处理 UTF-8 分段、CRLF、多行数据、注释和重复事件，并在页面离开时中止当前连接。断开进度连接不会取消后台任务。

Next.js 代理直接转发流，不先读取为 JSON 或完整缓冲；响应使用 `no-store, no-transform` 与 `X-Accel-Buffering: no`。部署到其他反向代理时也需要允许流式响应并关闭该路径的响应缓冲。

## 配置和容量

变量均加 `MODSAGE_` 前缀：

| 配置 | 默认值 | 范围 / 语义 |
| --- | ---: | --- |
| `RECOMMENDATION_WORKER_ENABLED` | true | API 是否运行内嵌 worker；Compose 固定关闭并使用独立服务 |
| `RECOMMENDATION_POLL_SECONDS` | 0.5 | 0.05–10 秒 |
| `RECOMMENDATION_LEASE_SECONDS` | 30 | 5–120 秒；执行中续租 |
| `RECOMMENDATION_MAX_ATTEMPTS` | 3 | 1–5；创建任务时固定 |
| `RECOMMENDATION_QUEUE_LIMIT` | 100 | 1–1000；数据库内 queued/running 总量上限 |
| `RECOMMENDATION_SSE_SECONDS` | 25 | 1–30 秒；每次进度连接上限 |
| `RECOMMENDATION_STREAM_LIMIT` | 32 | 1–200；每个 API 进程的并发进度连接上限 |

队列容量通过数据库中的短事务锁协调，重复提交先查已有任务，不额外占用容量。新建和手动重试受现有进程内请求频率限制。公网部署仍需入口限流；进程内限制不是跨节点配额。任务执行继续受推荐整体超时及模型/向量请求预算约束。

## 验证与尚未实现的部分

后端测试检查并发提交/领取、队列容量、来源核对、访问凭证、超时、取消竞态、旧执行者拒绝、尝试耗尽及 SSE 重放。PostgreSQL smoke 使用真实独立数据库连接验证竞争和恢复。容器 smoke 通过前端代理提交任务，等待独立 worker 完成，再检查 SSE、游标续接和保护边界；模型凭证必须关闭。

浏览器验收同时覆盖真实 API 完成流程、刷新恢复和本机记录；需要稳定停留在排队/失败/断线状态的交互测试使用明确的路由 fixture，不能当作真实外部模型或多日运行证据。

当前不包含账号同步、服务器端全量历史列表、任务/事件自动清理、LangGraph 节点检查点、Outbox 通知或跨机房容灾。执行记录与事件会持久保存，正式公开部署前需补充保留与删除策略。实际验收结果以 [验证记录](verification.md) 和对应提交的 CI 为准。
