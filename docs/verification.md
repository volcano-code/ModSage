# v0.6 验证记录

日期：2026-10-26（北京时间）。本轮完成本地部署交付所需的数据库版本化、可验证备份恢复、环境诊断、实际运行验收与原生生产启动入口。没有配置或调用真实 LLM、Embedding 或 Steam 认证服务。

前一轮记录保留在 [verification-v0.5.md](verification-v0.5.md)。用户本机操作见 [local-deployment.md](local-deployment.md)，已有数据库更新见 [operations.md](operations.md)。

## 本地最终验证

| 检查 | 实际结果 |
| --- | --- |
| 后端完整测试 | **467 passed，89.65 秒** |
| Python 语句覆盖率 | **91%**；backup 93%，schema 86%，provider usage 98% |
| Ruff / 格式 / diff 空白检查 | 通过 |
| ESLint / TypeScript | 通过 |
| Next.js 16.4.2 生产构建 | 通过，保留完整 TypeScript 检查 |
| Playwright 浏览器集成 | **15 passed，20.8 秒**，使用新的 standalone 启动入口 |
| SQLite 实际备份恢复 | 通过，17 张表全部记录一致，任务/事件/向量/来源/用量保留 |
| 已有真实 v0.5 目录副本备份、恢复、升级 | 通过，101 条 Mod、18 条中文介绍和其余业务记录全部保留 |
| 完整原生栈本地验收 | 通过，前端→API→数据库、官方图片、后台推荐、幂等、SSE 与凭证保护 |
| CLI + loopback provider 契约 | 通过，4 次模拟 HTTP、0 次真实提供方请求 |
| 冻结检索评测 | 通过，BM25 Recall@5=0.92、nDCG@5=0.88997、MRR@5=0.9，指标未改变 |
| 实际 wheel 打包后独立运行 | 通过，migration 资源齐全，在仓库外解包后可创建并验证 SQLite schema |

本地运行环境为 Python 3.12.14、Node 24.22.1。Playwright 下载源在当前环境返回无效归档，因此使用 Google 官方稳定版 Chrome 156.0.8078.47 完成浏览器测试；CI 使用 Playwright 管理的 Chromium。恢复的旧构建缓存已清理。Next.js 在当前沙箱中捕获 TypeScript 子进程输出为空，使用相同代码在允许的执行环境中通过了完整生产构建，没有关闭 TypeScript 或修改构建结果。

## 数据库版本与升级覆盖

数据库 revision 为 `0001_v05_baseline`，应用版本为 `0.6.0`。版本化机制使用冻结的 16 张业务表，不从可变化的 ORM 模型生成历史 baseline。

40 项迁移回归包括空库创建、完整旧 v0.5 结构接管、重复执行、多连接/进程竞争、事务回滚、只读缺失文件、未知 revision 和结构差异拒绝。补充了 Alembic 自动比较可能忽略的差异：额外列与 revision 表同名、VARCHAR 长度缺失、部分/表达式/降序/不同 collation 索引，以及 SQLite URI 布尔变体和字面 `~` 路径。

PostgreSQL 使用事务 advisory lock；SQLite 使用有界的立即写锁。API、CLI 和 worker 使用相同入口。公开健康检查在启动后的完整结构核验基础上读取 revision；版本表缺失或 revision 不一致返回脱敏 503。

仅自动接管**完整 v0.5**。保存的更早 v0.4 库缺少两张 provider 表，检查明确返回 blocked，没有补表或改写它。不能从这些测试推出任意历史版本都能自动升级。

## 备份与恢复覆盖

36 项 SQLite 备份回归覆盖 WAL 中已提交数据、同目标并发发布、已有路径与符号链接、遗留目标日志文件、损坏库、未知 schema、外键破坏、超时，以及发布后目录 fsync 失败。

独立 roundtrip 创建明确标记的临时夹具，实际执行备份和恢复，并比较 17 张表的全部记录。恢复后的排队任务和事件保持一致；失败与无回执的调用仍占用额度，不因恢复退回。没有创建模型 HTTP 客户端，也没有把夹具导入应用目录。

另外，对上一轮保存的真实 v0.5 验收数据库执行只读备份，恢复到新的隔离文件，再升级副本。逐表业务记录完全相等，来源文件 SHA-256 保持不变，101 条 Mod 和 18 条中文介绍保留。随后启动的本地服务只连接这个恢复副本。SQLite 只读连接可能维护锁相关侧文件，因此这里的未改写证据指主数据库内容及 schema，不扩展成源目录完全没有文件系统活动。

## 本机诊断与原生启动

标准库工具 `scripts/local_check.py` 有 35 项测试，覆盖 native/Docker 环境、Docker context 优先级、端口、空目录、版本异常、显式任务验收、凭证隔离、拒绝重定向、忽略环境代理、响应大小与慢速响应墙钟超时。

在实际恢复的 101 条目录上运行 `running --exercise --require-data`，全部通过：前端和官方战锤 JPEG 可访问、schema current、实际推荐 `engine=rules` / `retrieval=bm25`、6 条持久进度事件、幂等提交与 SSE 游标回放、错误凭证和匿名结果访问被拒绝。该测试创建一个明确标记的本地验收任务，不删除用户任务。

原生 `npm run start` 现在使用 Next standalone server，并按 Docker 相同布局补齐 public/static。已实测配置地址保留、本机默认绑定、6 组非法参数拒绝、缺少 build 的提示，以及 SIGTERM 关闭端口并传播退出码。独立启动器检查使用 3200 端口，完整验收使用 3000/8000，浏览器集成使用 3100/8100。测试完成后停止临时服务。

## 本次远端 CI 检查

四个 job 继续覆盖后端/真实 PostgreSQL、前端、浏览器与完整 Compose。本轮新增：

- SQLite 实际备份恢复报告。
- API 和 worker 容器中的 schema / preflight / usage 检查。
- 从真实前端入口执行本地验收工具，验证独立推荐 worker。
- 在全新、明确隔离的 PostgreSQL source/target 库上，实际执行 pg_dump 和 pg_restore。
- 四个独立进程同时接管旧 v0.5 结构，要求只有一个 adopted_legacy，其余 unchanged。
- 比较恢复前后所有表的行数和规范化内容 SHA-256。
- 在回滚事务中检查 PostgreSQL partial index、materialized view、用户 trigger、无长度 VARCHAR、未知 revision、移除或改指 SERIAL 默认序列这七种结构差异。

远端运行结果以本次提交对应的 [GitHub Actions](https://github.com/volcano-code/ModSage/actions/workflows/ci.yml) 为准；交付回复提供准确的提交与流水线链接。本地通过结果不替代远端状态。CI 只使用隔离夹具，不调用真实 Steam 或模型服务，不上传备份数据库。

## 尚需用户本地完成

- 在自己的电脑完成安装、端口和浏览器访问验证，再导入少量真实 Steam 目录。
- 在自己的部署上执行备份恢复和重启验收；云端临时库通过不代替这一步。
- 观察实际每日刷新、失败补偿和持续运行效果；本轮不声称已完成多日运行。
- 真实模型/向量权限、中文语义忠实度、实际费用与质量提升仍需自行配置后通过小规模 canary 和人工核验。
- 没有部署公网服务器或域名；账户、跨设备数据、通知、保留策略和更大规模检索仍是后续产品范围，不影响首次本机运行。

## 重现入口

```bash
.venv/bin/python -m pytest apps/api/tests --cov=modsage
.venv/bin/python -m ruff check apps/api scripts
.venv/bin/python -m ruff format --check apps/api scripts
.venv/bin/python scripts/backup_restore_smoke.py
.venv/bin/python scripts/provider_contract_smoke.py
.venv/bin/python scripts/evaluate_retrieval.py --check
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run test:e2e
```

部署运行检查命令及有效退出状态见 [本地部署文档](local-deployment.md)。XML、coverage、备份演练和本地运行 JSON 记录保存在忽略的 artifacts；GitHub CI 上传测试报告，不包含生产数据库、原始作者快照或 Key。
