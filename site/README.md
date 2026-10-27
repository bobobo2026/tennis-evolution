# site/ —— 公开网站（Cloudflare Pages）

英文主站 `/` + 中文 `/zh/`，纯静态（无构建、无依赖）。文案与 SEO 契约见
[`docs/website.md`](../docs/website.md)，上线与分发见 [`docs/launch.md`](../docs/launch.md)。

## 本地预览

```bash
cd site
python3 -m http.server 8890
# http://127.0.0.1:8890/     英文
# http://127.0.0.1:8890/zh/  中文
```

## 部署（两种方式，选 A 更省事）

### A. Cloudflare Pages + Git 集成（推荐，推代码即发布）

1. Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git → 选
   `bobobo2026/tennis-evolution`。
2. 构建配置：**Framework preset = None**、**Build command 留空**、**Build output directory = `site`**。
3. Save and Deploy → 得到 `https://<project>.pages.dev`。
4. 以后 `git push` 到该分支即自动重新部署。

### B. 本地直接上传（改动一次传一次）

```bash
# 首次需要登录（会开浏览器）
npx wrangler login
# 部署
npx wrangler pages deploy site --project-name tennis-evolution --commit-dirty=true
```

`_headers`（缓存与安全头）和 `_redirects` 会被 Pages 自动识别。

> 本机实测：`wrangler login` 走在浏览器里可能被 Cloudflare 的「Just a moment…」人机校验卡住。
> 这种情况下直接用 OAuth access token 走非交互部署即可（token 在 `~/Library/Preferences/.wrangler/config/default.toml`
> 的 `oauth_token` 字段，有效期 1 小时，过期重新 login 一次）：
>
> ```bash
> export CLOUDFLARE_API_TOKEN="$(grep -m1 '^oauth_token' ~/Library/Preferences/.wrangler/config/default.toml | sed -E 's/.*"(.*)"/\1/')"
> export CLOUDFLARE_ACCOUNT_ID=2565a4a85f810c6c0bbc1108c81f03c4
> npx wrangler@4 pages deploy site --project-name=tennis-evolution --branch=main --commit-dirty=true
> ```

## 绑定自定义域名

1. 买一个短域名（`tennisevolution.app` / `tennis-evolution.com` 之类）。
2. Cloudflare Pages → 项目 → Custom domains → 填域名 → 按提示加 CNAME。
3. 把下面这些地方的 `https://tennis-evolution.pages.dev` 换成正式域名，然后重新部署：

```bash
grep -rl "tennis-evolution.pages.dev" site/ | xargs sed -i '' 's#https://tennis-evolution.pages.dev#https://你的域名#g'
```

涉及：`index.html`（canonical/OG/Twitter/JSON-LD）、`zh/index.html`、`robots.txt`、`sitemap.xml`、
`llms.txt`。

## demo 素材怎么来的（可复现）

公开站不能放真实球馆录像，所以 demo 全部是 **CC0 免费素材**重新跑本工具生成的：

1. 素材：Mixkit 免费许可（可商用、无需署名）的网球片段：
   `869`（Tennis players at an outdoor court）、`873`（Tennis player serving）、
   `876`（Tennis player during a match）、`877`（Man playing tennis）、
   `23025`（Man doing a tennis serving）、`47278` / `47279`（Tennis player hitting the ball）、
   `880`（Two people playing tennis aerial view）。
   下载地址形如 `https://assets.mixkit.co/videos/869/869-1080.mp4`。
2. 合成一条「2:16 的原始录像」：统一成 1920×1080 / 30fps，加一段低音量环境底噪（原素材无音轨），
   写入 `creation_time=2026-10-24T09:16:54Z`（角标钟点由它推算）。
3. 跑本工具出片（英文角标）：

```bash
PYTHONPATH=src python -m tennis_evolution all /tmp/demo/source.mp4 \
  --highlights /tmp/demo/highlights.txt --top 4 --sort start \
  --place "Riverside Tennis Club" --unit hits --source-label Source \
  --timezone Asia/Shanghai --codec x264 --crf 20 --preset veryfast \
  --outdir /tmp/demo/out
```

4. 转成网页版本（1280×720 / h264 / faststart），poster 用同帧截图，落 `site/assets/`：
   `demo-reel.mp4`、`demo-plain.mp4`、`clip-01..03.mp4`、`*.jpg`、`frame-label.jpg`、`og.jpg`。

素材清单：

| 文件 | 来源 |
| --- | --- |
| `assets/demo-reel.mp4`、`demo-plain.mp4`、`clip-0*.mp4`、`*.jpg`、`frame-label.jpg`、`og.jpg` | 上述 CC0 素材经本工具渲染 + ffmpeg 转码 |

对应 `highlights.txt`：

```
0:02 - 0:22  Long rally (14 hits)
0:23 - 0:34  Rally (9 hits)
0:46 - 1:00  Long rally (11 hits)
1:30 - 1:42  Rally (12 hits)
```

## 约定

- 站点里**不出现**任何真实球馆/真人素材，demo 只用 `assets/`。
- 不引入外链字体/CDN/tracker；唯一外链是 GitHub 与安装命令。
- 视频一律 `preload="metadata"` + `poster`，不自动播放。

## 支付与许可证（Pages Functions）

`functions/api/*` 与站点同一个 Pages 项目部署，共享代码在 `worker/lib.js`，配置在 `wrangler.toml`。

| 接口 | 作用 |
| --- | --- |
| `POST /api/checkout {"provider":"creem"\|"afdian"}` | 建订单，返回收银台 URL（Creem 跳转；爱发电新标签页打开，本页去 `/thanks/` 等许可证） |
| `POST /api/webhooks/creem` | HMAC 验签 → 签发 `te1.` 许可证（幂等） |
| `POST /api/webhooks/afdian` | 推送不可信，回查爱发电签名 API 校验金额/方案后签发 |
| `GET /api/license?order=<id>` / `?afdian=<订单号>` | 取许可证 / 爱发电订单号找回 |
| `GET /api/stats`（Bearer `STATS_TOKEN`） | 给每日监控用的销量统计 |

一次性配置：

```bash
npx wrangler@4 kv namespace create LICENSES          # 把 id 填进 wrangler.toml
npx wrangler@4 pages secret put TE_SIGNING_JWK --project-name tennis-evolution < ~/.config/tennis-evolution-issuer/ed25519_private.jwk.json
npx wrangler@4 pages secret put CREEM_API_KEY --project-name tennis-evolution
npx wrangler@4 pages secret put CREEM_WEBHOOK_SECRET --project-name tennis-evolution
npx wrangler@4 pages secret put AFDIAN_TOKEN --project-name tennis-evolution
npx wrangler@4 pages secret put STATS_TOKEN --project-name tennis-evolution
```

- **Creem**：建一个 $19 一次性商品 → `CREEM_PRODUCT_ID`；Webhook 地址 `https://<域名>/api/webhooks/creem`，事件 `checkout.completed`。
- **爱发电**：建一个 ¥138 方案 → `AFDIAN_PLAN_ID`、购买链接 `AFDIAN_ORDER_URL`；开发者页拿 `user_id` / token，Webhook 填 `https://<域名>/api/webhooks/afdian`。
- **私钥**只在 `~/.config/tennis-evolution-issuer/`（不进仓库）；丢了要换公钥（`src/tennis_evolution/license.py::PUBLIC_KEY_HEX`），旧许可证全部失效，务必备份。
- 手工发证（退款补发、送测评）：`TE_LICENSE_PRIVATE_KEY_PEM_FILE=~/.config/tennis-evolution-issuer/ed25519_private.pem node tools/issue-license.mjs --name 某某`。
- 本地联调：`.dev.vars` 写上述 secret，`npx wrangler@4 pages dev --kv LICENSES`。

每次部署后：`python3 tools/indexnow.py`（推送 Bing/Yandex，Bing 同时供给 ChatGPT 搜索与 Copilot）。
