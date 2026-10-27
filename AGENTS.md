# AGENTS.md

给在本仓库里工作的 AI Agent 的作业说明。人类读者请先看 [README](README.md)。

目标读者：会改代码、加命令、修 bug、写文档的 agent。**改任何东西之前先读第 1 节的不变量**，
那里每一条都对应一次真实踩坑，违反它会让产物"看起来成功但实际坏掉"。

## 0. 30 秒上手

```bash
# 依赖：python >= 3.11、ffmpeg/ffprobe、CJK 字体（macOS 自带，Debian: fonts-noto-cjk）
pip install -e ".[dev]"

# 三条必须保持绿色的命令（提交前全跑）
ruff check .
ruff format --check .
pytest -q            # 52 项；集成测试会用 ffmpeg 现场合成 8 秒小视频，不需要素材
```

跑真实素材（耗时约 5 分钟，只在改动 ffmpeg 参数/编码/色彩时需要）：

```bash
tennis-evolution all RAW.MOV --highlights examples/highlights_sample.txt --top 5 \
  --place "测试场馆" --timezone Asia/Shanghai --outdir /tmp/demo
python3 -c "import json;m=json.load(open('/tmp/demo/manifest.json'));print(m['ok'])"
```

## 1. 四条不变量（不可协商）

| # | 规则 | 违反后的真实后果 |
| --- | --- | --- |
| 1 | **切片只能 `-c copy` + 关键帧对齐**（`cut.py`），永远不要"为了精确到毫秒"重编码 | 重编码让无损交付失效；切在 GOP 中间会留下缺参考帧的尾部，播放器上切点处花屏 |
| 2 | **合集只能 trim → concat → 单次编码**（`reel.py::render_reel`），禁止 `concat demuxer + -c copy` | 实测产生 1.01 s 音画永久偏移、`Non-monotonic DTS`、HEVC `Could not find ref with POC` 坏帧 |
| 3 | **HDR 色彩标签必须透传**（`-color_primaries/-color_trc/-colorspace`），角标必须走 **Pillow PNG + overlay**，不得依赖 `drawtext` | 丢标签 → 成品发灰；`drawtext` 在多数 Homebrew ffmpeg 构建里不存在，命令直接失败 |
| 4 | **每个产物都要过 `verify`**，`all` 的退出码就是验收结论 | 没有自检的交付等于没验收；音画偏移/静音/坏帧只能靠断言发现 |

配套的"解释性"约束：**不要把已知问题静默吞掉**。`warn()`/`CheckResult` 的职责是把问题暴露出来
（`concat` 子命令就是这样保住诚实的），宁可返回非零退出码。

## 2. 模块地图

| 文件 | 职责 | 改它会波及 |
| --- | --- | --- |
| `src/tennis_evolution/media.py` | ffprobe 封装、`MediaInfo`/`StreamInfo`、关键帧扫描、音轨优选 | 全流程（其它模块都从这里拿元数据） |
| `highlights.py` | 高光列表解析（sliceapp 文本/JSON）、排序 `ranked`、`select_top` | CLI 选片、`cut` 的段来源 |
| `cut.py` | `align()` 关键帧对齐、`ClipPlan`、无损切片、命名 | 片段名 = 交付物名，改名会破坏用户已有的产物引用 |
| `reel.py` | 一次成型的合集渲染、`concat_lossless` 逃生通道 | 编码/色彩/音画同步 |
| `labels.py` | 角标 PNG 渲染（字体发现、布局、钟点计算） | 视觉交付、font 兼容性 |
| `verify.py` | `CheckResult` 电池（解码/起始偏移/时长/亮度/音量） | `all` 退出码、manifest |
| `cli.py` | 子命令、参数、manifest 组装 | 所有面向用户的契约 |
| `util.py` | 时间码解析/格式化 | 文件名、角标文本 |
| `src/tennis_evolution/webapp/app.py` | 本地网页版 HTTP 层（标准库 `http.server`：路由/静态/Range/上传/JSON 错误） | `tennis-evolution web`、浏览器体验 |
| `src/tennis_evolution/webapp/jobs.py` | 网页版任务层（单并发队列、阶段进度、日志、run 目录持久化、demo 读取、SDR h264 预览转码） | 网页端进度/结果契约 |
| `src/tennis_evolution/webapp/static/` | 单页 UI（vanilla HTML/CSS/JS，无构建、无外链） | 视觉交付 |

网页版的接口与数据形状契约在 [`docs/webapp.md`](docs/webapp.md)：改字段先改契约，再改两端。
浏览器不能解 HEVC，所以每个要被页面播放的文件都有一份 `preview/` 下的 SDR h264 sibling。
`webapp-runs/`（运行产物）与 `demo_run/` 不提交。

`manifest.json` 是**对外契约**：字段名（`ok`/`clips`/`reels`/`verification`/`settings`）一旦发布就别随便改，
Agent 与脚本都靠它判断成败。

## 3. 常见改动的做法

**加一种高光列表格式**
1. `highlights.py` 加 `parse_xxx()`，返回 `list[Segment]`；
2. `load()` 里按后缀/内容嗅探接入（.txt 已带 JSON 兜底）；
3. `tests/test_highlights.py` 加正常 + 垃圾输入用例；
4. README「输入：高光列表」补格式示例。

**加角标字段（比分、球速、教练名…）**
1. `labels.LabelSpec` 加字段（保持所有字段可选）；
2. `render_label()` 里排版（注意 1080 宽设计单位 × `scale`，不要写死像素）；
3. `specs_for_plans()` 填充来源（需要新数据就加到 `ClipPlan`/`Segment`）；
4. `tests/test_labels.py` 断言内容差异；README + SKILL 的角标说明同步。

**加自检项**
1. `verify.py` 里加一个 `CheckResult(name, ok, detail)`，能给出可读 detail；
2. 放进 `verify_file()` 尾部，别破坏 `--fast` 语义（重活放在 `deep_decode`/`check_frames` 下）；
3. 若它会失败，同步更新 `tests/test_integration.py` 的期望与 README 自检表。

**加子命令 / 参数**
1. `cli.py::build_parser()` 加 subparser，`main()` 的 `ensure_tools()` 白名单按需扩展；
2. 新的输出文件要进 `manifest.json`，别只打印；
3. README 命令表 + SKILL.md 命令示例都要改（两处都对外承诺能力）。
4. 真实素材路径参数永远可选、绝不能硬编码某个用户的文件。

**改编码参数（crf/preset/codec/色彩）**
1. 只动 `reel.py::_encoder_args` / `_colour_args`；
2. 本地跑一次真实大文件 `all`（见第 0 节命令），确认 `manifest.ok == true`、画面不发灰、音画对得上；
3. 合成小视频**测不出** HDR/DV/长 GOP 问题，别只靠 pytest 就交付。

## 4. 测试策略

- 单元测试**不依赖 ffmpeg**：用 `MediaInfo`/`ClipPlan`/`Segment` 假数据构造（见 `tests/test_cut.py`）。
- 集成测试用 `lavfi` 现场合成 8 秒视频（`tests/test_integration.py`，`pytest.mark.integration`），
  **不要往仓库里提交任何媒体文件**（`.gitignore` 已覆盖 `*.mov/*.mp4/*.m4a`）。
- 没有 CJK 字体时 label 相关测试会 skip；CI 装了 `fonts-noto-cjk`，应该全部执行（52 passed，0 skipped）。
- CI 矩阵：Python 3.11/3.12/3.13 + `apt install ffmpeg fonts-noto-cjk` + `ruff check` + `pytest`。

## 5. 代码风格

- Python 3.11+，全量类型标注，数据结构用 `dataclass`；不要为了风格引入新依赖。
- 唯一第三方运行时依赖是 **Pillow**（角标）。新增依赖需要在 PR/commit 里说明理由，并更新 `pyproject.toml`。
- 子进程调用一律走 `media.run()`（失败即抛 `FfmpegError`，带最后 12 行 stderr）。
- 代码注释/docstring 用英文；README / docs / SKILL 用中文（对外文档中文为主，英文摘要保留）。
- 提交前 `ruff format .`；行宽 100。

## 6. 不要做的事

- ❌ 提交素材、生成的 `out/`、`demo_run/`、任何 MP4/MOV。
- ❌ 用放宽 `verify` 阈值的方式让测试变绿——先查根因（阈值是踩坑证据，不是噪音）。
- ❌ 在 `cut`/`reel` 里偷偷加入检测算法；检测是独立环节，输入契约见 `docs/detection.md`。
- ❌ 把 `-ss`/`-t` 之外的裁剪技巧（`-accurate_seek`、重编码切片）当作"更精确"的默认路径。
- ❌ 修改 `requires-python` 下限、`pyproject` 项目名、CLI 入口名而不改 README/SKILL/CI。
- ❌ 删除 `concat` 子命令：它是"承认问题并报告"的逃生通道，删了等于把坑藏起来。

## 7. 完成定义（DoD）

- [ ] `ruff check .`、`ruff format --check .`、`pytest -q` 全绿
- [ ] 改了路径/参数/输出 → README + `skills/tennis-evolution/SKILL.md` 同步
- [ ] 改了 ffmpeg 行为 → 真实素材跑过 `all`，`manifest.ok == true`
- [ ] 新输出文件写进 `manifest.json`
- [ ] commit message 说明**为什么**，不只说明改了什么

## 8. 故障速查

| 症状 | 先看这里 |
| --- | --- |
| 成品发灰 / 颜色不对 | `reel.py::_colour_args`，源片 `color_transfer` 是否为 `arib-std-b67` |
| 音画不同步 | 是否有人绕开 `render_reel` 用了 concat demuxer |
| 切点花屏 | `cut.py::align` 是否真的对齐了关键帧；`-t` 是否被改成非关键帧边界 |
| 切片没声音 | `media.first_audio_stream()`；iPhone 的空间音频轨（`apac`）ffmpeg 无法解码 |
| 角标是方块 | 字体发现失败：装 `fonts-noto-cjk` 或用 `--font/--font-bold` |
| 钟点差 8 小时 | `--timezone` 没传，落到了系统时区；源片 `creation_time` 是 UTC |
| CI 只在 Ubuntu 挂 | 字体路径候选表 `labels._CJK_FONT_CANDIDATES` |
