# ModSage 运行与更新手册

本手册适用于本地评估或自行管理的单个 Compose 部署。仓库没有自动公开托管服务，也没有预设真实模型供应商。默认端口绑定回环地址，数据库和管理接口不直接暴露公网。

## 启动前检查

```bash
cp .env.example .env
# 在自己的编辑器里填写环境配置；不要把凭证放入 shell 命令历史。
docker compose config --quiet
docker compose up --build --detach --wait
docker compose exec -T api python -m modsage.cli preflight
docker compose exec -T recommendation-worker python -m modsage.cli preflight
```

已存在 `.env` 时保留原文件，不重复复制。首次启动仍是空目录，需要从真实来源导入；preflight 的 ready 不证明外部模型已经连通。完成 [provider 专项验收](provider-readiness.md) 后，再逐步启用摘要与混合检索。

默认启动 `db`、`api`、`web` 和 `recommendation-worker`。每日刷新独立启用：

```bash
docker compose --profile automation up --build --detach --wait
docker compose exec -T worker python -m modsage.cli jobs --limit 10
```

`MODSAGE_REFRESH_ENABLED=true` 只打开计划，仍需运行该 worker。每日计划按北京时间计算；摘要默认批量上限为 0，不会随来源刷新自动调用模型。

## 更新代码与配置

1. 查看最近的推荐和刷新任务。模型设置首次启用前，用 preflight 检查已有积压，不用 canary 代为执行队列。
2. 备份当前数据库。v0.5 只新增 provider 表和索引，不改写旧列；普通 `init-db` 是幂等加表，不能把它当作任意未来版本的迁移工具。
3. 正常 fast-forward 拉取代码，检查版本和相关验证记录。
4. 重建 API、推荐 worker、前端；启用了每日刷新时也重建刷新 worker。
5. 对 API 和各 worker 分别执行 preflight，检查健康响应和 usage。

```bash
git pull --ff-only
docker compose up --build --detach --wait --force-recreate api recommendation-worker web
# 仅在使用每日刷新时执行：
docker compose --profile automation up --build --detach --wait --force-recreate worker

curl --fail --silent --show-error http://127.0.0.1:3000/api/v1/health
docker compose exec -T api python -m modsage.cli usage --limit 10
```

API、CLI 和两个 worker 必须指向同一数据库，并使用一致的模型配置、额度与总开关。配额计数在数据库中共享，限额值读取各进程启动环境；改变一个进程的环境不会实时改变其他进程的设置。

## 紧急停止模型消费

先停止所有可能消费模型的应用进程，再修改环境和重建。停止请求不能撤回提供方已经接收的工作；已预留的次数不会退还。

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
docker compose exec -T db pg_dump -U modsage -Fc modsage > artifacts/backups/modsage.dump
```

不要用同名备份覆盖唯一旧副本。实际运营时为文件增加日期或版本，保存到独立持久存储，并对保留周期作明确管理。恢复先写入一个新的演练数据库，不覆盖正在使用的库：

```bash
docker compose exec -T db createdb -U modsage modsage_restorecheck
docker compose exec -T db pg_restore -U modsage --no-owner --exit-on-error --dbname=modsage_restorecheck < artifacts/backups/modsage.dump
docker compose exec -T db psql -U modsage -d modsage_restorecheck -c 'SELECT count(*) FROM mods;'
docker compose exec -T db psql -U modsage -d modsage_restorecheck -c 'SELECT count(*) FROM provider_calls;'
```

恢复旧备份会回滚 usage 计数，也会恢复旧的排队任务。切换数据库前应保持模型总开关关闭，核对任务、提供方账单和当天已发生的调用；不要把从旧快照恢复的剩余额度视为真实余额。

### SQLite

本地 SQLite 应使用 SQLite backup API，不在有写入时直接复制一个 `.db` 文件。示例只读取原库并创建新备份，若目标已经存在则停止：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import sqlite3

source = Path('data/modsage.db').resolve()
target = Path('data/modsage-backup.db').resolve()
if not source.is_file() or target.exists():
    raise SystemExit('Source missing or backup already exists')
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original:
    with sqlite3.connect(target) as backup:
        original.backup(backup)
        assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
print('Backup verified')
PY
```

上述是操作模板。只有在自己的部署上执行并检查结果，才能称该部署的备份恢复已通过；CI 的临时数据库验证不替代生产备份。

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
