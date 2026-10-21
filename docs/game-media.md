# v0.3 官方游戏画面与来源

本轮按用户要求，将界面的主要氛围素材改为《全面战争：战锤 3》的真实官方宣传画和官方游戏截图。以下文件来自发行方的 Steam 商店素材，并非 AI 生成图、ModSage 自绘素材或社区 Mod 的效果截图。

检索与下载日期：**2026-10-21（北京时间）**。

## 来源核对

- [《Total War: WARHAMMER III》官方 Steam 商店](https://store.steampowered.com/app/1142710/Total_War_WARHAMMER_III/)：商店列出开发商 Creative Assembly、发行商 SEGA，并提供本体宣传画和截图。图片地址取自该应用的 [公开商店元数据](https://store.steampowered.com/api/appdetails?appids=1142710&l=english)，使用实际返回的 `background_raw` 或 `screenshots[].path_full`，没有猜测 CDN 文件名。
- [《Elspeth – Thrones of Decay》官方 Steam 商店](https://store.steampowered.com/app/2325832/Total_War_WARHAMMER_III_Elspeth_Thrones_of_Decay/)：明确说明 Elspeth von Draken 为帝国传奇领主，并介绍其努恩火器机制。图片地址取自该 DLC 的 [公开商店元数据](https://store.steampowered.com/api/appdetails?appids=2325832&l=english)。

## 文件清单

文件均位于 `apps/web/public/media/warhammer3/`，格式为 JPEG，保留官方原始下载字节，未重绘、拼接、去除版权角标或改变内容。各画面已逐张查看后选择。网页可通过 Next.js Image 提供响应式尺寸和格式；这不改变其官方来源属性。

| 文件 | 来源与可确认画面 | 尺寸 | 字节数 | 界面用途 |
| --- | --- | --- | ---: | --- |
| `official-key-art.jpg` | 本体商店背景宣传画：冰雪中的基斯里夫军阵与恐虐恶魔对峙 | 1438 × 809 | 1,213,932 | 首页主视觉；这张是官方宣传插画，不标为实机截图 |
| `kislev-ice-court.jpg` | 本体官方截图：冰雪女王、冰霜施法者、雪豹与基斯里夫军阵 | 1920 × 1080 | 633,259 | 基斯里夫派系入口或推荐页画面 |
| `cathay-battle.jpg` | 本体官方截图：震旦城防前的军阵交战、天灯与巨型石像兵 | 1920 × 1080 | 797,924 | 震旦派系入口 |
| `khorne-battle.jpg` | 本体官方截图：熔火战场中的恐虐军阵与巨型恶魔 | 1920 × 1080 | 700,371 | 混沌相关视觉或探索横幅 |
| `empire-elspeth.jpg` | Elspeth DLC 官方截图：艾斯佩斯与身后的帝国火枪军列 | 1920 × 1080 | 266,507 | 帝国派系入口；不称为火炮画面 |

合计：3,611,993 字节（约 3.44 MiB）。主视觉人物与恶魔集中在画面中部及右侧；文字遮罩应保留这些主体可辨，移动端优先保留关键角色。

### 原始文件地址

1. [官方主视觉](https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1142710/page_bg_raw.jpg?t=1790321531)
2. [基斯里夫截图](https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1142710/ss_086d9d777d14d64c9e8bc1713b088381033dfeeb.1920x1080.jpg?t=1790321531)
3. [震旦截图](https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1142710/ss_6149b0570c42cda0e40b4de71e6f48bbe5c4c577.1920x1080.jpg?t=1790321531)
4. [恐虐截图](https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1142710/ss_a55fad2780446067c6eb8079c92a5bd218e1b542.1920x1080.jpg?t=1790321531)
5. [帝国艾斯佩斯截图](https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/2325832/ss_3bbb7d04dc4007ef7e42e3c2bfcee4435a459334.1920x1080.jpg?t=1790001962)

### 完整性记录

| 文件 | SHA-256 |
| --- | --- |
| `official-key-art.jpg` | `c2b1a052cbe3f854bf12ad18d5a97676bc46e6072ab17b205bf75bc5caa25fa0` |
| `kislev-ice-court.jpg` | `7240597b78718312577f4fb55e59903a246d7132aea76d3e34281c8f59f2947d` |
| `cathay-battle.jpg` | `d9d1b78ab609cf6b08caedfaee7971a70115f6c1f7cac2f02c41dde8ed53864e` |
| `khorne-battle.jpg` | `29efe45b4d10182c1175f870c57fd023f2e981e90daf39e7f07e095f19db946c` |
| `empire-elspeth.jpg` | `4a22ca645704f0dd69ad495361ac7cd1efb5e095eaae3cab603c1bd93c79e85f` |

## 展示与权利归属

游戏画面及其人物、世界观、标识的权利归 Games Workshop、Creative Assembly、SEGA 等相应权利人所有；官方商店页提供了完整权利声明。它们**不属于本项目的原创资产，也未被当作开放许可素材**。仓库代码适用的许可不授予这些游戏图片的额外权利。

界面应提供可见的“官方游戏画面”来源链接，并保持 ModSage 为独立玩家项目的说明。派系入口中的官方画面用于说明游戏主题，不能证明某个 Mod 包含画面中的单位、付费内容或效果。具体 Mod 卡片继续使用该条目的 Workshop 预览及作者来源；没有预览时使用明确的缺图状态，不以官方游戏图补造 Mod 效果。

这些图片是静态游戏视觉资产，不代表最新补丁状态。显示游戏内中文、前置依赖、版本兼容性和 Mod 功能时，仍须依据各条目的来源记录。
