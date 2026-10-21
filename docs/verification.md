# v0.3 交付与验证记录

验证日期：2026-10-21。本轮交付真实官方游戏画面、BM25 检索、可选 embedding/RRF 通道，以及可复现的冻结评测。上一轮记录保留在 [v0.2 验证记录](verification-v0.2.md)。

## 前端：实际战锤画面

新增 5 张官方 Steam 商店原始素材：本体宣传画、基斯里夫、震旦、恐虐截图，以及帝国艾斯佩斯 DLC 截图。下载地址、尺寸、SHA-256、用途与权利归属见 [game-media.md](game-media.md)。官方宣传插画不称作实机截图，官方游戏图也不充当具体 Mod 的效果证据。

首页三组场景可手动切换，支持键盘操作、选择状态和随场景变化的来源链接；帝国、震旦、基斯里夫入口连接真实目录搜索。探索、推荐与清单页接入官方横幅。旧生成城堡图保留历史文件，不再被页面引用。

使用生产构建连接既有 101 条真实目录，检查桌面 1440×1100 与手机 390×960 的首页、探索、推荐、详情、清单。没有 JavaScript 页面异常、水平溢出或可见图片加载失败。已查看首页桌面/手机、派系卡片、帝国切换场景与推荐结果截图，人物主体清楚，文字可读。

截图：[首页桌面](screenshots/home-v3-desktop.jpg)、[首页手机](screenshots/home-v3-mobile.jpg)、[帝国场景](screenshots/home-v3-empire.jpg)、[派系入口](screenshots/factions-v3-desktop.jpg)、[探索](screenshots/explore-v3-desktop.jpg)、[推荐](screenshots/recommend-v3-desktop.jpg)、[推荐结果](screenshots/recommend-v3-results.jpg)、[推荐手机](screenshots/recommend-v3-mobile.jpg)、[清单](screenshots/collection-v3-desktop.jpg)。

本地 HTTPS 检查代理影响外部 Steam 图片证书，仅截图用隔离 context 放宽该项验证；产品代码、正式 E2E 和容器未关闭 TLS 校验。中文字体由临时 QA 环境提供，页面使用系统字体回退。

## 本地自动化结果

使用锁定依赖、Python 3.12.14、Node.js 24.19；CI 使用 Node.js 22。版本统一为 0.3.0，未增加第三方运行时依赖。

| 检查 | 实际结果 |
| --- | --- |
| 完整后端 pytest | **183 passed，15.64 秒** |
| 后端总语句覆盖率 | **90%** |
| 新 retrieval.py / embeddings.py 覆盖率 | **99% / 99%** |
| recommendations.py / jobs.py 覆盖率 | **98% / 97%** |
| Ruff 检查与格式 | 通过 |
| 前端 ESLint / TypeScript | 通过 |
| Next.js 生产构建 | 通过 |
| Chromium E2E | **8 passed，10.7 秒** |
| 冻结评测 `--check` | 通过；0 次模型/网络调用 |

新测试覆盖 BM25 排序与字段权重、双语别名、负向硬约束、目录中文摘要搜索、RRF、索引 namespace 和来源指纹、向量数值/维度/模型/批量校验、请求预算、提供方错误、请求期间来源变更、回退元数据，以及评测指标与数据完整性。

浏览器测试连接实际 API 和隔离 SQLite fixture 数据库，覆盖已有业务链路，并增加官方画面的有效像素加载、键盘切换、来源跟随、派系搜索跳转、检索字段和匹配词。合成数据仅进入显式 E2E 数据库。

独立审查复现并修复两项问题：目录改用 BM25 时遗漏中文摘要；无效 embedding URL 端口绕过预期降级。对应 8 个参数化回归用例另经独立复验通过。SQLite 手工回填并发限制与目录搜索全量扫描限制已纳入运行文档。

## 冻结检索评测

真实 Workshop 短文本 42 条，固定需求 28 条，开发/回归各 14 条；其中 25 条有答案、3 条无答案。数据与标签在首次跑分前冻结并记录 SHA-256。评测不读取生产数据库、不调用外部服务、不导入演示 Mod。

| 方法 | Recall@5 | nDCG@5 | MRR@5 | 无答案正确数 | 标注禁入项违规 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 旧关键词 | 0.9200 | 0.87533 | 0.87333 | 3/3 | 0 |
| BM25 | 0.9200 | 0.88997 | 0.90000 | 3/3 | 0 |

排序指标只对 25 条有答案需求宏平均；9 条需求带独立于运行筛选函数的禁入 ID 标签。标签为开发判断，尚非独立人工金标；回归拆分不是统计留出集。两个中文意译案例仍失败，完整说明见 [评测文档](retrieval-evaluation.md)。报告不构成真实语义通道或全量生产质量结论。

## 真实目录推荐与索引状态

本轮复用既有 101 条目录，其中 18 条为作者中文摘录，没有重新导入 101 条。需求“想玩震旦，增加新兵种，不要大型重做”完成实际 API 请求，返回 5 条候选，`engine=rules`、`retrieval.method=bm25`，没有模型调用。

本地运行 ID 为 `bf47f06d-531c-4ef3-bb1c-118f7e54f65e`；候选为 `2862938097`、`3823280996`、`3388312326`、`3031564620`、`3389974313`。运行记录位于忽略的本地数据库，克隆后不会自带。

`embedding-status` 实际返回配置关闭、目录 101 条、有效向量 0 条。可选语义通道有实现与自动化验证，但本轮没有可用真实 embeddings 凭证，不能宣称已获得语义质量提升。

## PostgreSQL 与容器

CI 的 PostgreSQL smoke 新增真实数据库写读：使用隔离 fixture 和假提供方写入 JSON 向量，验证模型 namespace 隔离、二次回填跳过、硬约束后的混合召回，以及来源变化后的过期回退。假提供方仅替代模型网络调用，数据库路径使用真实 PostgreSQL。已有并发入队/领取、租约恢复、旧 owner fencing 检查保留。

新 smoke 已在隔离 SQLite 下预检，这不是 PostgreSQL 验收。本地没有 PostgreSQL 服务或 Docker daemon。实际远端结果以 [本版本提交的 GitHub Actions](https://github.com/volcano-code/ModSage/actions/workflows/ci.yml) 为准，发布说明会引用具体运行。四项 CI 分别验证后端/PostgreSQL、前端构建、浏览器集成、完整 Compose 与可选 worker；脚本存在不等于已运行通过。

## 当前边界

- 真实 LLM 生成、Steam QueryFiles 认证、真实 embeddings 质量需要可用凭证后继续验收，当前没有伪造成功结果。
- 向量通道是有界精确扫描，尚未部署 pgvector、ANN 或神经重排；目录搜索仍为应用层全量扫描。
- SQLite 同库手工 `embed` 应串行执行，没有每日 worker 的分布式租约。
- 未测试实际游戏加载、存档和跨 Mod 兼容性，没有把依赖等同于加载顺序。
- 每日刷新尚无连续多日记录；推荐 SSE、账户同步、通知 Outbox 和正式数据库迁移待实现。
- 仓库提交不会自动创建公开托管实例。自动刷新默认关闭，自动中文生成预算仍为 0。
