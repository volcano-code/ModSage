# ModSage 美术资产说明

## 当前版本：v0.3 官方游戏画面

当前页面已改用真实的《全面战争：战锤 3》官方宣传画和游戏截图，清单、来源与权利说明见 [game-media.md](game-media.md)。首页三组场景可手动切换，帝国、震旦和基斯里夫的官方画面同时承担派系搜索入口。移动端将画面与文案分区，避免角色主体被遮挡。各 Mod 卡片仍使用对应 Workshop 的真实预览。

下方保留 v0.2 的生成插画制作记录。这张素材不再被当前页面引用，不能将它描述为真实战锤画面。

## v0.2 历史原创战役主视觉

- 文件：`apps/web/public/media/old-world-citadel-v2.webp`
- 页面地址：`/media/old-world-citadel-v2.webp`
- 尺寸：1672 × 941 像素，接近 16:9
- 网页格式：WebP
- 网页文件大小：135,284 字节（约 132 KiB），原始生成图为 2,490,727 字节
- 创建日期：2026-10-20
- 制作方式：内置 `image_gen__imagegen` 工具单次生成；网页使用 Next.js Image 已生成并经过页面验收的全尺寸 WebP 导出。没有改变插画构图或增删画面元素。
- 来源：为本项目生成的原创画面；没有使用外部图片作为参考或复制官方宣传素材。画面不是游戏截图、官方宣传图或真实 Mod 预览图。
- 历史用途：v0.2 网站首页战役氛围背景；不用于证明 Mod 内容、推荐结果或游戏数据。v0.3 已停止页面引用。

### 视觉方向与接入

右侧为帝国式哥特城堡、猩红军旗、城门火盆和远处军阵，少量飞行生物剪影强调暗黑奇幻战役氛围。左侧以阴云、山谷、薄雾和暗部提供文案空间。整体使用旧金暮光、灰黑铁石、猩红织物和暖灰雾色，让城堡与军旗在阴影中仍可辨识。

首页以左文右图布局使用这张背景。请以 CSS 渐变处理文案底部对比度，避免直接降低整张图的亮度；图中的城堡和军旗是右侧视觉焦点。项目保存全尺寸网页资源，Next.js Image 仍按屏幕尺寸生成响应式版本。窄屏裁切应保留右侧主体；作为纯装饰背景时使用空 `alt`，避免读屏器重复播报装饰内容。

### 最终生成提示词

```text
Use case: stylized-concept
Asset type: original panoramic hero background for ModSage, a Chinese-language medieval dark-fantasy campaign archive and mod discovery website.
Create a masterful, richly detailed original dark fantasy painting with the atmosphere of the Old World and the human Empire in medieval Warhammer: a monumental, battle-scarred gothic imperial citadel dominates the RIGHT side, clustered soaring narrow towers, steep slate roofs, flying buttresses, fortified walls, iron spikes and an elevated gate. Deep crimson war banners with simple abstract gold geometric embroidery hang from towers and stream in the wind; no recognizable logos or heraldic trademarks. Far below, a small distant disciplined host of iron-armored soldiers with long spears and scattered warm torches marches toward the fortress. A few glowing braziers and distant fires punctuate the walls. The setting is a war-torn European medieval fantasy mountain frontier under a smoky dusk sky.
Composition: very wide cinematic panoramic frame, ideally 16:9 or wider. The fortress architecture and striking crimson banners occupy the RIGHT 60 percent, brightest focal detail around the upper-right middle, enough breathing room above the spires. The LEFT 40 percent is intentionally restrained, low-contrast charcoal storm clouds and shadowed rolling terrain fading into warm grey parchment-colored mist, for readable overlaid Chinese website copy. Keep the left foreground dark and uncluttered, without a prominent subject or bright fire. Natural painterly transition between left and right; do not make a blank flat rectangle. Strong readable silhouette, grand scale, deep atmospheric perspective. A subtle far-distant griffon-shaped flying silhouette near the right skyline is optional.
Style: premium dark-fantasy game environment key art with classical oil painting richness, controlled brush texture, beautifully described weathered stone, battered iron, worn cloth, warm ash, believable architectural perspective. Clearly a painting, not a photograph, not a generic 3D render. Somber and martial, ancient and storied, not cheerful or cartoonish.
Lighting and palette: diffuse warm parchment sunset breaking through the cloud behind the right-side fortress, readable illuminated stone edges and faded old-gold metal, crimson banners distinct against smoky slate and charcoal greys, restrained ember orange. Shadow detail must remain visible; do not crush the whole scene into black. No saturated cyan, electric violet or neon.
Constraints: image only; absolutely no words, lettering, pseudo-text, numbers, UI, borders, title, watermark, signature, official emblems or logos. No modern guns, tanks, science-fiction technology, modern buildings, photographic people, close-up faces or giant centered hero. Original artwork, no copied official promotional composition.
```

### 生成后检查记录

已检查生成图和项目中的网页资源：城堡、军旗、军阵及暮色光线可辨，左侧存在连续暗部；没有文字、界面元素或官方标识。图片按约定路径进入仓库，前端无需依赖临时生成目录。
