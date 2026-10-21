# v0.3 检索配置与运行边界

默认推荐使用本地 BM25，不需要模型 key。可选语义通道只有在明确开启、配置有效服务且已有当前索引时才发起需求向量请求。需求解析、检索方法与中文介绍生成是三个独立能力，界面和 API 分别记录其状态。

## 默认流程

1. 通过规则或配置的模型解析需求，提取派系、排除项、内容要求和语言要求。
2. 扫描有上限的真实目录，先应用硬约束。未确认的游戏内中文不能满足“必须汉化”；“不要大修”也不会因为语义相似而被放宽。
3. 对合格条目的标题、标签、作者介绍前段执行 BM25，字段权重为 3、2、1；采用英文归一化、中文双字片段和派系/主题别名。
4. 若启用混合检索，读取当前 namespace 内且来源指纹一致的向量，对需求调用一次 embeddings 服务，执行精确余弦扫描，再用 RRF 融合两个通道的名次。
5. 重新读取来源并核对指纹和硬约束，持久化推荐结果及匹配证据。

BM25 参数为 `k1=1.2`、`b=0.75`，按字段分别进行长度归一化并加权求和，不称为 BM25F。RRF 使用 `k=60`、两通道等权。各通道的原始分数刻度不同，因此融合使用名次；最终分数不是匹配概率或兼容性置信度。方法背景可见 [Robertson 与 Zaragoza 的 BM25 综述](https://doi.org/10.1561/1500000019) 和 [Cormack 等人的 RRF 论文](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)，具体实现以 `retrieval.py` 为准。

推荐和向量输入仅使用来源字段，避免生成摘要成为新的事实证据。目录搜索额外包含已显示的中文摘要，以保留原有搜索契约。

## 开启可选语义通道

先同步真实目录，然后在根目录 `.env` 或进程环境配置：

```dotenv
MODSAGE_RETRIEVAL_MODE=hybrid
MODSAGE_EMBEDDING_BASE_URL=https://your-provider.example/v1
MODSAGE_EMBEDDING_MODEL=your-accessible-embedding-model
MODSAGE_EMBEDDING_API_KEY=your-private-key
MODSAGE_EMBEDDING_DIMENSIONS=
```

`BASE_URL` 必须提供兼容 `POST /embeddings` 的接口，返回带 `index` 与浮点 `embedding` 的 `data` 数组。API 不跟随重定向，拒绝无效数值、零向量、混合维度、重复索引及错误的模型标识；这里只承诺该响应契约，不承诺任意供应商都兼容。可选固定维度留空表示不发送 `dimensions` 参数；填写前需要核对所用服务是否支持。

```bash
.venv/bin/python -m modsage.cli embedding-status
.venv/bin/python -m modsage.cli embed --limit 20 --batch-size 8
.venv/bin/python -m modsage.cli embedding-status
```

修改服务环境后重启 API。`embed` 明确发起模型请求，将标题、标签和作者介绍前段发送到所配置的提供方；混合推荐发送需求文本。凭证只保留在服务端。聊天模型 key 与 embedding key 分开配置，默认 BM25 模式不会因为存在 embedding key 而自动调用服务。

回填不是后台任务：一次执行只处理指定数量，完成后退出。当前有效记录被跳过，可以继续重复执行来完成剩余条目。**同一数据库一次运行一个 `embed` 命令**，尤其 SQLite 不具备回填分布式租约，并发执行可能有一个进程因唯一键竞争失败。每日刷新 worker 的租约机制不覆盖手工回填。

## 配置与预算

以下变量均加 `MODSAGE_` 前缀：

| 变量 | 默认值 / 范围 | 作用 |
| --- | --- | --- |
| `RETRIEVAL_MODE` | `bm25`；或 `hybrid` | 是否请求可选语义通道 |
| `RETRIEVAL_CATALOG_LIMIT` | 5000；100–10000 | 推荐与索引按 ID 排序后的最大扫描条目数 |
| `RETRIEVAL_CANDIDATE_LIMIT` | 50；10–200 | 每个召回通道保留的最大候选数 |
| `EMBEDDING_API_KEY` | 空 | 独立提供方凭证 |
| `EMBEDDING_BASE_URL` | 空 | 提供方基础地址，不含 `/embeddings` |
| `EMBEDDING_MODEL` | 空 | 明确的模型标识 |
| `EMBEDDING_DIMENSIONS` | 未指定；1–4096 | 可选固定维度 |
| `EMBEDDING_TIMEOUT_SECONDS` | 10；1–20 秒 | 每批回填或需求向量请求的总超时 |
| `EMBEDDING_MIN_SIMILARITY` | 0.35；0–1 | 余弦候选阈值；默认值尚未经过真实提供方调优 |

单次回填 `--limit` 为 1–200，单批 `--batch-size` 为 1–16；每项输入最多 6000 字符，响应最多 4 MiB，向量最多 4096 维。达到预算或提供方出错时停止本次后续批次，报告 `partial` 或 `failed`，已提交的有效批次保留。推荐另受整个请求的超时预算约束。

这些预算不等于费用估计。真实成本取决于提供方和实际文本，本轮没有可用模型凭证，未测得真实账单或语义检索延迟。`RETRIEVAL_CATALOG_LIMIT` 只约束推荐与向量索引扫描，目录 `GET /mods?q=...` 仍在应用层扫描完整目录，不能声称为全站搜索规模提供保障。

## 索引新鲜度与命名空间

`mod_embeddings` 存储 `mod_id`、`namespace`、`document_hash`、模型、维度、JSON 向量及生成时间。复合主键为 `(mod_id, namespace)`。

- namespace 包含服务地址、模型、请求维度与文档格式版本，不包含秘密 key。
- document fingerprint 包含源内容哈希与实际送入向量模型的文本。因此即使源哈希字段未被刷新，正文变化仍会使旧向量失效。
- 网络等待期间不持有数据库事务。回填返回后重读源记录，PostgreSQL 对源行加锁；来源已变更时跳过旧结果，并提示再次回填。
- 推荐在向量请求后再次检查索引与来源，并在返回前再核对候选，避免用已过期来源解释推荐。

更换模型、地址、固定维度或文档格式会产生新 namespace，需要重新回填。旧 namespace 不会自动复用，也不会自动清理；清理命令和正式 schema migration 仍待实现。仅轮换 key 不改变 namespace。若提供方在同一个模型名称下静默更换权重或输出语义，当前实现无法自动识别，需要使用新的模型标识或明确重建策略。

`embedding-status` 返回 `catalog_total`、`scanned`、`indexed`、`stale`、`missing` 和扫描上限。`indexed` 指当前 namespace 中通过校验的记录；其他 namespace 不算覆盖。状态查询不调用提供方，所以 `configured=true` 也不证明服务可达。

## 回退与前端解释

| 状态 | 实际结果 |
| --- | --- |
| 默认 BM25 | 不读取索引、不调用向量服务；`method=bm25` |
| 请求 hybrid 但未配置服务 | 返回 BM25 与未配置提示 |
| 没有当前有效索引 | 不发起需求向量请求，返回 BM25 并提示回填 |
| 向量超时、错误响应、维度不匹配 | 保留词法候选，明确回退 BM25 |
| 仅部分合格条目有当前向量 | 执行 hybrid，未索引条目仍可经 BM25 召回，显示覆盖不足提示 |
| 没有满足硬约束的条目 | 返回空结果与原因，不自动放宽用户条件 |

`engine=rules|llm` 只说明需求解析；`retrieval.method=bm25|hybrid_rrf` 说明本次实际执行；`requested_method` 保留所请求的模式。每项结果包含匹配词、词法分数、可选余弦分数与召回通道。界面可展开这些信息，历史运行缺少新字段时仍可展示。

## 当前验证边界

纯排序、提供方响应、超时、预算、来源变更、故障回退与索引幂等有自动化测试。PostgreSQL smoke 使用隔离数据库和假向量提供方，验证真实 SQL 写读、命名空间与约束；假向量只用于测试，不计入实际语义质量。

[冻结评测](retrieval-evaluation.md) 比较旧关键词与 BM25，尚未得出实际 embeddings 或混合检索优于基线的结论。当前向量通道是有界精确扫描，不是 pgvector、ANN 索引或神经重排。下一阶段应先用实际可用提供方跑相同评测并扩展独立标注，再决定索引、阈值和重排方案。
