# tennis-evolution Web 版 · 实现契约（v1）

本地 Web 版：让用户不用命令行就能体验「长网球录像 → 带角标精彩合集」。
**核心约束：纯本地运行、零新增依赖、真实素材不出本机。**

本文件是并行开发的唯一契约。改接口先改这里。

---

## 0. 硬规则

1. **零新增依赖**：后端只用 Python 标准库（`http.server` + `ThreadingHTTPServer`）+
   `Pillow`（已有）；前端 vanilla HTML/CSS/JS，**不引入 npm / CDN / 构建步骤 / 远程字体**。
2. **纯本地**：素材、产物、上传文件都落本机目录，不请求任何外部服务，不发 telemetry。
3. **不违反 pipeline 四条不变量**（见 `AGENTS.md` §1）：切片 `-c copy` + 关键帧对齐；
   合集 trim→concat→单次编码；HDR 色彩标签透传 + 角标走 Pillow PNG overlay；产物必须过 `verify`。
4. **不提交真实素材**：运行产物写 `<runs_root>`（默认仓库根 `webapp-runs/`），已在 `.gitignore`。
5. **UI 全中文**，桌面优先（1280–1680 宽），移动端能看不塌。
6. ffmpeg 路径：继承 `FFMPEG`/`FFPROBE` 环境变量；未设置时若
   `/opt/homebrew/bin/ffmpeg` 存在则用它（本机 Homebrew 构建无 `drawtext`，不影响本方案）。

---

## 1. 目录与职责

```
src/tennis_evolution/webapp/
├── __init__.py      # create_server(...) 工厂 + 版本号
├── __main__.py      # python -m tennis_evolution.webapp --port 8787
├── app.py           # HTTP 层：路由、静态文件、Range、上传、JSON 错误   ← 后端同学
├── jobs.py          # 任务编排：pipeline 调用、进度、日志、持久化、demo ← 主控
└── static/
    ├── index.html   # 单页骨架                                  ← 前端同学
    ├── app.js       # 交互、轮询、渲染
    └── style.css    # 设计系统
tests/test_webapp.py # 单元 + 端到端（QA 同学）
docs/webapp.md       # 本文件
```

启动方式（两种都应可用）：

```bash
# 1) 直接跑模块
PYTHONPATH=src python -m tennis_evolution.webapp --port 8787
# 2) 跑 CLI 子命令
PYTHONPATH=src python -m tennis_evolution web --port 8787
```

---

## 2. HTTP 接口

- 基址同源；JSON 一律 `application/json; charset=utf-8`，`ensure_ascii=False`。
- 错误统一：HTTP 4xx/5xx + body `{"ok": false, "error": {"code": "bad_request", "message": "中文可读说明"}}`。
- 成功统一含 `"ok": true`。
- 所有响应 `Cache-Control: no-store`；静态资源 `no-cache`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 返回 `static/index.html` |
| GET | `/static/<path>` | 静态资源（仅 `static/` 内，禁止 `..`） |
| GET | `/api/health` | 环境自检 |
| GET | `/api/demo` | 体验成品（预先渲染的 demo 产物） |
| POST | `/api/probe` | 探测一个本地视频文件 |
| POST | `/api/highlights` | 解析高光文本并给出选片预览 |
| POST | `/api/jobs` | 创建任务并立即后台开跑 |
| GET | `/api/jobs` | 任务列表（新→旧，最多 50） |
| GET | `/api/jobs/<id>` | 任务详情（含进度、日志、结果） |
| POST | `/api/jobs/<id>/cancel` | 请求取消 |
| DELETE | `/api/jobs/<id>` | 删除任务及其产物 |
| PUT | `/api/uploads/<filename>` | 原始 body 流式落盘，返回绝对路径 |
| GET/HEAD | `/files/<job_id>/<relpath>` | 任务产物（支持 Range，视频拖动必需） |
| GET/HEAD | `/demo-files/<relpath>` | demo 产物（支持 Range） |

### 2.1 `GET /api/health`

```json
{"ok": true, "version": "0.1.0", "runs_root": "/abs/path/webapp-runs",
 "tools": {"ffmpeg": "/opt/homebrew/bin/ffmpeg", "ffprobe": "/opt/homebrew/bin/ffprobe", "ok": true},
 "demo": {"available": true}, "limits": {"max_upload_bytes": 5368709120},
 "defaults": {"source": "/Users/yibo/Downloads/IMG_5155.MOV", "place": "宁波 ECHO Tennis",
              "timezone": "Asia/Shanghai", "top": 5, "quality": "preview"}}
```

`defaults.source` 仅在文件存在时给出，否则 `null`（前端把它填进输入框）。

### 2.2 `GET /api/demo`

```json
{"ok": true, "available": true, "title": "前五长回合 · 角标合集",
 "source": {"name": "IMG_5155.MOV", "duration": 2849.195, "duration_hms": "47:29",
            "width": 1080, "height": 1920, "hdr": true, "created_at": "2026-10-24T17:16:54+08:00"},
 "settings": {"top": 5, "sort": "hits", "place": "宁波 ECHO Tennis", "timezone": "Asia/Shanghai",
              "lead": 1.0, "tail": 1.5},
 "reels": {"labeled": {"name": "reel_labeled.mp4", "url": "/demo-files/reel_labeled.mp4",
                       "duration": 228.27, "size": 117000000,
                       "preview_url": "/demo-files/preview/reel_labeled.mp4"},
           "plain": {"name": "reel_plain.mp4", "url": "/demo-files/reel_plain.mp4",
                     "duration": 228.27, "size": 117000000, "preview_url": null},
           "preview": {"name": "reel_labeled.mp4",
                       "url": "/demo-files/preview/reel_labeled.mp4",
                       "duration": 228.27, "size": 42000000}},
 "clips": [{"index": 1, "name": "01_40hits_27m14s-28m08s.mp4",
            "url": "/demo-files/clips/01_40hits_27m14s-28m08s.mp4",
            "preview_url": "/demo-files/preview/clips/01_40hits_27m14s-28m08s.mp4",
            "duration": 57.597, "duration_hms": "0:58", "hits": 40,
            "start": 1634.0, "end": 1688.0, "start_hms": "27:14", "end_hms": "28:08", "size": 63697566}],
 "labels": [{"index": 1, "url": "/demo-files/labels/label_01.png"}],
 "verification": {"reel_labeled.mp4": [{"name": "decode clean", "ok": true, "detail": ""}]},
 "preview_pending": false,
 "verdict": {"ok": true, "files": 7, "checks": 63, "failed": 0},
 "manifest_url": "/demo-files/manifest.json"}
```

**浏览器可播预览（必须）**：Chrome 不能解码 HEVC，HDR(HLG) 标记的 h264 在浏览器里还会偏灰。
所以服务端会为每个要被页面播放的文件额外产出一份 **SDR h264 / 720 宽 / 8bit** sibling，
固定放在 `<run 或 demo 目录>/preview/` 下：

- 合集 → `preview/reel_labeled.mp4`（同时出现在 `reels.preview` 与 `reels.labeled.preview_url`）
- 片段 → `preview/clips/<原文件名>`

前端选播放源一律 `preview_url || url`。demo 的预览在**首次**访问 `/api/demo` 时后台生成，
生成期间返回 `preview_pending: true`（此时用 `reels.labeled` 兜底播放并每 2s 轮询刷新）；
任务的预览在 pipeline 的 `preview` 阶段产出（`quality=high` 时才有，`preview` 档本身就是 h264）。

`available=false` 时其余字段可为空，前端显示「未找到 demo 素材，先跑一次 CLI」并隐藏体验页。

### 2.3 `POST /api/probe`

请求 `{"path": "/Users/yibo/Downloads/IMG_5155.MOV"}`

```json
{"ok": true, "source": {"path": "...", "name": "IMG_5155.MOV", "duration": 2849.195,
  "duration_hms": "47:29", "size": 3275259475, "size_human": "3.05 GB",
  "width": 1080, "height": 1920, "rotation": -90, "hdr": true,
  "color_transfer": "arib-std-b67", "created_at": "2026-10-24T17:16:54+08:00",
  "recorded_at_local": "2026-10-24 17:16", "format": "mov,mp4,m4a,3gp,3g2,mj2",
  "audio_streams": [{"index": 1, "codec": "aac", "channels": 2, "sample_rate": 48000},
                    {"index": 2, "codec": "apac", "channels": 4, "sample_rate": 48000}],
  "video_codec": "hevc", "fps": 29.97, "bit_depth": 10, "has_audio": true},
 "warnings": ["源片含 ffmpeg 无法解码的空间音频轨（apac），将自动只用 AAC 立体声"]}
```

失败：文件不存在 `404 / file_not_found`；非视频 `400 / not_media`。

### 2.4 `POST /api/highlights`

请求 `{"text": "27:14 - 28:08  Long rally (40 hits)\n...", "top": 5, "sort": "hits"}`
（`text` 可以是 sliceapp 文本，也可以是 JSON 数组/`{"segments":[...]}`；也接受 `{"json": ...}` 的等价形式，
实现里嗅探即可。）

```json
{"ok": true, "total": 10, "sort": "hits", "top": 5,
 "selected": [{"index": 1, "start": 1634.0, "end": 1688.0, "start_hms": "27:14", "end_hms": "28:08",
               "hits": 40, "duration": 54.0, "duration_hms": "0:54", "label": "Long rally"}],
 "dropped": [{"start": 0.0, "end": 6.0, "start_hms": "0:00", "end_hms": "0:06", "hits": 3, "duration": 6.0, "duration_hms": "0:06", "label": "Quick exchange"}],
 "summary": {"candidate_duration": 197.0, "candidate_duration_hms": "3:17",
             "with_padding_hms": "3:32", "estimated_output_hms": "3:32"}}
```

解析不出任何段：`400 / bad_highlights` + 提示正确格式。
`with_padding_hms` = Σ(duration + lead + tail)，用于前端预估成片长度。

### 2.5 `POST /api/jobs`（请求 body = JobOptions，见 §3.1）

```json
{"ok": true, "job": {"id": "20261026-2231-4f2a", "status": "queued", "progress": 0.0}}
```

校验失败（路径不存在 / 高光为空 / top 非法）→ `400`，body 带错误码。

### 2.6 `GET /api/jobs` / `GET /api/jobs/<id>`

列表项只含摘要字段：`id, status, stage, stage_label, progress, created_at, elapsed,
source_name, top, quality, ok, error`。

详情 = 完整 job view（§3.3）。

### 2.7 上传

`PUT /api/uploads/IMG_5155.MOV`，body 为原始字节。
- 文件名 sanitize（去路径、去控制字符），落盘名 `<uuid8>_<name>`；
- 超过 `limits.max_upload_bytes`（5 GiB）→ `413 / payload_too_large`；
- 返回 `{"ok": true, "path": "/abs/.../uploads/xxxx_IMG_5155.MOV", "name": "IMG_5155.MOV", "size": 3275259475}`。
- 前端上传完成后把 `path` 填进「片源路径」，后续 `probe`/`jobs` 都用路径，不重复上传。

### 2.8 文件服务

- 必须支持 `Range: bytes=start-end`（206 + `Content-Range`），否则手机/大文件视频不能拖动。
- `Accept-Ranges: bytes`、`Content-Length`、`Content-Type` 按后缀（mp4/hevc→`video/mp4`，
  json→`application/json`，png→`image/png`）。
- 拒绝 `..`、绝对路径、符号链接逃逸；只允许 `<job_id>` 目录内或 demo 目录内。
- 404 返回 JSON 错误而不是 HTML（视频元素不关心，但便于调试）。

---

## 3. 后端 ↔ 任务层契约（`jobs.py`）

### 3.1 `JobOptions`

```python
@dataclass
class JobOptions:
    source: str  # 绝对路径，必须存在，必须含视频流
    highlights: str  # 原始文本（txt 或 json）
    top: int = 5  # 0 = 全部
    sort: str = "hits"  # hits | duration | start
    lead: float = 1.0
    tail: float = 1.5
    place: str | None = "宁波 ECHO Tennis"
    timezone: str = "Asia/Shanghai"
    unit: str = "拍"
    source_label: str = "片源"
    labels: bool = True  # 生成带角标合集
    keep_clips: bool = True  # 保留无损切片
    plain_reel: bool = False  # 额外再出一条无角标合集（多一次编码）
    browser_preview: bool = True  # 额外转一条 h264 8bit 预览，供 Chrome 播放
    quality: str = "preview"  # preview | high
    note: str = ""  # 用户备注，落 manifest
```

质量档映射（`preview` 面向体验，`high` 面向交付）：

| quality | codec | preset | crf |
| --- | --- | --- | --- |
| `preview` | `x264` | `veryfast` | 24 |
| `high` | `hevc` | `fast` | 19 |

`browser_preview`：若主合集不是 h264，则额外渲染 `reel_preview.mp4`
（`libx264 -crf 26 -preset veryfast`，宽度缩到 ≤ 720，`yuv420p`，`+faststart`），
供 Chrome/Firefox 播放；HDR 源不做 tonemap（会略灰，界面注明「预览用，正式版以 labeled 文件为准」）。

### 3.2 `JobManager`

```python
class JobManager:
    def __init__(self, runs_root: Path, demo_dir: Path | None = None,
                 defaults: dict | None = None) -> None: ...
    def create(self, options: JobOptions) -> dict        # 返回 job 摘要，立即返回
    def list(self) -> list[dict]                         # 摘要，新→旧
    def get(self, job_id: str) -> dict | None            # 完整 view
    def cancel(self, job_id: str) -> bool
    def delete(self, job_id: str) -> bool                # 删除 run 目录
    def resolve_file(self, job_id: str, relpath: str) -> Path | None
    def demo(self) -> dict                               # §2.2
    def demo_file(self, relpath: str) -> Path | None
    def probe(self, path: str) -> dict                   # §2.3 的 source 部分
    def parse_preview(self, text: str, top: int, sort: str) -> dict  # §2.4
```

`create()` 永不抛异常给 HTTP 层（校验失败返回 `None` + `manager.last_error`，
或 raise `ValueError`——**决定：raise `ValueError`，HTTP 层转 400**）。

### 3.3 Job view（完整）

```json
{"id": "20261026-2231-4f2a", "status": "running",
 "stage": "render", "stage_label": "编码角标合集 42%",
 "progress": 0.63, "created_at": "2026-10-26T22:31:04+08:00",
 "started_at": "...", "finished_at": null, "elapsed": 41.2,
 "options": {"source": "...", "top": 5, "sort": "hits", "quality": "preview", "...": "全部 JobOptions 字段"},
 "source": {"name": "IMG_5155.MOV", "duration": 2849.195, "duration_hms": "47:29",
            "width": 1080, "height": 1920, "hdr": true, "created_at": "..."},
 "selection": [{"index": 1, "start": 1634.0, "end": 1688.0, "hits": 40,
                "duration": 54.0, "start_hms": "27:14", "end_hms": "28:08",
                "cut_start": 1632.968, "cut_end": 1690.565, "cut_duration": 57.597}],
 "logs": [{"t": "2026-10-26T22:31:05+08:00", "level": "info", "text": "扫描关键帧…"}],
 "error": null,
 "result": {"ok": true, "clips": [{"index": 1, "name": "...", "url": "...",
                                    "preview_url": "/files/<id>/preview/clips/...mp4"}],
            "reels": {"labeled": {"url": "...", "preview_url": "...", "codec": "hevc",
                                   "browser_playable": false, "duration": 148.267, "size": 1}},
            "labels": [...],
            "verification": {...}, "manifest_url": "/files/<id>/manifest.json",
            "outputs": [{"name": "...", "url": "...", "size": 123,
                         "duration": 57.6, "kind": "clip|reel|preview|label|manifest"}]}}
```

`codec` / `browser_playable`：合集的实际编码（`cut` 永远 `-c copy` 保持源编码，片段一律 HEVC/HDR，
所以只要 `browser_preview` 开启就一定会产出 `preview/clips/*`；合集则只在 `quality=high` 时才需要预览）。
前端徽标与「能否直接播」提示应据此判断，而不是只看 `preview_url` 是否存在。

`stage` 枚举：`queued, probe, keyframes, cut, render, render_plain, preview, verify, done, failed, cancelled`。
`progress` ∈ [0,1]，单调不减。

### 3.4 并发

一次只跑 **一个** 任务（ffmpeg 抢 CPU 没意义）：新任务排队 `status=queued`，
同一时刻最多 1 个 `running`؛ 前端展示「排队中，前面还有 N 个」。

---

## 4. 前端契约（`static/`）

### 4.1 信息架构（单页 + 两个 tab）

```
┌ header: 网球进化 · 从长录像到角标合集 ──────────● 运行中 1 · ffmpeg 就绪 ─┐
├ hero: 47 分钟录像 → 3 分 49 秒，只剩回合（大字 + 一句副标题 + 主 CTA）      ├
├ tabs: [体验成品] [生成我的合集]                                            ├
│                                                                          │
│ 体验成品：左侧 9:16 播放器（reel_labeled.mp4）| 右侧：本次参数、5 条片段卡片  │
│          下方：校验结论（7 文件 / 63 项 / 0 失败）+ 角标 PNG 缩略 + 下载区   │
│                                                                          │
│ 生成我的：左 1/3 表单：①片源（路径输入 + 拖拽上传）②高光列表（textarea +     │
│          解析预览列表：拍数/时长/片源时间点，可看到被裁掉的那些）              │
│          ③参数（保留条数、排序、地点、时区、拍数单位、片源前缀、画质档、      │
│          是否保留片段、是否出无角标版）④大按钮「开始生成」                   │
│          右 2/3 运行面板：阶段进度条 + 当前阶段文字 + 实时日志 + 计时         │
│          完成后同一面板切结果：播放器 + 片段列表 + 校验表 + manifest/下载      │
└ 底部：历史任务（最近 8 条，可点回看 / 删除）                                ┘
```

### 4.2 交互要点

- 片源路径输入框失焦/回车 → `POST /api/probe`，右侧显示「47:29 · 1080×1920 · HDR · 录制于 2026-10-24 17:16」。
- 拖文件到片源区 → `PUT /api/uploads/<name>`（带进度条）→ 自动 probe。
- 高光 textarea 输入 debounce 500ms → `POST /api/highlights`，展示已选/未选两组；
  空文本时给「用示例列表」按钮（`examples/highlights_sample.txt` 内容内联到 JS）。
- 参数变化不需要后端校验，提交时后端再校验。
- 提交后 1s 轮询 `GET /api/jobs/<id>`；`status ∈ done/failed/cancelled` 停止。
- 进度条：整体 progress + 阶段列表（probe/keyframes/cut/render/preview/verify）打勾。
- 结果播放器优先 `reels.preview`，没有则 `reels.labeled`；`<video controls playsinline>`。
- 播放源统一 `preview_url || url`（Chrome 不解 HEVC）；demo `preview_pending` 时每 2s 轮询 `/api/demo`
  并在预览就绪后无缝切源（保持用户已选中的片段）；都没有预览时提示「下载后用本地播放器观看」。
- 校验表：按文件分组，每项 `✓/✗ + name + detail`；全绿显示「自检通过 · 可以交付」。
- 失败态：红色面板 + `error` 文本 + 「查看日志」（展开最后 30 行）。
- 取消按钮：`POST /api/jobs/<id>/cancel`。
- 空状态/加载态/错误态都要有；任何 fetch 失败都要 toast，不允许白屏。
- 无网络依赖：字体用系统栈（`PingFang SC`, `Hiragino Sans GB`, system-ui）。

### 4.3 设计基调

- 深色球场质感，主色「网球黄绿」`#C9F24B`，辅色陶土橙 `#E86A3A`，底色 `#0A0E0D`，
  面板 `#121816`，文字 `#F2F1EA`/`#93A19A`。
- 竖屏素材是主角：视频容器 9:16，最大高 62vh，圆角 18px，深色描边 + 轻微发光。
- 数字用等宽（`ui-monospace, SF Mono`），拍数/时长用大字重。
- 不堆 SVG 装饰；用留白、细分隔线、徽章（圆角胶囊）表达状态。
- 动效克制：仅进度条、按钮 hover、tab 切换 120–180ms。

---

## 5. 验收标准（QA）

1. `PYTHONPATH=src python -m tennis_evolution.webapp --port 8787` 能起，`/` 返回页面。
2. `/api/health`、`/api/demo` 在有 `demo_run/` 的工作区返回真实数据。
3. 用**合成小视频**（`tests/test_integration.py` 的 lavfi 方式，8–20 秒）跑通一次
   `POST /api/probe → /api/highlights → /api/jobs`，轮询到 `done`，`result.verification` 全绿。
4. 用真实素材（`~/Downloads/IMG_5155.MOV` + 131 段高光列表）跑一次 `quality=preview`、`top=3`，
   确认 `reel` 可播放、`elapsed` 在 3 分钟内、verify PASS。
5. `/files/...` 支持 Range：`curl -H 'Range: bytes=0-1023'` 返回 206 且 `Content-Range` 正确。
6. 浏览器（Chrome headless 截图）三个状态截图：体验成品、生成中、结果页；无 console error。
7. `pytest -q`、`ruff check .`、`ruff format --check .` 全绿。
8. 真实素材产物**只能**落在 `webapp-runs/`（gitignored），`git status` 干净。
