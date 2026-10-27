# 公开网站（Cloudflare Pages）· 实现契约

目标：**让全世界有这个需求的人搜到并愿意试用**——一个英文优先、中文可切的静态营销站，
放在 `tennis-evolution/site/`，用 Cloudflare Pages 部署。站点只做「讲清楚 + 看 demo + 安装/下载」，
**不提供在线渲染、不接受上传**（逐帧渲染在本机；这也是隐私卖点）。

## 0. 硬规则

1. 纯静态：HTML/CSS/vanilla JS，**无构建步骤、无 npm 依赖、无 CDN、无远程字体**（系统字体栈）。
2. 不出现任何真实球馆/真人素材：只用 `site/assets/` 下的 CC0 demo（Mixkit 免费素材 + 本项目渲染）。
3. 双语：`/` 为英文主站，`/zh/` 为中文（各自独立 URL，互相 `<link rel="alternate" hreflang>`），
   语言切换按钮在两个版本间互相跳转。文案见 §3，英文必须地道、不是机翻味道。
4. SEO 是首要 KPI：标题/描述/结构化数据/sitemap/llms.txt/OG 卡片齐全，见 §4。
5. 首屏不依赖视频加载：hero 先出文字，video 用 `preload="metadata"` + poster。
6. 移动端 390 宽可用；桌面 1440 为主设计尺寸。

## 1. 文件

```
site/
├── index.html          # 英文主站
├── zh/index.html       # 中文
├── assets/             # 由 Lead 提供的 demo 资产（见 §2）
├── styles.css
├── app.js              # 语言跳转、demo 播放器、before/after 切换、FAQ 折叠
├── og.jpg              # 1200×630（Lead 产出）
├── favicon.svg
├── robots.txt
├── sitemap.xml
├── llms.txt
├── _headers            # Cloudflare Pages 响应头
├── _redirects
└── README.md           # 部署说明（Lead 写）
```

## 2. 资产清单（Lead 产出，路径固定）

| 文件 | 规格 | 用途 |
| --- | --- | --- |
| `assets/demo-reel.mp4` | 1280×720 h264, ≈1:39, ≤10 MB, 带角标 | 主 demo 播放器 / hero |
| `assets/demo-reel.jpg` | 1280×720 | 上面那条的 poster |
| `assets/demo-plain.mp4` | 同内容无角标 | before/after 对比 |
| `assets/clip-01.mp4` `clip-02.mp4` `clip-03.mp4` | 1280×720 h264, 6–12 s | 三条「无损切片」卡片 |
| `assets/clip-0*.jpg` | 1280×720 | 对应的 poster |
| `assets/frame-label.jpg` | 1600×900 横版（从 demo 渲染里截的角标画面） | 角标版式细节截图 |
| `og.jpg` | 1200×630 | 社交卡片 |

**这些文件在 Lead 完成前可能不存在**：前端先按固定文件名写 `<video src>`，图片缺失时用 CSS 占位
（`background: #121816`），不要引用不存在的其它资源。Lead 完成后会通知你。

## 3. 文案（英文主站；中文版按意思本地化，不是逐字译）

### Hero
- eyebrow：`OPEN SOURCE · MIT · RUNS ON YOUR MAC`
- H1：`A 47-minute tennis recording in. A 3-minute highlight reel out.`
- 副标：`Tennis Evolution cuts every rally out of a long phone recording, burns the hit count, venue,
  wall clock and source timestamp onto the picture, and checks the result before handing it over.
  Your footage never leaves your machine — no upload, no account, no cloud.`
- 主 CTA：`See a real reel`（锚点到 demo）；次 CTA：`Install in one line`
- 数字条：`47:29 → 3:49` / `5 longest rallies` / `40 hits` / `PASS on 9 checks per file`

### Before / After
标题 `Same footage. The labels do the talking.`
一句说明：`Every segment carries the hit count, the venue, the wall clock of that rally and where it
came from in the source file — so a clip still makes sense once it's out of context.`
切换按钮：`Without labels` / `With labels`（切 `demo-plain.mp4` ↔ `demo-reel.mp4`，保持播放位置）。

### Demo 区
标题 `Watch it, then run it on your own recording.`
三个片段卡片（`clip-01..03`）+ 一句 `These are lossless stream copies — the original HDR 10-bit
frames, cut on keyframes so nothing is re-encoded.`

### How it works（4 步，每步一句工程实话）
1. `Bring a highlight list` — `Any tool that emits time ranges works: sliceapp's export, your own
   detector, or a text file you typed. Detection is deliberately out of scope.`
2. `Cut on keyframes, not on milliseconds` — `Stream copy is bit-exact and keeps HDR, 10-bit and
   rotation metadata; cuts snap to the nearest keyframe, which is why the pipeline never re-encodes a clip.`
3. `Assemble in one pass` — `Rallies are trimmed and concatenated into a single encode. The obvious
   concat-demuxer shortcut produced a permanent 1.01 s audio offset and broken HEVC reference frames,
   so this tool refuses it and reports the problem instead.`
4. `Verify before it counts as done` — `Every output is checked for clean decode, zero start offset,
   A/V duration match, non-black first/last frames and non-silent audio. Failing checks make the
   command exit non-zero.`

### What you get
`reel_labeled.mp4` / `reel_plain.mp4` / `clips/`（无损片段）/ `labels/*.png` / `manifest.json`
一句话：`The manifest is the contract: every parameter and every check result, machine-readable.`

### Why local（四个胶囊）
`Your footage stays on disk` / `No account, no queue` / `Free and MIT licensed` / `Works on HDR iPhone video`

### FAQ（6 条）
- `Do I need a hit detector?` — 不需要，带自己的列表即可；sliceapp 导出的文本直接可用。
- `Does it work with iPhone HDR video?` — HLG/10-bit/rotation 透传；Dolby Vision 的 RPU 在重编码时会丢，HDR10 基础层完好。
- `macOS only?` — CLI 在 macOS/Linux 上跑（需要 ffmpeg + Python 3.11+）；Mac 打包版在计划中，先加入
  邮件列表/Star 仓库。
- `Can I try without installing?` — 站点上有预渲染 demo；在线生成不做，因为要把长时间录像上传到别人的服务器。
- `How long does it take?` — 47 分钟源片、5 个回合，M 系列 Mac 约 3–5 分钟；网页版有实时进度。
- `What does it cost?` — 免费、MIT、开源（GitHub 链接）。

### 页脚
`MIT licensed · built with ffmpeg + Pillow · no telemetry · GitHub` + 版权年份 + 语言切换。

## 4. SEO / 可发现性（必须逐项落实）

- `<title>`（英）：`Tennis Evolution — Automatic Tennis Highlight Reels with Hit Counts (Local, Open Source)`
- `meta description`（≤155 字符）：`Cut every rally out of a long tennis recording, burn hit count,
  venue and timestamp onto the video, and get a verified highlight reel. Local-first, open source, MIT.`
- `<link rel="canonical">` 指向 `https://tennis-evolution.pages.dev/`（域名未定前用这个；Lead 后改）
- `hreflang` en / zh-CN / x-default
- Open Graph + Twitter card（`summary_large_image`，图 `og.jpg`）
- JSON-LD：`SoftwareApplication`（name/description/applicationCategory=MultimediaApplication/
  operatingSystem=macOS,Linux/offers price 0/license MIT/codeRepository GitHub）+ `FAQPage`（英文主站）
- 语义化：唯一 `h1`、每个 section 有 `h2`、图片 `alt`、按钮 `aria-label`
- 目标关键词自然出现在正文与小标题里：`tennis highlight video`、`cut tennis rallies`、
  `tennis rally cutter`、`slice app alternative`、`iPhone tennis video highlights`、
  `HDR HEVC tennis video`、`open source tennis video editor`
- `robots.txt` 允许全部 + 指向 sitemap；`sitemap.xml` 含 `/` 与 `/zh/`
- `llms.txt`：一段机器可读的项目说明 + 关键链接（AI 搜索时代必备）
- 性能：单页 CSS+JS 各一个文件、视频 `preload="metadata"`、图片显式宽高、无字体请求

## 5. 验收

1. `python3 -m http.server` 起本地静态服务，`/`、`/zh/`、`/assets/*` 全部 200。
2. Chrome headless 1440×1100 截图：英文首页、中文首页、demo 播放态；390 宽不塌。
3. 无 console 错误；无外链请求（除 GitHub 链接点击）。
4. `<title>`/description/canonical/JSON-LD/FAQPage 可被解析（注意转义）。
5. 所有视频/图片路径真实存在（Lead 提供后）。
