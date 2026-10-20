# Steam 数据接入验证记录

验证时间：**2026-10-20 09:01 UTC（北京时间 17:01）**。
范围：`appid=1142710`，公开 Workshop 最近更新列表的小规模分页发现，以及 Steam 官方详情接口。

## 已实际执行的检查

1. 从公开 Workshop 列表的真实 `<a>` 链接发现最多 50 个条目 ID。
2. 调用 `POST /ISteamRemoteStorage/GetPublishedFileDetails/v1/` 批量读取详情，无需提供本项目 API key。
3. 校验所属游戏、公开状态、返回结果和 Mod 类型；过滤无关或无法确认的条目。
4. 保存 ID 列表与字段完整率到被 Git 忽略的 `data/` 目录。未提交作者全文或运行数据库。

| 检查项 | 实际结果 |
|---|---:|
| 发现的候选 ID | 50 |
| 接受的有效 Mod | 49 |
| 保守跳过 | 1 |
| 有标题、描述的有效 Mod | 49 / 49 |
| 有发布时间、更新时间的有效 Mod | 49 / 49 |
| 有当前订阅数、收藏数的有效 Mod | 49 / 49 |
| 有允许的 HTTPS Steam 图片地址 | 49 / 49 |
| 返回完整前置关系字段 | **0 / 49** |

跳过的条目 `3739994254` 在旧详情接口中未返回 `file_type`，也没有可确认的 `mod` 标签。因此适配器没有把它自动归类为 Mod。这是保守过滤，不表示该条目被删除或不兼容。

## 关键结论与限制

- 已验证的数据路径是 **`public_html_fallback` + 官方详情 API**。公开 HTML 发现只覆盖实际读取的页面，不承诺全站覆盖，也不把其结果包装成经过认证的官方排名。
- `IPublishedFileService/QueryFiles` 已实现，配置 `MODSAGE_STEAM_API_KEY` 后使用官方发现路径。本次没有该 key，因此**未实测认证查询**。
- 认证请求使用官方支持的 `x-webapi-key` 请求头，避免 key 出现在普通 URL 访问日志中；异常及同步记录不会包含原始凭据或服务响应。
- 旧详情接口实际提供 `subscriptions` 和 `favorited`；这两项是当前计数，不能当成近期新增量。
- 本次旧详情响应未提供 `children` 或等效完整关系字段。`required_ids=[]` 必须与 `dependencies_known=False` 一起理解，不能解释为“没有前置”。
- 适配器同时保存 `SteamItem.dependencies_known` 与 `raw['_modsage_dependencies_available']`，供同步逻辑保留此前可信关系并提示信息缺口。
- 作者字段当前是来源返回的 Steam 创建者 ID，不是通过额外用户 API 解析出来的展示名。
- 详情描述被清洗为纯文本；不会根据页面中出现的 `incompatible` 或 `removed` 模板字符串判断兼容性或下架。
- 本次只完成一次小规模实测，**未完成连续三天运行、长期稳定性、中文生成、实际游戏加载或存档兼容验证**。

## 可复现命令

在安装项目 Python 依赖后运行：

```bash
PYTHONPATH=apps/api python scripts/steam_probe.py --limit 50
```

默认写入：

- `data/probe_ids.json`：本次被接受的真实 Workshop ID。
- `data/steam_probe_report.json`：观察时间、来源模式、统计与限制。

如果已配置项目 Steam key，同一脚本自动改用 `QueryFiles` 进行发现。网页结构改变、HTTP 错误或 API 返回异常会明确失败，不会返回伪造的成功数据。

## 自动化测试

适配器测试使用标明为合成数据的 HTTP mock，不会注入产品目录。覆盖：ID 校验、批量与去重、跨游戏/非公开/非 Mod 过滤、游标分页、前置信息缺失、HTML 结构变化、429 有界重试、长 `Retry-After` 的退出、坏 JSON 包装、合集成员与依赖分离、客户端所有权和不安全预览 URL。HTTP 200 内的超时、服务不可用等逐项错误，以及整批被省略的响应，均会明确失败；单个条目未找到或拒绝访问会单独记录警告。

运行：

```bash
cd apps/api
pytest tests/test_sources.py
```

本轮适配器实现完成时，**32 个测试通过**。后续测试结果以 CI 和最新提交为准。

## 官方参考

- [ISteamRemoteStorage](https://partner.steamgames.com/doc/webapi/ISteamRemoteStorage)
- [IPublishedFileService](https://partner.steamgames.com/doc/webapi/IPublishedFileService)
- [Steam Web API 认证](https://partner.steamgames.com/doc/webapi_overview/auth)
- [Steam EResult 错误码](https://partner.steamgames.com/doc/api/steam_api#EResult)
- [WARHAMMER III Workshop](https://steamcommunity.com/app/1142710/workshop/)
- [Steam Web API Terms of Use](https://steamcommunity.com/dev/apiterms)
