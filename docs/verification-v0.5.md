# v0.5 验证记录

日期：2026-10-22（北京时间）。本轮完成真实 API key 接入前的配置、协议、canary、调用次数额度、使用记录、公开状态与运行文档；没有配置或调用真实模型服务。

前一轮记录保留在 [verification-v0.4.md](verification-v0.4.md)。真实服务接入说明见 [provider-readiness.md](provider-readiness.md)。

## 本地最终验证

| 检查 | 实际结果 |
| --- | --- |
| Python 后端全量回归 | **349 passed，40.04 秒** |
| Python 语句覆盖率 | **92%**；provider usage 98%，provider checks 92%，provider HTTP 90% |
| Ruff / 格式 / diff 空白检查 | 通过，48 个 Python 文件格式一致 |
| ESLint / TypeScript | 通过 |
| Next.js 16.4.2 production build | 通过 |
| Playwright 浏览器集成 | **15 passed，25.1 秒** |
| CLI + 真实 loopback HTTP 契约 | 通过，4 次模拟模型 HTTP 请求、0 次真实提供方调用 |
| 冻结检索评测 | 通过；BM25 Recall@5=0.92、nDCG@5=0.88997、MRR@5=0.9，保持原基线 |
| 真实目录副本桌面/手机视觉核验 | 通过，1440×1100 / 390×960，无横向溢出、页面 JS 异常或横幅加载失败 |

运行环境为 Python 3.12.14、Node 24.19.0。Playwright 官方下载在本执行环境返回了损坏归档，因此本地使用 Google 官方稳定版 Chrome 156.0.8078.47；CI 继续使用 Playwright 管理的 Chromium。中文字体仅补充在本地截图环境，产品和容器 TLS 检查未关闭。

## 新增的关键覆盖

1. **配置与协议**：空/部分字段、首尾空白、非法 URL、内嵌凭证、误填操作路径、端口、JSON 输出、截断、无效 usage、响应体上限、整个请求超时、重定向拒绝、取消与默认漏记账调用拒绝。
2. **敏感数据**：上游在模型名或请求 ID 中回显密钥时清除；日志和健康响应不暴露配置值、URL、请求文本；无效 Unicode 不进入最终 UTF-8 业务响应；无效 key 的公开状态不尝试计算可失败的编码指纹。
3. **跨进程额度**：四个独立连接和三个独立进程争用最后一个配额；失败、取消、无回执保持消耗；UTC 跨日、滚动分钟与等待锁后的窗口校验；数据库错误时拒绝调用。
4. **真实图与降级**：配额耗尽时实际执行推荐图，LLM 不发 HTTP 且 engine=rules；有向量但 embedding 受限时不发 HTTP 且 method=bm25，仍保留来源目录候选。
5. **严格 canary**：缺 --live、缺 key、空目录、额度不足、0 次工作、规则回退、未进入 hybrid、业务约束错误、摘要引用错误、Steam 空/错误类型/分页越界，都不能报验收成功。
6. **运行隔离**：CLI 子进程通过 HTTP fixture 实际完成 LLM 与 embedding canary，达到共享四次配额后阻断新调用；检查原测试目录的摘要、向量、推荐任务和推荐结果没有被修改。
7. **前端**：未配置、字段完整但无调用记录、最近成功、失败、调用暂停分开显示；不把 configured 当作模型可用。原有幂等提交、SSE 断点恢复、响应丢失恢复、取消、重试、历史和清单保持通过。

两项交叉审查修复已进入回归：返回 JSON 中的未配对 Unicode surrogate，以及 canonical embedding alias 变化后的旧索引复用。

## 真实目录与视觉证据

没有为这一轮添加合成生产 Mod，也没有新采集来源。使用已有 **101 条真实 Workshop 条目、18 条中文介绍** 的独立数据库副本，显式关闭 LLM/embedding/Steam key 与模型调用后进行视觉验证。隔离副本内提交“想玩震旦，增加新兵种，不要大型重做”完成推荐；原应用数据库的摘要、队列和向量均未被这次视觉测试修改。

- [桌面参谋状态](screenshots/provider-v5-desktop.jpg)
- [手机参谋状态](screenshots/provider-v5-mobile.jpg)

截图保留官方战锤游戏横幅、猩红军旗、古铜边框和羊皮纸输入区。官方画面与具体 Mod 的预览/来源信息仍分开处理，来源见 [game-media.md](game-media.md)。页面中的“尚未配置”“尚无调用记录”来自真实接口，不是为截图伪造的成功状态。

## 远端 CI 的检查内容

本次工作流新增 provider loopback 契约步骤、PostgreSQL 共享调用准入验证，以及 API 和推荐 worker 容器里的 preflight/usage 检查。四个 job 分别运行后端/PostgreSQL、前端构建、浏览器、完整 Compose。

远端是否成功以本版本具体提交对应的 [GitHub Actions](https://github.com/volcano-code/ModSage/actions/workflows/ci.yml) 为准；本文件中的本地通过结果不代替远端状态，也不引用旧提交的绿灯。本轮执行结束时在交付回复中提供准确提交与运行链接。

## 未执行与边界

- **没有真实 LLM / embedding / Steam 认证 canary 结果**：缺少用户自行配置的凭证；工具已可运行，CI 全部使用明确的受控 fixture。
- 中文生成的语义忠实度和游戏内汉化/兼容性不由 JSON 或引用校验自动证明，仍需要来源核验和游戏内验证。
- 模型额度是共享调用次数，不是自动计算的货币账单；缺失 token 明确保持未知。没有证据支持真实成本或质量提升结论。
- 当前配额值由进程启动环境提供，修改后必须共同重启 API、CLI 环境和所有 worker。
- 没有运行公网生产部署、真实多日任务、生产数据库备份恢复或规模压测；运行手册提供具体操作，未把模板写成已完成的生产验证。
- 账户、跨设备数据、通知、正式数据库列迁移、独立标注评测仍是独立产品演进项，不是第一次配置 key 的前置阻塞。

## 可重现命令

```bash
.venv/bin/python -m pytest apps/api/tests --cov=modsage
.venv/bin/python -m ruff check apps/api scripts
.venv/bin/python -m ruff format --check apps/api scripts
.venv/bin/python scripts/evaluate_retrieval.py --check
.venv/bin/python scripts/provider_contract_smoke.py
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
cd apps/web
npm run test:e2e
```

本地 XML、冻结评测、loopback 和视觉 JSON 报告保存在忽略的 artifacts 目录；远端 CI 上传测试报告工件。仓库不保存 Key、数据库或完整作者快照。
