# ModSage · 战锤模组参谋

**发现好模组，配出好战役。**

面向《全面战争：战锤 3》中文玩家的 Mod 发现、解读与需求推荐项目。
使用真实 Steam Workshop 元数据，保留来源和采集时间；把中文介绍、游戏内汉化与兼容性声明分别处理。

[![CI](https://github.com/volcano-code/ModSage/actions/workflows/ci.yml/badge.svg)](https://github.com/volcano-code/ModSage/actions/workflows/ci.yml)

![ModSage 首页：真实 Workshop 目录、中文界面与每日精选](docs/screenshots/home-desktop.jpg)

上图为 2026-10-20 的真实数据运行截图；首次克隆仍从空目录开始，通过下方命令自行同步。另见 [移动端截图](docs/screenshots/home-mobile.jpg)。

## 当前版本：v0.1 工程起步版

| 已实现的能力 | 实际边界 |
| --- | --- |
| Workshop 详情与公开合集读取、认证检索适配器 | QueryFiles 需要自行配置有效 Steam API key；无 key 详情路径已经做小规模实测 |
| 有上限的公共页面发现 | 明确的备用路径，不能保证全站完整覆盖；不会绕登录、验证码或限流 |
| 内容快照、哈希去重、指标记录、同步任务结果 | 保存真实源数据，失败不会刷新成功同步时间 |
| 目录、筛选、详情与来源证据 | 首次启动为空，需执行真实同步；没有默认演示 Mod |
| LangGraph 有界推荐流程与运行记录 | 无模型 key 时使用中英术语和关键词规则；尚未实现向量检索与神经重排 |
| 可配置模型的需求解析、中文介绍生成 | 不附带模型 key；真实模型连通性需在配置后验证，错误会明确退化 |
| 中文原文摘录 | 标记为来源中文，不冒充机器翻译；不据此认定游戏内汉化 |
| 日报构建与读取 | 使用收录目录的时间与可用指标；不伪造历史增长率 |
| 浏览器本机清单、前置检查 | 无跨设备账户同步；未知前置和未知兼容性会明确提示 |
| SQLite 本地启动、PostgreSQL Compose、CI | 后续仍需真实连续运行、生产部署与更完整检索评测 |

Next.js + React + TypeScript / FastAPI + SQLAlchemy / LangGraph。
详见 [架构与边界](docs/architecture.md)、[API 契约](docs/API_CONTRACT.md)、[数据接入实测](docs/data-access-report.md)、[验证记录](docs/verification.md)。

## 快速启动

需要 Python 3.12+、Node.js 22+。从仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r apps/api/requirements-dev.lock
.venv/bin/python -m pip install --no-deps -e 'apps/api[dev]'
npm --prefix apps/web ci
.venv/bin/python -m modsage.cli init-db
```

分别在两个终端启动：

```bash
.venv/bin/python -m uvicorn modsage.main:app --host 127.0.0.1 --port 8000
```

```bash
npm --prefix apps/web run dev
```

打开 <http://localhost:3000>。API 文档位于 <http://localhost:8000/docs>。
网页通过同源 `/api/v1` 白名单代理访问 API，默认上游为 `http://127.0.0.1:8000`；可在 `apps/web/.env.local` 中设置 `API_BASE_URL`。

Python 依赖由 `apps/api/uv.lock` 及导出的带哈希 requirements 锁定，前端使用 `package-lock.json`。开发、CI 和容器使用相同的锁定依赖；升级时统一更新锁文件。

### 导入真实数据

```bash
# 已知 Workshop ID：使用官方详情 API。
.venv/bin/python -m modsage.cli sync --ids 2976080822

# 明确选择公共页面有限发现，再通过官方详情 API 导入。
.venv/bin/python -m modsage.cli discover --public --limit 50 --sort updated

# 配置 MODSAGE_STEAM_API_KEY 后，可改用官方认证发现。
# .venv/bin/python -m modsage.cli discover --limit 50 --sort updated

# 以当前目录生成日报。
.venv/bin/python -m modsage.cli digest
```

示例 ID 仅用于演示导入方式，并非对其功能、版本或兼容性的推荐。
无数据和外部来源失败时，页面会明确显示状态。生产数据库及原文快照位于忽略目录，不提交到 Git。

### 可选模型配置

在根目录 `.env` 或进程环境中设置：

```dotenv
MODSAGE_LLM_BASE_URL=https://your-provider.example/v1
MODSAGE_LLM_MODEL=your-accessible-model
MODSAGE_LLM_API_KEY=your-private-key
```

地址需提供兼容 `POST /chat/completions` 的服务。项目不会替你提供或猜测真实 key。
配置后运行 `.venv/bin/python -m modsage.cli enrich --help`，按批次生成中文介绍。
模型生成结果必须保留证据引用；界面仍将游戏内中文与兼容性独立显示为未知，除非存在对应事实。

### Docker Compose

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec api python -m modsage.cli discover --public --limit 50
docker compose exec api python -m modsage.cli digest
```

Compose 使用 PostgreSQL，并将所有端口绑定到本机。默认数据库口令仅用于本地开发；公共部署需替换秘密、配置 HTTPS、备份及容量限制。
代码仓库不包含公开托管实例，也不会因为提交代码自动部署线上服务。

## 验证

```bash
.venv/bin/python -m pytest apps/api/tests
.venv/bin/python -m ruff check apps/api scripts
.venv/bin/python -m ruff format --check apps/api scripts
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

浏览器集成测试需要 Chromium，并使用独立的 `data/modsage-e2e.db`。其中的合成条目明确标为 E2E fixture，不会导入默认应用数据库。

```bash
cd apps/web
npx playwright install chromium
npm run test:e2e
```

CI 包含后端、真实 PostgreSQL API smoke、前端构建、Chromium 浏览器集成测试，以及完整 Docker Compose 启动检查。请以 Actions 中具体提交的结果为准。

## 目录

| 路径 | 内容 |
| --- | --- |
| `apps/api` | API、数据模型、Steam 适配器、推荐图、中文生成、CLI 和测试 |
| `apps/web` | Next.js 页面、公开 API 代理、本机清单和浏览器测试 |
| `scripts` | 真实来源探测、隔离 E2E 初始化、PostgreSQL 验证 |
| `docs` | 接口契约、架构、数据实测与交付记录 |
| `.github/workflows` | CI |

## 下一阶段验收

1. 配置实际可用的 Steam QueryFiles key，验证认证发现、分页和字段覆盖。
2. 配置模型并完成中文生成的真实 canary 与人工来源核验。
3. 构建冻结的中文需求评测集，比较关键词、向量及混合检索。
4. 实现持久化后台队列、SSE 恢复、用户账户与通知幂等。
5. 记录连续运行、更新覆盖、失败补偿和实际成本。

ModSage 是独立玩家项目，与 Valve、Creative Assembly 和 Games Workshop 无隶属关系。Mod 内容归其作者及相应权利人所有，保留 Steam 与作者原始链接。
