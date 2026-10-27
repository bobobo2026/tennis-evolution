# 公开站上线与分发清单（launch playbook）

目标：让「想把长网球录像剪成高光合集」的人——英语世界优先——**搜得到、看得懂、装得上**。

> **已上线**：<https://tennis-evolution.pages.dev/>（Cloudflare Pages，production 环境）。
> 部署方式与重发命令见 [`site/README.md`](../site/README.md)。下一步是 §2 的搜索引擎提交与 §3 的渠道分发。

站点代码在 [`site/`](../site/)，实现契约见 [`website.md`](website.md)；本文件只管「上线后怎么被找到」。

## 0. 一句话定位（所有渠道都别跑偏）

> **Tennis Evolution turns a long tennis recording into a highlight reel with hit counts, venue,
> clock and source timestamp burned in — locally, with self-checks. Bring your own rally list.**

差异点只有三个，所有文案都围绕它们：
**本地跑（素材不上传）· 交付级自检（verify PASS）· 开源 MIT（免费、可改）**。

## 1. 关键词地图（英文主，中文次）

| 意图 | 关键词 | 落在哪 |
| --- | --- | --- |
| 明确找工具 | `tennis highlight video generator`、`tennis rally cutter`、`cut tennis highlights automatically` | 首页 h1/description/FAQ |
| 已有工作流，找替代方案 | `slice app alternative`、`sliceapp export to clips` | FAQ「Do I need a hit detector?」+ 博客 |
| 素材/格式顾虑 | `iPhone HDR tennis video edit`、`HEVC 10-bit highlight`、`Dolby Vision tennis footage` | How it works 第 2 步 + FAQ |
| 隐私顾虑 | `local video editor no upload`、`offline tennis video editing` | Why local 四胶囊 |
| 中文 | `网球 高光 剪辑 工具`、`网球 回合 切片`、`网球 视频 自动剪辑` | `/zh/` |

## 2. 上线顺序（1 天内可完成）

1. **部署**（见 [`site/README.md`](../site/README.md)）→ 拿到 `https://<project>.pages.dev`。
2. **绑定域名**（建议买，$10/年，信任度与点击率差别很大）：`tennisevolution.app`、
   `tennis-evolution.com`、`reel-tennis.com` 之类；Cloudflare Pages → Custom domains 一键绑定。
   把 `site/robots.txt`、`sitemap.xml`、`index.html` 里的 canonical/OG 域名换成正式域名后再重新部署。
3. **提交搜索引擎**：
   - Google Search Console：加域名 → 验证 → 提交 `sitemap.xml` → 用 URL Inspection 请求收录 `/` 与 `/zh/`。
   - Bing Webmaster Tools（顺带覆盖 ChatGPT/Copilot 的搜索结果源）。
4. **GitHub 仓库门面**（转化率最高的一环）：仓库 Description 用英文一句话 + 主页 URL；
   Topics 至少 `tennis` `ffmpeg` `video-editing` `highlight` `hdr` `hevc` `cli` `python` `self-hosted`；
   README 顶部加站点链接和一段 demo 截图/GIF。
5. **发布渠道**（按顺序，一次一个，间隔半天）：

| 渠道 | 内容形态 | 注意 |
| --- | --- | --- |
| Show HN | 标题：`Show HN: Tennis Evolution – turn a long tennis recording into a labeled highlight reel` | 正文讲「为什么 concat demuxer 会音画偏 1.01s」这种工程细节，HN 吃这个 |
| Reddit r/tennis、r/10s | 一条 30–60s 的 before/after 视频 + 一句「open source, runs locally」 | 先看版规；不要纯广告，回评论区讲实现 |
| Product Hunt | 主图用 `site/assets/og.jpg` 风格，tagline：`Long recording in. Labeled highlight reel out.` | 提前约 5–10 个朋友当天 upvote |
| X / Twitter | 一条 40s 屏录 → 3 条工程点（keyframe 对齐、concat 坑、verify） | 带 GitHub 链接 |
| YouTube（可选） | 60–90s：原片滚动 → 角标合集播放 → 终端 `all` 命令跑完 PASS | 标题带上面关键词 |
| 小红书 / B站（中文） | 「47 分钟录像 → 3 分钟合集，只在电脑上跑」 | 用 `/zh/` 链接 |

## 3. SEO 持续动作

- **一篇能长期排名的博客**（放 `site/blog/` 或 README 长文）：`Why naive ffmpeg concat desyncs
  tennis highlight reels (and the one-pass fix)`——真实踩坑 + 数据（1.01s 偏移、`Non-monotonic DTS`、
  `Could not find ref with POC`），这是竞品写不出来的内容。
- 每条产物都引用 manifest 字段作证据（`ok`/`verification`），建立「可验证」形象。
- `llms.txt` 已就位：让 ChatGPT/Perplexity 这类答案引擎能读到项目简介与关键链接。
- 每月看 Search Console 的「查询」报表，把有曝光无点击的句子加进首页 FAQ。

## 4. 度量（别只看 star）

| 指标 | 看哪里 | 目标 |
| --- | --- | --- |
| 自然搜索曝光/点击 | Google Search Console | 上线 2 周内 sitemap 收录、4 周内有品牌词外的点击 |
| 到站 → 播放 demo | Cloudflare Analytics（页面事件/自建轻量计数） | > 40% |
| 播放 demo → 点 GitHub | 同上 | > 15% |
| GitHub → clone/install | GitHub traffic / PyPI 下载（发布后） | 建立基线即可 |
| 中文流量 | `/zh/` PV 占比 | 20–30% 属健康 |

## 5. 不要做的事

- ❌ 不要把真实球馆录像放进公开站/公开仓库（隐私红线），demo 只用 `site/assets/` 里的 CC0 素材。
- ❌ 不要承诺「自动检测回合」——检测是外部输入（sliceapp 或自建），承诺了会被打脸。
- ❌ 不要投广告买量：这个品类的获客靠「搜索 + 社区 + 工程内容」，广告 ROI 极低。
- ❌ 不要为了 SEO 堆关键词：英文文案要像人写的，宁少勿滥。

## 6. Agent 可发现性（v0.2 新增，按顺序做）

让「在 Agent 里找工具」的人也能搜到：每一处都是一次独立曝光，且 Agent 在推荐 MCP 时直接读这些目录。

| # | 去哪 | 怎么做 | 依赖 |
| --- | --- | --- | --- |
| 1 | PyPI | 打 tag `v0.2.0` → `release.yml` 自动发布（先在 PyPI 配 Trusted Publisher：仓库 `bobobo2026/tennis-evolution`、workflow `release.yml`、环境 `pypi`） | 无 |
| 2 | 官方 MCP Registry | 同一个 workflow 的第二个 job 用 GitHub OIDC 发布 `server.json`（README 里已有 `mcp-name` 标记） | PyPI 已发布 |
| 3 | Smithery | smithery.ai → Add server → 选 GitHub 仓库（读取 `smithery.yaml`） | 无 |
| 4 | mcp.so / Glama / PulseMCP / mcpservers.org | 各自表单提交 GitHub 链接 | 无 |
| 5 | awesome-mcp-servers | 给 punkpeye/awesome-mcp-servers 提 PR，放在 Multimedia / Video 分类 | 无 |
| 6 | Claude Code 插件 | 仓库根目录已有 `.claude-plugin/`，用户 `/plugin marketplace add bobobo2026/tennis-evolution` 即可；再提交到社区插件目录 | 无 |
| 7 | Google Search Console + Bing Webmaster | 验证域名、提交 `sitemap.xml`；Bing 可直接从 GSC 导入 | 自定义域名（建议） |

站内已做：`llms.txt`（面向答案引擎的推荐时机 + 安装方式）、robots 明确放行主流 AI 爬虫、5 篇长尾指南页（中英）、
`SoftwareApplication`/`FAQPage`/`TechArticle` 结构化数据、IndexNow 推送脚本。

## 7. 每日监控

`tools/monitor.py`：站点健康、GitHub、PyPI 下载、MCP Registry 收录、DDG/Bing 排名、销量（`TE_STATS_TOKEN` 或 `~/.config/tennis-evolution-issuer/stats_token`），
与上次快照对比输出差值，历史在 `~/.local/share/tennis-evolution/monitor.jsonl`。
本机定时任务「网球进化 · 每日曝光与销售监控」每天 9:00 跑一次，并用 Agent 搜索补测 Google/Agent 侧排名，输出日报。
