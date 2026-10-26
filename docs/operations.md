# ModSage 运行与更新手册

本手册覆盖原生 SQLite 和自行管理的单个 Compose/PostgreSQL 部署，命令从仓库根目录执行。仓库没有自动公开托管服务，也没有预设真实模型供应商。默认端口绑定回环地址，数据库和管理接口不直接暴露公网。

## 启动前检查

首次安装与无 Key 验收见 [本地部署与验收](local-deployment.md)。先按该文档非覆盖地创建 `.env`；已有文件保留。v0.6 新配置默认关闭模型调用，完成本地验收后才显式启用提供方。

```bash
python3 scripts/local_check.py doctor --mode docker
docker compose config --quiet
docker compose up --build --detach --wait
docker compose exec -T api python -m modsage.cli db-status
docker compose exec -T api python -m modsage.cli preflight
docker compose exec -T recommendation-worker python -m modsage.cli preflight
python3 scripts/local_check.py running --base-url http://127.0.0.1:3000
```

已存在 `.env` 时保留原文件，不重复复制。首次启动仍是空目录，需要从真实来源导入；preflight 的 ready 不证明外部模型已经连通。完成 [provider 专项验收](provider-readiness.md) 后，再逐步启用摘要与混合检索。

默认启动 `db`、`api`、`web` 和 `recommendation-worker`。每日刷新独立启用：

```bash
docker compose --profile automation up --build --detach --wait
docker compose exec -T worker python -m modsage.cli jobs --limit 10
```

`MODSAGE_REFRESH_ENABLED=true` 只打开计划，仍需运行该 worker。每日计划按北京时间计算；摘要默认批量上限为 0，不会随来源刷新自动调用模型。

## 更新代码与配置

先确认正在使用的数据库和配置，查看最近的推荐、刷新任务。模型设置首次启用前，用 preflight 检查已有积压，不用 canary 代为执行队列。下面按实际部署方式选择一条流程；备份或迁移失败时先停止，不继续启动应用。

### Compose / PostgreSQL 升级

停止应用消费者，保留数据库服务：

```bash
docker compose --profile automation stop api recommendation-worker worker web
```

然后完成下节的 [PostgreSQL 备份与恢复演练](#postgresql)。确认备份成功后，再更新镜像和数据库；以下命令要求 `db` 仍在运行：

```bash
git status --short
git pull --ff-only
docker compose build api
docker compose run --rm --no-deps api python -m modsage.cli db-upgrade
docker compose run --rm --no-deps api python -m modsage.cli db-status
docker compose up --build --detach --wait --force-recreate api recommendation-worker web
# 仅在使用每日刷新时执行：
docker compose --profile automation up --build --detach --wait --force-recreate worker

docker compose exec -T api python -m modsage.cli preflight
docker compose exec -T recommendation-worker python -m modsage.cli preflight
docker compose exec -T api python -m modsage.cli usage --limit 10
python3 scripts/local_check.py running --base-url http://127.0.0.1:3000
```

启用了刷新 worker 时，再执行 `docker compose exec -T worker python -m modsage.cli preflight`。本流程中的 `running` 只读取健康状态和页面；带 Key 的已有部署不运行专用于无 Key 环境的 `--exercise`。

### 原生 / SQLite 升级

先在 API、前端及所有独立 worker 的终端按 `Ctrl+C` 停止进程。旧 v0.5 尚无 `backup-db` 命令，因此可以在应用全部停止后先拉取代码、安装新版工具；这些步骤不启动服务，也不迁移业务数据库：

```bash
git status --short
git pull --ff-only
.venv/bin/python -m pip install --require-hashes -r apps/api/requirements-dev.lock
.venv/bin/python -m pip install --no-deps -e 'apps/api[dev]'
npm --prefix apps/web ci
```

仍从同一个仓库根目录执行备份，确保 `.env` 和终端中的 `MODSAGE_DATABASE_URL` 指向原先使用的库：

```bash
mkdir -p artifacts/backups
.venv/bin/python -m modsage.cli backup-db --output artifacts/backups/before-v0.6.db
.venv/bin/python -m modsage.cli db-status
```

备份目标已存在时另选一个新文件名，不覆盖旧副本。完整旧 v0.5 的 `db-status` 返回 `legacy` 且退出码为 1，这是尚未登记 revision 的状态。**确认备份报告 `succeeded` 后**，再显式升级：

```bash
.venv/bin/python -m modsage.cli db-upgrade
.venv/bin/python -m modsage.cli db-status
.venv/bin/python -m modsage.cli preflight
npm --prefix apps/web run build
```

随后按 [本地启动步骤](local-deployment.md#初始化与启动) 重启 API 和前端；原先使用独立 worker 的，也使用同一数据库和配置重启。最后执行 `python3 scripts/local_check.py running --base-url http://127.0.0.1:3000`。

### 数据库版本与迁移边界

当前 Alembic revision 为 `0001_v05_baseline`，表示被冻结的 v0.5 业务结构。应用版本 `0.6.0` 与数据库 revision 分开记录。空库通过迁移创建；完整匹配的旧 v0.5 库在核对表、列、类型、主键、约束、索引与默认值后登记 revision，保留原记录。

`db-status` 只检查，不创建缺失的 SQLite 文件或目录；只有 `current` 返回退出码 0。`legacy` 表示可由本版接管，但尚未迁移；`empty` 表示尚未初始化；`blocked` 表示结构或版本不可接受。数据库连接错误单独失败，不回显连接字符串。

升级用 SQLite 写锁或 PostgreSQL 事务 advisory lock 串行执行，并设定锁等待上限。API、worker、CLI 同时启动时不会各自无锁建表。未知 revision、缺表、额外对象或结构不一致会停止，不强制 stamp、不自动删改未知结构；本版不提供自动降级。`init-db` 保留为受保护升级入口的兼容命令，不能用它修复任意版本数据库。

迁移资源包含在 Python wheel 中，容器直接使用同一代码。后续业务结构变更应新增 revision，并扩展受支持结构与升级测试；不要改写已经发布的 baseline。

API、CLI 和两个 worker 必须指向同一数据库，并使用一致的模型配置、额度与总开关。配额计数在数据库中共享，限额值读取各进程启动环境；改变一个进程的环境不会实时改变其他进程的设置。

## 紧急停止模型消费

先停止所有可能消费模型的应用进程，再修改环境和重建。停止请求不能撤回提供方已经接收的工作；已预留的次数不会退还。

原生部署在 API 和所有独立 worker 终端按 `Ctrl+C`，将根目录 `.env` 的 `MODSAGE_PROVIDER_CALLS_ENABLED` 改为 `false`，再按本地部署文档重新启动这些进程。以下命令用于 Compose：

```bash
docker compose stop api recommendation-worker
# 如果启用了每日刷新：
docker compose --profile automation stop worker
```

在 `.env` 设置 `MODSAGE_PROVIDER_CALLS_ENABLED=false` 后重建相应服务：

```bash
docker compose up --detach --wait --force-recreate api recommendation-worker
# 如果启用了每日刷新：
docker compose --profile automation up --detach --wait --force-recreate worker
docker compose exec -T api python -m modsage.cli preflight
docker compose exec -T api python -m modsage.cli usage --limit 20
```

已有目录与规则/BM25 推荐可以继续使用。不要删除 usage 记录来恢复额度；当天记录是限额依据，删除会重置实际计数。需要重新启用时修改配置并重建全部模型调用进程。

## 数据库备份与恢复演练

备份包含来源快照、任务查询、结果及调用记录，应按私有数据保管，不提交 Git。推荐任务访问凭证只保存在浏览器，数据库只有摘要；恢复数据库不会重建用户已经丢失的浏览器凭证。

### PostgreSQL

```bash
mkdir -p artifacts/backups
(
  set -e
  umask 077
  set -o noclobber
  docker compose exec -T db pg_dump -U modsage -Fc modsage > artifacts/backups/modsage-before-v0.6.dump
  docker compose exec -T db pg_restore --list < artifacts/backups/modsage-before-v0.6.dump > /dev/null
)
```

以上子命令必须成功退出；归档列表检查能发现部分格式错误，完整恢复另做下面的演练。若导出失败，可能留下一个不完整的输出文件，不能把它当作有效备份。不要用同名备份覆盖唯一旧副本；重复备份时为文件增加日期或版本，并同步修改恢复命令中的输入路径。

恢复写入一个新的演练数据库，不覆盖正在使用的库。下面的子 shell 会在任一步失败时停止；如果 `modsage_restorecheck` 已存在，换一个新的数据库名后重新执行，不复用或删除原来的库：

```bash
(
  set -e
  docker compose exec -T db createdb -U modsage modsage_restorecheck
  docker compose exec -T db pg_restore -U modsage --no-owner --no-acl --exit-on-error --single-transaction --dbname=modsage_restorecheck < artifacts/backups/modsage-before-v0.6.dump
  docker compose exec -T db psql -U modsage -d modsage_restorecheck -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM mods;'
  docker compose exec -T db psql -U modsage -d modsage_restorecheck -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM provider_calls;'
)
```

这一步验证归档能够恢复，并显示两张关键表的行数；与备份时的记录核对，不把“命令成功”当作完整业务验收。演练没有修改应用的数据库连接，也没有启动恢复库中的队列。实际运营时还应把备份保存在独立持久存储中。

恢复旧备份会回滚 usage 计数，也会恢复旧的排队任务。正式切换数据库前应保持模型总开关关闭，核对任务、提供方账单和当天已发生的调用；不要把从旧快照恢复的剩余额度视为真实余额。

### SQLite

新版 CLI 使用 SQLite online backup API，包含已经提交但仍在 WAL 中的记录，不在有写入时直接复制 `.db`。源库以只读模式打开，临时输出通过 schema、完整性和外键校验后，以 `0600` 权限原子发布为新文件：

```bash
mkdir -p artifacts/backups
.venv/bin/python -m modsage.cli backup-db --output artifacts/backups/modsage-local.db
.venv/bin/python -m modsage.cli restore-db --input artifacts/backups/modsage-local.db --output artifacts/backups/modsage-restored-check.db
# 只检查新副本，不改变应用正在使用的库。
MODSAGE_DATABASE_URL=sqlite:///./artifacts/backups/modsage-restored-check.db .venv/bin/python -m modsage.cli db-status
```

目标已存在、同名原库、符号链接、遗留目标 WAL/SHM/journal、未知 schema 或损坏记录都会拒绝。父目录需要先创建，失败后不会用不完整文件覆盖已有目标。报告提供 SHA-256、文件大小、逐表行数和 schema，不打印数据库正文。`publication_durability_unknown` 表示校验后的文件已发布，但目录 fsync 未确认；应检查并保留该文件，不要认为没有输出而覆盖。

只读 SQLite 连接仍可能维护锁相关的 WAL/SHM 辅助文件，不承诺源目录完全没有文件系统活动。备份保持主数据库内容与 schema；旧 v0.5 备份保留 legacy 状态，因此上述新副本的 `db-status` 也会返回 `legacy` 和退出码 1。需要使用该副本时，再对该副本显式执行 `db-upgrade`。

恢复命令不切换应用配置、不执行旧队列，也不需要可用的模型配置。示例中的 `MODSAGE_DATABASE_URL=...` 只对紧随其后的那一条状态检查生效，不会修改 `.env` 或运行中的 API。模型调用账本会按备份原样恢复，旧快照可能少于实际已发生的费用，正式切换前仍需关闭模型总开关并对账。

CI 的 SQLite 演练会逐表比较全部记录，并检查排队任务、来源快照、向量和失败/无回执额度。Compose 的 PostgreSQL 演练会对明确隔离的测试库执行真实 pg_dump/pg_restore、四进程旧库接管和逐表内容哈希比较。它们验证实现，不代替你的实际部署备份；只有在自己的库上执行并检查结果，才能称该部署的备份恢复通过。

## 日常观察与排错

| 观察到的状态 | 含义与处理 |
| --- | --- |
| preflight `configuration_incomplete` | 按报告中的字段名补齐；不输出字段值 |
| `configuration_invalid` / `invalid_base_url` | 核对 API 根地址、完整操作后缀误填、端口、嵌入账号和 query |
| `unauthorized` | 当前凭证或模型访问权限未被接受；查看提供方账户设置 |
| `rate_limited` | 上游限制；等待后显式重试，不开启无界自动重试 |
| `daily_call_limit` / `minute_call_limit` | 本应用的共享次数额度已经达到上限；失败请求同样计数 |
| `usage_unavailable` | 无法可靠预留或完成调用记录；检查数据库，不要关闭记账以绕过错误 |
| `output_truncated` | 输出预算耗尽；核对模型 token 参数和输出上限后小样本重测 |
| `invalid_usage` | 上游返回的 usage 不符合整数与一致性约束 |
| `unexpected_model` | embeddings 响应模型与当前配置不符；确认别名后显式配置，不混用向量空间 |
| `started` 长时间无完成回执 | 调用可能仍在途或进程已中断；消费情况未知，不算成功，不退回额度 |
| 完成推荐但 `engine=rules` | 查看 warnings；可能未配置模型，也可能本次模型失败后安全回退 |
| 请求 hybrid 但实际 `method=bm25` | 核对当前配置、有效索引、来源变化和调用失败，不能只看配置开关 |

`usage` 为本地 CLI 诊断，不提供公开网页管理入口。公开健康响应只显示有限状态；不能从它推断余额、质量、当前请求是否成功或 worker 一直存活。

## 公网部署边界

外部访问应经过受控 HTTPS 入口，只代理前端服务。数据库/API 保持私有；不把 `MODSAGE_*_API_KEY` 放进 `NEXT_PUBLIC_*`。SSE 路径需要禁用代理响应缓冲，代理读取超时应大于应用 SSE 连接预算，确保中断后允许客户端携带游标重连。

本应用的 HTTP 限速是进程级辅助保护，Next.js 代理后的 API 可能把多个访问者视为同一代理地址；公网入口仍需按真实客户端设置限速、请求体上限和连接上限。模型日/分钟限额是共享的，不能替代账户级配额或公平性策略。当前本机历史和清单不提供跨设备账户同步。

本版本没有公开部署地址、多日运行数据、完整站点覆盖或独立用户评测。这些结果必须来自实际部署与观察，不由一个通过的构建推断。
