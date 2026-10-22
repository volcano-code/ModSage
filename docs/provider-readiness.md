# 模型接入、隔离验收与调用额度

v0.5 提供离线预检、显式真实调用验收、持久化调用记录和跨进程调用次数限制。未填写真实凭证时，目录、日报、规则理解、BM25、持久化推荐任务和页面恢复仍可运行。配置完整只表示字段合法；真实服务是否接受当前凭证、模型与请求协议，需要配置后执行验收。

本文命令从仓库根目录执行。凭证只进入根目录 `.env` 或服务端环境；不进入浏览器环境、URL、截图或 Git。不要把含凭证的 `docker compose config` 完整输出分享出去，校验语法使用 `docker compose config --quiet`。

## 1. 先运行零联网预检

```bash
.venv/bin/python -m modsage.cli init-db
.venv/bin/python -m modsage.cli preflight
.venv/bin/python -m modsage.cli usage --limit 10
```

Compose 对应命令：

```bash
docker compose exec -T api python -m modsage.cli preflight
docker compose exec -T recommendation-worker python -m modsage.cli preflight
docker compose exec -T api python -m modsage.cli usage --limit 10
```

`preflight` 不发网络请求，返回 `network_calls: 0`、`live_verified: false`。它检查：

- LLM、embeddings 和 Steam 的配置状态；LLM/embeddings 区分未配置、不完整、无效、字段完整。
- 真实目录数、含作者描述的样本数、待生成介绍数，以及当前向量索引覆盖。
- 推荐和刷新队列中的未完成任务，提示启用模型后它们可能使用付费服务。
- 总推荐预算是否容纳串行模型超时及处理余量。
- 自动刷新启用后，所选认证来源是否具备配置。
- 共享调用次数限额、剩余额度和总开关。

所有可选服务均未配置时，`status: ready` 代表本地功能可启动，不能据此声称模型已验证。空目录产生 `catalog_empty` 警告。填写了一部分模型配置或非法地址会使预检 `status: failed`、退出码为 1，报告不回显配置值。

## 2. 请求协议与配置

### 对话模型

```dotenv
MODSAGE_LLM_API_KEY=
MODSAGE_LLM_BASE_URL=
MODSAGE_LLM_MODEL=
MODSAGE_LLM_TOKEN_PARAMETER=max_tokens
MODSAGE_LLM_MAX_OUTPUT_TOKENS=900
MODSAGE_LLM_TEMPERATURE=0
MODSAGE_LLM_TIMEOUT_SECONDS=15
MODSAGE_RECOMMENDATION_TIMEOUT_SECONDS=25
```

必须使用当前账户确实有权限调用的模型。Base URL 是 API 根路径，例如提供方文档中的 `https://provider.example/v1`；程序追加 `/chat/completions`。不要在地址末尾再次写 `/chat/completions`，也不要填管理后台或网页聊天地址。

当前适配器使用 Bearer 鉴权、`messages`、单次非流式 Chat Completions 语义，从 `choices[0].message.content` 读取 JSON 对象。底层流式读取 HTTP 响应用于限制字节数，不等同于模型 token 流式输出。没有实现其他厂商的专有请求协议，也不自动转换成 Responses API。

| 参数 | 默认与范围 | 用途 |
| --- | --- | --- |
| `LLM_TOKEN_PARAMETER` | `max_tokens` 或 `max_completion_tokens` | 按提供方文档选择，单次请求只发送一个字段 |
| `LLM_MAX_OUTPUT_TOKENS` | 900；64–8192 | 单次生成的输出 token 参数上限 |
| `LLM_TEMPERATURE` | 0；0–2；空值代表省略 | 某些模型不接受该参数时显式留空 |
| `LLM_TIMEOUT_SECONDS` | 15；1–25 秒 | 单次 HTTP 响应预算 |
| `RECOMMENDATION_TIMEOUT_SECONDS` | 25；1–60 秒 | 整个推荐图的时间预算 |

代码默认 LLM Base URL 保留原有兼容值，但 `.env.example` 和 Compose 明确留空，要求操作者填入自己选择的提供方地址。只有 key、有效 Base URL 和模型三项完整才标记 `llm_configured=true`。

地址拒绝账号密码、查询字符串、片段、非法端口/主机与控制字符；不跟随重定向。支持显式 HTTP 兼容服务和本地联调，公网凭证应经 HTTPS 发送。key 与模型字段统一去除首尾空白。LLM 输入总预算为 128 KiB，HTTP 响应上限 256 KiB；结果仍需通过业务结构校验。`finish_reason=length` 被视为截断失败，不能把半份 JSON 当作有效结果。

若启用混合检索，15 秒 LLM + 10 秒 embedding 的最坏串行时间已经占满默认 25 秒；可把总预算设为 35 秒，预留图执行和数据库核验时间。预检对此给出警告，不会悄悄改写操作员配置。

### 向量服务

```dotenv
MODSAGE_EMBEDDING_API_KEY=
MODSAGE_EMBEDDING_BASE_URL=
MODSAGE_EMBEDDING_MODEL=
MODSAGE_EMBEDDING_RESPONSE_MODEL=
MODSAGE_EMBEDDING_DIMENSIONS=
MODSAGE_EMBEDDING_TIMEOUT_SECONDS=10
MODSAGE_RETRIEVAL_MODE=bm25
```

程序向 Base URL 追加 `/embeddings`，发送模型、文本数组和 `encoding_format: float`。可选 dimensions 只有明确填写才发送。返回值必须包含连续且唯一的索引、有限数值、非零向量和一致维度；响应上限 4 MiB。单批最多 16 条、每条最多 6000 字符，输出维度最多 4096。

默认严格核对返回模型与请求模型。如果网关使用请求别名而响应返回固定模型名，可显式填写 `EMBEDDING_RESPONSE_MODEL`。该配置变化会使旧索引不能在新身份下复用；不要通过忽略模型差异混用不同向量空间。

向量凭证独立配置；不会自动借用对话模型 key。即使是同一个服务，也需要确认它支持 embeddings。此阶段保持 BM25，先通过独立验收再决定是否回填及切换。

## 3. 显式真实验收

以下 `--live` 命令会把小样本发送到已配置的提供方。它们是配置 key 后由操作者主动执行的操作；应用启动、健康检查和 CI 不会替你执行。

```bash
# 不带 --live：返回 blocked 和非零退出码，不发请求。
.venv/bin/python -m modsage.cli provider-check --scope llm

# 真实检查：需要真实目录中至少一条具有作者描述的记录。
mkdir -p artifacts
.venv/bin/python -m modsage.cli provider-check --scope llm --live > artifacts/llm-canary.json
.venv/bin/python -m modsage.cli provider-check --scope embedding --live > artifacts/embedding-canary.json

# 可选：选择已经收录的 Workshop ID 与适合该样本的需求。
.venv/bin/python -m modsage.cli provider-check --scope llm --live --mod-id 2976080822 --query '推荐这个条目适合的玩法'

# Steam 专项检查不要求先有本地目录。
.venv/bin/python -m modsage.cli provider-check --scope steam --live > artifacts/steam-canary.json

# 全项检查：任一必要配置或数据条件缺失，会在联网前整体阻止。
.venv/bin/python -m modsage.cli provider-check --scope all --live > artifacts/provider-canary.json
```

示例 ID 只说明命令格式，不保证它当前可访问或在你的目录中。没有指定 ID 时，最多取按 ID 排列的 3 个有描述的真实条目；未指定 query 时使用首条标题。自行提供的需求必须适合这组小样本，否则“没有推荐候选”会让验收失败，不能误判成提供方凭证错误。

| Scope | 最大调用预算 | 成功条件 |
| --- | --- | --- |
| `llm` | 2 次 LLM | 推荐图实际 `engine=llm`、有候选；另生成 1 条中文介绍并通过结构和逐字来源引用校验 |
| `embedding` | 2 次 embeddings | 1 批最多 3 条临时向量，再执行 1 次查询；实际 `hybrid_rrf`、有效索引和非空候选均成立 |
| `steam` | 1 次认证发现操作 + 1 次详情操作；适配器重试合计最多 6 个 HTTP 尝试 | QueryFiles 返回 1 个 ID，并可核验为有效战锤 3 Workshop 条目 |
| `all` | 上述预算之和 | 每个所请求的检查阶段均成功 |

LLM 和 embeddings 适配器不自动重试付费请求。Steam 的总检查另有 45 秒上限，认证发现最多检查一页；这不是全站覆盖或分页完整性验收。

验收把源条目复制到独立内存数据库，运行真实推荐/生成/向量代码；不会消费已有队列，不改生产中文介绍、不回填生产向量、不发布推荐历史。**调用记录仍进入原应用数据库，和其他进程共享额度。** 因此 canary 不是免费绕过配额的旁路。

### 怎样读报告

- 只有所有请求阶段满足条件，才返回 `status: succeeded` 和退出码 0。
- 缺 key、配置不完整、缺目录、没有显式 `--live` 或不足以完成所选 scope 的剩余额度，返回 `blocked` / 退出码 1。
- 模型错误后回退规则、向量错误后回退 BM25、生成数为 0、仅有 HTTP 200、JSON 正确但业务结构不合法，都不能通过。
- 摘要报告包含来源 URL、生成文本和有限逐字引用，`manual_review_required: true`。逐字引用能校验出处，不能自动证明每个总结结论都被来源支持；需要逐条人工核验。
- `quality_benchmark_passed: false` 表示混合检索验收只证明通道被实际执行，不证明其质量优于 BM25。
- 缺少 provider usage 时保留 `null` 和不完整标记，不当成 0 token。合法缺失 usage 可完成协议验收，但不能声称成本已验证；负数、字符串、矛盾计数等非法 usage 使检查失败。

## 4. 共享额度与调用台账

```dotenv
MODSAGE_PROVIDER_CALLS_ENABLED=true
MODSAGE_PROVIDER_DAILY_CALL_LIMIT=200
MODSAGE_PROVIDER_MINUTE_CALL_LIMIT=30
```

次数额度合并计算 LLM 和 embeddings，覆盖同步推荐、持久化推荐 worker、中文介绍、刷新 worker、手动向量回填以及 canary。Steam 不进入这个付费模型调用次数台账；它的抓取预算仍由来源适配器和刷新任务约束。

每日窗口使用 UTC 日历日，等价于北京时间每天 08:00 重置；分钟窗口为滚动 60 秒，跨日仍然生效。任一限额设为 0 或总开关为 false 会阻止新模型调用。默认 200/日、30/分钟是小规模联调的工程限制，不是推荐的商业预算。

每次网络请求之前，数据库在一个短事务里取得准入写锁，核对次数并插入 `started` 记录。SQLite 写锁与 PostgreSQL 行锁在多个进程间协调，网络期间不持有事务。失败、超时、取消、进程崩溃后没有完成回执的请求都占用已预留次数，不返还，防止重复请求扩大潜在费用。数据库不可读写时拒绝新的模型请求；正常规则/BM25 回退仍然可用。

```bash
.venv/bin/python -m modsage.cli usage --limit 20
```

记录包括操作类型、预留与完成时间、延迟、HTTP 状态、安全错误码、提供方返回的模型/请求标识、结束原因和实际报告的 token 数。缺失 usage 和无完成回执分别计数。没有保存用户 prompt、完整作者原文、密钥、完整提供方 URL 或任意上游错误文本。标识需通过字符与长度检查，并过滤已知秘密；读取历史时也会检查当前秘密。

每条记录包含不对外输出的配置指纹。换 key、模型、地址或相关协议参数后，公开状态不会继承旧配置的成功记录。向量缓存自身不会因轮换 key 而重建，因为 key 不改变向量空间；两种身份的职责不同。

**次数限制不等于人民币/美元硬上限。** 不同模型和输入的价格不同，缺少 usage 也不能臆算成本。本项目报告事实 token 和调用次数，不内置可能过期的模型价格。真实上线时同时在提供方设置账户消费限额。

额度上限和开关来自各进程的启动配置。同一数据库的 API、CLI 和两个 worker 必须使用相同配置；修改 `.env` 后应重建全部相关服务。只改 API 环境不能改变已经运行的旧 worker。紧急暂停步骤见 [运行手册](operations.md)。

## 5. 公开页面与业务结果的分工

`GET /api/v1/health` 保留原字段，并追加 `providers.llm` / `providers.embedding`。每项只含配置状态、调用是否启用、当前配置最近一次调用的时间与安全结果，不公开模型名、URL、请求标识、额度数字或配置指纹。

页面中的“模型已配置 · 待核验”“最近模型调用成功”“调用已暂停”与实际推荐结果分开显示。`last_call=succeeded` 只表示最近响应满足客户端的 JSON/向量协议校验，不等于业务条件解析、摘要事实或本次推荐成功。当前任务仍以 `engine`、`retrieval.method` 和 warnings 为准。

## 6. 配置后推荐顺序

1. 暂停推荐/刷新 worker，运行 preflight 查看待处理任务；决定保留或取消已有请求后再启用真实模型。
2. 先保持 BM25，配置 LLM，执行 `provider-check --scope llm --live`；检查生成介绍与源引用。
3. 模型验收后，从 `enrich --limit 1` 开始正式写入介绍。generated=0 的批次不算生成效果验证。
4. 单独配置 embeddings，执行专项 canary。通过后运行 `embed --limit 3 --batch-size 3`，检查 `embedding-status`，再设 `RETRIEVAL_MODE=hybrid`。
5. Steam key 配置后做专项 canary，再执行有界认证发现；最后启用每日刷新。
6. 观察实际调用记录、延迟、回退和账户账单，再调整配额。多日稳定性和真实检索质量需要配置后的观察数据。

## 验证边界与依据

CI 的 `scripts/provider_contract_smoke.py` 使用 loopback HTTP fixture、隔离临时数据库和真实 CLI 子进程，实际走 4 次模拟 HTTP 请求并验证跨进程额度与目录不改写。它的输出显式为 `mode: loopback_http_fixture_only`，`external_provider_calls: 0`。它不能替代上述真实提供方验收。

配置转发使用 [Docker Compose 的变量插值语义](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)，temperature 使用 `${VAR-default}` 保留显式空值。HTTP 错误分类参考 [HTTPX 异常层次](https://www.python-httpx.org/exceptions/)。SQLite 并发与事务前提参见 [SQLAlchemy SQLite 方言说明](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)。Steam 认证检索边界见 [官方 QueryFiles 文档](https://partner.steamgames.com/doc/webapi/IPublishedFileService)。
