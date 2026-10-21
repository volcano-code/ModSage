# 每日目录刷新与 worker

v0.2 将目录刷新做成数据库中的持久化任务。API 和 worker 使用同一个 SQLite 或 PostgreSQL 数据库；API 提供状态，独立 worker 执行外部请求。网页进程不会通过定时器自行抓取 Steam。

## 本地执行一次

以下命令从仓库根目录运行。没有 Steam API key 时，必须显式选择有限的公共页面发现：

```bash
.venv/bin/python -m modsage.cli enqueue-refresh --key manual-first-refresh --source public
.venv/bin/python -m modsage.cli worker --once
.venv/bin/python -m modsage.cli jobs --limit 10
```

相同 `--key` 且任务配置相同时返回已存在的任务，不重复排队；相同 key 使用不同配置会明确失败。要在另一时间发起新刷新，应使用新的 key，或省略 key 生成新的任务标识。`worker --once` 最多处理一个可领取任务；队列为空时正常退出。已经完成的同 key 任务不会因再次执行上述命令而重新运行。

默认 `MODSAGE_REFRESH_ENABLED=false`，因此单次 worker 不会自行创建每日任务。这个开关控制自动排队，不会阻止执行用户已经明确排入的任务。

## 启用每日刷新

根目录 `.env` 设置：

```dotenv
MODSAGE_REFRESH_ENABLED=true
MODSAGE_REFRESH_SOURCE=public
MODSAGE_REFRESH_HOUR=8
MODSAGE_REFRESH_MINUTE=0
MODSAGE_REFRESH_LIMIT_PER_SORT=20
MODSAGE_REFRESH_ENRICH_LIMIT=0
```

时间按 **Asia/Shanghai** 解释。然后保持独立进程运行：

```bash
.venv/bin/python -m modsage.cli worker
```

或者通过 Compose 启动可选 worker：

```bash
cp .env.example .env
# 编辑 .env，明确设置刷新开关和来源，再启动。
docker compose --profile automation up --build -d
docker compose logs --follow worker
```

如果有自己可用的 Steam API key，将 `MODSAGE_REFRESH_SOURCE` 设为 `api`，并设置 `MODSAGE_STEAM_API_KEY`。`api` 是默认来源；缺 key 或认证接口失败会记录失败，不会静默切换到公共页面。

每日任务使用上海日期生成幂等键。当天计划时间已经过去、worker 才启动时，补排当天任务；不追补长期停机期间的所有历史日期。计划开启并不证明 worker 在运行，状态接口另外报告最近心跳。

## 一次任务做什么

1. 有上限地发现最近更新、最新发布和热门候选，合并并去重 Workshop ID。
2. 通过官方详情接口校验游戏、公开状态和 Mod 类型，写入目录、原始快照与指标记录。
3. 可选地生成有限数量的中文介绍。默认数量为 0，不自动产生模型调用。
4. 根据当前收录目录更新当日日报，保存任务完成状态和计数。

公开页面发现受页面结构、可访问性和限流影响；一次任务的成功只代表这一有限批次执行完成。热门候选和站内订阅数排序不等于全站完整榜单。没有任何来源返回有效候选时，不把空结果伪装成刷新成功，也不清空原目录。

## 执行与恢复语义

| 机制 | 行为 |
| --- | --- |
| 持久化任务 | 排队、阶段、次数、计数和安全错误码保存在数据库，进程重启后仍可查询 |
| 幂等键 | 数据库唯一约束防止相同任务重复入队 |
| 原子领取 | 通过数据库条件更新竞争任务和目录租约，多 worker 不同时刷新同一目录 |
| 租约与心跳 | 正在执行的 worker 定期续租；超过有效期后任务允许恢复 |
| 所有权校验 | 每次阶段写入核对租约 token，失去租约的旧 worker 不能继续提交结果 |
| 阶段检查点 | 已完成的数据写入与相应阶段记录一起提交，重试从检查点恢复 |
| 有限重试 | 临时错误进入等待重试；达到最大次数后终止，避免无限请求上游 |
| 请求预算 | 外部流程有总时间预算；超时是可见的失败状态 |

网络调用不占用长数据库事务。目录写入保留已有内容哈希与原文快照去重；指标仍按日期归档。该设计提供可恢复的任务执行，不承诺跨网络和数据库的“恰好一次”外部请求：进程在外部请求后、提交前崩溃时，请求可能重发。

`succeeded` 表示所配置阶段成功；`partial` 表示只完成了部分工作，不推进“最后完整刷新成功时间”。`failed` 不代表原有目录失效或已被删除。首页的最旧条目采集时间与这里的最后任务成功时间含义不同，应分别理解。

## 状态接口

`GET /api/v1/sync/status` 返回：

```json
{
  "latest_job": null,
  "last_successful_refresh_at": null,
  "schedule": {
    "enabled": false,
    "timezone": "Asia/Shanghai",
    "time": "08:00",
    "next_run_at": null
  },
  "worker": {
    "status": "unknown",
    "last_seen_at": null
  }
}
```

任务存在时，`latest_job` 含有 `id`、`status`、`stage`、`source`、重试次数、创建/开始/结束/下一次重试时间、安全错误码和 `counts`（`discovered`、`imported`、`enriched`）。接口不返回凭据、原始上游响应、租约 token 或幂等键。

worker 状态有 `recently_seen`、`stopped`、`stale`、`unknown`。最近心跳只是一个带时间的观察值，不能视为进程此刻仍存活的保证。`schedule.next_run_at` 表示计划时间，不是对任务一定按时完成的承诺。

## 配置

所有变量使用 `MODSAGE_` 前缀；下表省略该前缀。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `REFRESH_ENABLED` | `false` | 是否自动排入每日任务 |
| `REFRESH_SOURCE` | `api` | `api` 或显式 `public` |
| `REFRESH_HOUR` / `REFRESH_MINUTE` | `8` / `0` | 上海时区的计划时间 |
| `REFRESH_LIMIT_PER_SORT` | `20` | 每种发现排序的候选上限 |
| `REFRESH_ENRICH_LIMIT` | `0` | 单次可生成中文介绍的条目上限 |
| `REFRESH_MAX_ATTEMPTS` | `3` | 单任务最大执行次数 |
| `REFRESH_LEASE_SECONDS` | `60` | 目录与任务租约时长 |
| `REFRESH_REQUEST_BUDGET_SECONDS` | `180` | 单次执行的外部请求总预算 |
| `REFRESH_POLL_SECONDS` | `15` | 常驻 worker 检查任务的间隔 |

开启摘要生成前，应先配置并验证模型服务，再设置有限的 `REFRESH_ENRICH_LIMIT`。当前版本没有按用户发送邮件、站内通知或消息推送；日报构建与个人通知是不同的功能。推荐请求仍使用独立的有界同步流程，本次没有将推荐改成 SSE 异步任务。

## 运维边界

新表使用现有的 `create_all` 初始化，没有修改既有目录表的列定义。后续 schema 变更仍需要正式迁移机制。一次 worker 验证、单元测试或 CI 通过不能替代多日运行观察；真实认证发现、真实模型摘要及长期失败率须在有对应环境后另行记录。
