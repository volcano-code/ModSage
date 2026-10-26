# ModSage v0.6 本地部署与验收

本文面向 Ubuntu/Linux 本机使用。先运行完整的无 Key 版本：真实 Workshop 目录、中文原文介绍、日报、规则理解、BM25 推荐、持久任务和 SSE 进度。LLM 与语义向量需要以后单独接入，首次部署不需要填写任何 API Key。

云端测试验证代码与受控环境；你的电脑还需要验证安装权限、端口、网络、浏览器和重启后的运行情况。以下命令都从仓库根目录执行。两种部署方式选一种，不要同时占用 3000/8000 端口。每个阶段以上一步成功为前提；安装、构建或启动失败时，先处理该错误，再继续。

## 1. 获取代码与创建配置

没有本地仓库时，放到当前用户有写权限的目录：

```bash
mkdir -p "$HOME/projects"
cd "$HOME/projects"
git clone https://github.com/volcano-code/ModSage.git
cd ModSage
```

已有仓库但尚未运行、没有业务数据库时，进入自己的实际路径，先查看状态，再正常拉取：

```bash
git status --short
git pull --ff-only
```

如果有自己的修改，先保留和处理它们；不要通过删除目录或强制重置来绕过冲突。**已经运行过并有数据库时，先走 [运行手册的升级步骤](operations.md#更新代码与配置)**：停止应用进程，备份，再迁移和启动；不要把已有部署当作首次安装重新初始化。

创建 `.env`，已有文件则原样保留：

```bash
python3 - <<'PY'
from pathlib import Path
import os

target = Path('.env')
try:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print('已保留现有 .env，请在编辑器中核对配置。')
else:
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(Path('.env.example').read_text(encoding='utf-8'))
    print('已创建无 Key 配置，模型调用默认关闭。')
PY
```

新示例中 Key、模型和提供方地址为空，`MODSAGE_PROVIDER_CALLS_ENABLED=false`、`MODSAGE_REFRESH_ENABLED=false`、`MODSAGE_RETRIEVAL_MODE=bm25`。已有 `.env` 不会被这个步骤修改；本轮无 Key 验收应保持这些设置，且 API 与所有 worker 使用同一配置。进程环境变量优先于文件，旧终端中的 `export` 也需要核对。

## 2A. 原生启动：Python + Node.js + SQLite

适合本机尚未安装 Docker 的情况。需要 Python 3.12+、Node.js 22+ 和 npm 10+；不需要独立 PostgreSQL 服务。新安装默认使用仓库下的 `data/modsage.db`，根目录 `.env` 的推荐 worker 开关应为 `MODSAGE_RECOMMENDATION_WORKER_ENABLED=true`。如果已有配置或终端环境设置了 `MODSAGE_DATABASE_URL`，先确认它指向你实际要使用的数据库；不要为了通过检查改到另一个空库。

### 安装锁定依赖

```bash
python3 --version
node --version
npm --version

python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r apps/api/requirements-dev.lock
.venv/bin/python -m pip install --no-deps -e 'apps/api[dev]'
npm --prefix apps/web ci

python3 scripts/local_check.py doctor --mode native
```

安装命令使用普通用户执行。`doctor` 会检查目录权限、Python/Node/npm、虚拟环境和依赖、配置文件是否存在、3000/8000 端口是否被占用。它不自动安装软件或修改权限；若返回 `blocked`，先按对应项处理。运行中的 ModSage 会占用端口，此时改用下方 `running` 检查。

### 初始化与启动

首次空库可直接升级；已有库先备份：

```bash
.venv/bin/python -m modsage.cli db-upgrade
.venv/bin/python -m modsage.cli db-status
.venv/bin/python -m modsage.cli preflight
npm --prefix apps/web run build
```

`db-status` 应为 `current`，revision 应与 head 一致。空目录时 `preflight` 会有 `catalog_empty` 警告；模型关闭会有 `provider_calls_disabled`，这两项符合首次无 Key 启动的预期。下面需要两个持续运行的终端；保留刚才的安装终端用于验收和导入即可。

**终端 A：API 与内嵌推荐 worker**

```bash
cd "$HOME/projects/ModSage"
.venv/bin/python -m uvicorn modsage.main:app --host 127.0.0.1 --port 8000
```

**终端 B：已构建的前端**

```bash
cd "$HOME/projects/ModSage"
npm --prefix apps/web run start -- --hostname 127.0.0.1 --port 3000
```

若你的仓库在其他位置，将两个 `cd` 换成实际路径。打开 <http://127.0.0.1:3000>；API 文档为 <http://127.0.0.1:8000/docs>。默认原生 API 内嵌推荐 worker，因此两个终端就能执行推荐任务。前端默认通过服务端代理连接 `http://127.0.0.1:8000`；如果已有 `apps/web/.env.local` 或终端变量覆盖了 `API_BASE_URL`，核对该值后重新启动前端。API Key 不进入浏览器。

**终端 C：执行验收和数据导入**

```bash
cd "$HOME/projects/ModSage"
python3 scripts/local_check.py running --base-url http://127.0.0.1:3000
python3 scripts/local_check.py running --base-url http://127.0.0.1:3000 --exercise
```

`--exercise` 会创建一个内容明确标为“本地部署验收”的正常推荐任务，验证 worker、幂等提交、SSE 回放和凭证保护。任务保留在数据库中，工具不会清理真实用户记录。检查需要两类模型均未配置且调用开关关闭；不会为了通过检查改变设置或执行付费 canary。

首次空目录下，`running` 的整体结果可以是 `warning`，其中 `catalog` 提示先导入数据；这不表示服务启动失败。导入后使用第 3 节的 `--require-data` 进行正式目录验收。

## 2B. Docker Compose：PostgreSQL 完整栈

需要 Docker Engine 和 Compose 插件。此路径不要求安装 Node.js 或 Python 项目依赖；诊断脚本只使用系统 Python 标准库。

```bash
docker compose version
python3 scripts/local_check.py doctor --mode docker
docker compose config --quiet
docker compose up --build --detach --wait --wait-timeout 180

docker compose exec -T api python -m modsage.cli db-status
docker compose exec -T api python -m modsage.cli preflight
docker compose exec -T recommendation-worker python -m modsage.cli preflight

python3 scripts/local_check.py running --base-url http://127.0.0.1:3000
python3 scripts/local_check.py running --base-url http://127.0.0.1:3000 --exercise
```

默认启动 PostgreSQL、API、前端和独立推荐 worker。数据库保存在 Docker 命名卷中。端口绑定本机；如果 5432 已被其他 PostgreSQL 占用，应先确认该服务用途，再调整 Compose 的主机映射，容器内部仍使用 `db:5432`。

`doctor --mode docker` 只检查本地 Unix socket，不会连接远程 Docker context。若 `docker` 不存在或 daemon 权限不足，请按 [Docker Ubuntu 官方安装说明](https://docs.docker.com/engine/install/ubuntu/) 和 [Linux 安装后配置](https://docs.docker.com/engine/install/linux-postinstall/) 完成安装；不要用 `sudo npm` 或 `sudo pip` 处理 Docker 问题。

打开 <http://127.0.0.1:3000>。仅检查 Compose 语法时使用 `config --quiet`，不要分享展开后可能含密钥的完整配置。

## 3. 导入真实 Mod 数据

首次克隆没有生产数据。下面是显式、有上限的 Steam 公共页面发现，不需要 Steam API Key，但需要电脑能访问 Steam；受限、超时或空结果会如实报告，不会用假 Mod 填充目录。

原生部署：

```bash
.venv/bin/python -m modsage.cli discover --public --limit 20 --sort updated
.venv/bin/python -m modsage.cli digest
```

Compose 部署：

```bash
docker compose exec -T api python -m modsage.cli discover --public --limit 20 --sort updated
docker compose exec -T api python -m modsage.cli digest
```

导入后执行带数据要求的检查：

```bash
python3 scripts/local_check.py running --base-url http://127.0.0.1:3000 --exercise --require-data
```

检查报告中目录数量必须大于 0。工具核对目录总数和任务链路，具体候选列表在推荐页面查看。自然语言推荐可能没有匹配项，这取决于实际收录内容；一条特定需求没有结果并不等同于 worker 失败。可在页面尝试“想玩震旦，增加新兵种，不要大型重做”，核对实际 `rules` / `bm25` 状态、候选内容和来源链接。

无 Key 时，中文内容来自可确认的中文原文摘录；英文作者描述不会自动变成完整中文介绍。模型生成介绍与真实向量检索的接入见 [provider-readiness.md](provider-readiness.md)。

## 4. 本机验收清单

| 结果 | 验收要求 |
| --- | --- |
| 环境 | 所选模式的 doctor 没有 blocked/failed |
| 数据库 | db-status 为 current；schema revision 与 head 一致 |
| 页面 | 首页、探索、需求推荐、清单能打开；真实战锤横幅加载 |
| 数据 | 完成真实同步后目录数 > 0，标题和来源链接可以核对 |
| 推荐 | 任务由 queued 进入 completed，实际引擎和检索方式如实显示 |
| 恢复 | 推荐页面刷新后能恢复同一任务；重启服务后已有目录仍在 |
| 模型 | 未配置、调用关闭；没有把规则结果显示成模型结果 |
| 备份 | 先备份，再恢复到新位置；校验通过且原库继续保留 |

`running` 报告的 `provider_billing_verified=false`、`worker_configuration_verified=false` 是边界说明：公开 API 只能观察 API 当前配置，不能替代核对独立 worker 的配置或提供方账单。Compose 对各进程使用相同环境配置；自行拆分进程时要分别检查。

## 5. 关机、重启与继续运行

原生部署中，终端 A/B 按 `Ctrl+C` 正常停止；重启电脑后回到仓库，再运行两个启动命令。代码和锁文件没变时，不需要重新创建虚拟环境、安装依赖或构建前端。SQLite 默认位于 `data/modsage.db`。

Compose 正常停止和恢复：

```bash
docker compose --profile automation stop
# 重启后：
docker compose up --detach --wait
python3 scripts/local_check.py running --base-url http://127.0.0.1:3000
```

停止命令包含可能已经启动的每日刷新 worker；未启用它时也可以使用。已启用每日刷新的部署，恢复时改用 `docker compose --profile automation up --detach --wait`，同时恢复刷新 worker。

停止服务不需要删除卷。`docker compose down --volumes` 会删除数据库卷，不能作为日常重启命令。数据库里的任务可按既有租约机制恢复，但浏览器任务凭证仍取决于原浏览器本地存储；清空浏览器存储后不能凭数据库重建凭证。

## 6. 每日刷新与持续观察

初次验收后，在 `.env` 设置 `MODSAGE_REFRESH_ENABLED=true`、`MODSAGE_REFRESH_SOURCE=public`，保留 `MODSAGE_REFRESH_ENRICH_LIMIT=0`。原生部署先重启终端 A 的 API，让页面读取到新的计划配置；若单独运行推荐 worker，也一并重启。随后从仓库根目录额外持续运行刷新 worker：

```bash
.venv/bin/python -m modsage.cli worker
```

Compose 使用：

```bash
docker compose --profile automation up --build --detach --wait
docker compose exec -T worker python -m modsage.cli jobs --limit 10
```

默认计划为北京时间每日 08:00。电脑、服务和 worker 需要处于运行状态；不是关闭电脑后仍会自动执行。记录真实同步状态、失败与恢复，再判断连续运行效果。

## 7. 常见阻塞

| 检查结果或现象 | 处理 |
| --- | --- |
| 目录属于 root、无法创建 `.venv` 或安装前端依赖 | 将项目放到自己可写的目录；需要修复所有权时先核对准确路径，不对整个数据盘递归改权限 |
| `python3 -m venv` 失败 | 安装与当前 Python 匹配的 venv 组件，再重试；不要混用不同 Python 的虚拟环境 |
| Node 版本低于 22 | 升级 Node 后重新执行锁定依赖安装 |
| `docker` 不存在 | 使用原生路径，或按官方文档安装 Engine 与 Compose 插件 |
| Docker daemon 无权限/未启动 | 修复本机 Docker 服务或当前用户访问权限后重新执行 doctor |
| 端口占用 | 先确认是否已有 ModSage；已有则使用 running，其他服务占用则显式调整端口/代理配置 |
| `schema_drift` / `unknown_revision` 等迁移错误 | 停止升级，保留原数据库和已有备份；核对代码和数据库版本，不删表、不强制 stamp。验证型备份也会拒绝未知结构，不能把失败当作已生成有效备份 |
| Steam 同步失败 | 保留已有目录；核对网络和真实错误状态后重试有限导入，不循环高频请求 |
| 空目录或空推荐 | 先区分数据未导入与需求没有匹配项，查看 stats 和实际候选数 |
| 推荐一直排队 | 检查 API 内嵌 worker 是否启用，或 Compose recommendation-worker 是否运行、是否连接同一库 |

备份恢复、升级顺序和紧急停止模型消费见 [operations.md](operations.md)。本地部署通过之后，下一项验收是你的实际连续运行和可选真实提供方质量，不需要先增加新页面才能开始使用。
