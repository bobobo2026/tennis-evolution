---
name: tennis-evolution
description: 把长网球录像剪成带角标的精彩集锦。当用户给出网球/球类训练录像并说「剪出最长的几个回合」「做成精彩集锦」「加拍数/地点/时间角标」「把长视频里的有效回合挑出来」时必须使用。覆盖：高光列表解析与排序、关键帧无损切片、单次重编码拼接、HDR 竖屏处理、角标烧录、自检与 manifest。不负责击球检测算法本身（可由 sliceapp 等工具或人工提供列表）。
---

# tennis-evolution

把「一两小时的原片 + 一份高光列表」变成「几条无损片段 + 带角标的合集 + 可审计的 manifest」。

## 何时使用

- 用户给出一段网球（或其他球类）长时间录制，要求剪出高光/回合/最长的几次拉锯；
- 用户要求给视频加上「拍数 / 地点 / 时间」角标；
- 用户已有 sliceapp 之类的击球列表，想批量落地成品；
- 需要可复现、可自检的剪辑流水线（不允许"看着差不多"就交付）。

## 前置检查

1. `ffmpeg -version`、`ffprobe -version` 可用；缺失则先安装（macOS `brew install ffmpeg`，Debian `apt install ffmpeg fonts-noto-cjk`）。
2. `tennis-evolution --version` 可执行；否则 `pipx install tennis-evolution` 或用仓库源码 `pip install -e .`。
3. 中文字体存在（macOS 自带 Hiragino Sans GB；Linux 需 `fonts-noto-cjk`）。缺失时角标会报错并给出安装提示。

## 标准流程

```bash
# 1. 看清素材（旋转、HDR、音轨、录制时间）
tennis-evolution probe RAW.MOV

# 2. 如果用户只给了检测结果文本，先确认排序与选择
tennis-evolution highlights highlights.txt --top 5 --sort hits

# 3. 全流程：无损切片 + 无角标合集 + 带角标合集 + 自检
tennis-evolution all RAW.MOV \
  --highlights highlights.txt \
  --top 5 --sort hits \
  --place "用户告诉你的场地名" \
  --timezone Asia/Shanghai \
  --outdir out

# 4. 读 manifest 决策
python3 -c "import json;m=json.load(open('out/manifest.json'));print(m['ok']);print(json.dumps(m['reels'],indent=2))"
```

只做其中一步时：

- 只要无损片段：`tennis-evolution cut RAW.MOV --highlights h.txt --top 5 --outdir clips/`
- 只要合集：`tennis-evolution reel RAW.MOV --highlights h.txt --place "..." --out reel.mp4`
- 交付前复检：`tennis-evolution verify out/reel_labeled.mp4`

## 输入格式

- sliceapp 文本：`27:14 - 28:08  Long rally (40 hits)`（表头/注释自动忽略）；
- JSON：`{"segments":[{"start":"27:14","end":"28:08","hits":40}]}`。

缺拍数也能跑（角标只显示地点与时间）。`--sort hits|duration|start` 控制选片逻辑：
默认 `hits`（拍数多优先，其次时长，最后时间），保证"最长回合"稳定排第一。

## 必须遵守的规则

1. **不要用 `concat -c copy` 拼合集**。本项目实测会产生 1 秒音画偏移、`Non-monotonic DTS`、HEVC 跨段参考帧断裂；
   合集一律用 `reel`/`all`（trim + concat + 单次编码）。若用户坚持无损拼接，用 `tennis-evolution concat`
   并如实转达它报告的警告，不要声称成功。
2. **切片保持 `-c copy`**。不要为了"精确到毫秒"去重编码切片；关键帧对齐 + `--lead/--tail` 已足够。
3. **HDR 源片必须显式传色彩参数**（工具已自动完成）。若改用其他命令，务必带
   `-color_primaries bt2020 -color_trc arib-std-b67 -colorspace bt2020nc`，否则成品发灰。
4. **竖屏素材**默认把旋转烧进画面；用户明确要求保留元数据时加 `--keep-rotation`。
5. **交付前看 `manifest.json` 的 `ok`**。为 false 时按 `verification` 里失败的条目排查，不要直接交付。

## 验收标准

- `all` 退出码 0，`manifest.ok == true`；
- 每条 clip 是 `-c copy` 产物（`ffprobe` 显示与源片相同 codec/pix_fmt）；
- 合集音视频 `start_time` 均为 0，时长等于各片段之和（±0.8 s）；
- 全片解码零错误；
- 角标内容与用户给的地点/时区一致，拍数与列表一致。

## 常见故障对照

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 成品画面发灰 | 丢了 HDR 色彩标签 | 用 `reel/all`（自动带标签），不要手搓 ffmpeg |
| 音画不同步 | `concat -c copy` | 改用 `reel/all` |
| 切点花屏 | 切在 GOP 中间 | 让工具对齐关键帧（默认行为） |
| `drawtext` 不存在 | ffmpeg 无 freetype | 本工具用 Pillow overlay，无需 drawtext |
| 切片无声音 | 选错音轨（iPhone 空间音频） | `probe` 看音轨，必要时 `--audio-stream <index>` |
| 角标中文变方块 | 缺 CJK 字体 | 装 `fonts-noto-cjk` 或 `--font/--font-bold` 指定 |
| 钟点不对 | 源片无 `creation_time` 或时区错 | 传 `--timezone`；无创建时间时工具退回文件时间并告警 |

## 交付

回复里给出：成品绝对路径、时长/分辨率/码率、每个片段的拍数与片源时间点、`manifest.json` 路径。
需要二次编辑时，把 `out/clips/` 与 `out/labels/` 一并交付（角标 PNG 可复用）。
