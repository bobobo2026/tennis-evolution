# tennis-evolution · 网球录像 → 带角标的精彩集锦

把一两小时的网球录像，变成几条「只剩回合」的合集，并在画面上标好**拍数 / 地点 / 拍摄时间 / 片源时间点**。
高光检测交给任意工具或人工（例如 [sliceapp](https://sliceapp.so/tools/) 的击球列表），剪辑、拼接、角标、校验全部在本地完成。

> 这个仓库诞生于一次真实任务：47 分 29 秒的 iPhone 录音录像（1080p 竖屏 HEVC 10-bit HDR），
> 剪出 5 个最长回合（40 / 36 / 27 / 24 / 24 拍），合成为一条 3 分 49 秒的角标合集。
> 过程中踩到的坑（音画偏移、拼接坏帧、HDR 偏色、`drawtext` 缺失）都固化成了代码里的规则和自检项。

```
长录像 ──► 高光列表 ──► 关键帧无损切片 ──► 一次性拼接重编 ──► 角标烧录 ──► 自检
 (MOV/HDR)   (txt/json)     (c=copy)         (trim+concat)     (PNG overlay)  (verify)
```

## 一条命令

```bash
tennis-evolution all IMG_5155.MOV \
  --highlights highlights.txt \
  --top 5 \
  --place "宁波 ECHO Tennis" \
  --timezone Asia/Shanghai \
  --outdir out
```

产物：

```
out/
├── clips/
│   ├── 01_40hits_27m14s-28m08s.mp4      # 1 号长回合，无损切片（原画质、HDR）
│   ├── 02_36hits_38m49s-39m38s.mp4
│   └── ...
├── labels/label_01.png                  # 角标图层（透明 PNG，可单独复用）
├── reel_plain.mp4                       # 合集（无角标）
├── reel_labeled.mp4                     # 合集（带角标）
├── highlights.normalized.json           # 本次实际使用的切片清单
└── manifest.json                        # 全部参数 + 每个文件的校验结果
```

命令结束时打印 `verify PASS/FAIL`，失败会以退出码 1 结束，可直接放进脚本或交给 Agent 跑。

## 安装

需要 **ffmpeg / ffprobe**（未内置，避免几十 MB 的重复分发）：

```bash
# macOS
brew install ffmpeg
# Ubuntu / Debian（顺带装中文字体，角标需要）
sudo apt install ffmpeg fonts-noto-cjk
```

然后装本工具（Python ≥ 3.11）：

```bash
# 直接安装（推荐，pipx 会隔离环境）
pipx install git+https://github.com/bobobo2026/tennis-evolution

# 或者从源码开发
git clone https://github.com/bobobo2026/tennis-evolution
cd tennis-evolution
pip install -e ".[dev]"
```

> 尚未发布到 PyPI，请用上面的 git 安装方式。

## 输入：高光列表

任何能给「起止时间 + 可选拍数」的工具都可以，格式越简单越好。

**sliceapp 导出的文本**（原样粘贴即可，表头和注释会被忽略）：

```text
Highlights — IMG_5155.m4a (made with sliceapp.so/tools/)
2:15 - 2:52  Long rally (24 hits)
27:14 - 28:08  Long rally (40 hits)
38:49 - 39:38  Long rally (36 hits)
```

**JSON**（自有检测器或人工整理）：

```json
{
  "segments": [
    {"start": "27:14", "end": "28:08", "hits": 40, "kind": "Long rally"},
    {"start": 135, "end": 172, "hits": 24}
  ]
}
```

排序默认 `--sort hits`：先按拍数，再按时长，最后按时间，因此「最长回合」永远排第一，结果可复现。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `probe VIDEO` | 看分辨率、旋转、HDR、录制时间、所有音轨（iPhone 常带一条 ffmpeg 无法解码的空间音频轨） |
| `keyframes VIDEO` | 列出关键帧和平均 GOP 长度（决定切片能对齐到多准） |
| `highlights LIST --top 5` | 只看排序结果，不碰视频 |
| `cut VIDEO --highlights LIST --outdir clips/` | 只切无损片段，不合成 |
| `reel VIDEO --highlights LIST --place "..." --out reel.mp4` | 只出合集（可加角标） |
| `all VIDEO --highlights LIST --outdir out/` | 全流程 + 自检 + manifest |
| `verify FILE` | 单独跑一次自检 |
| `concat CLIP... --out FILE` | 无损拼接备选路径（会主动报告时间戳问题，见下） |

## 为什么是这个做法

**1. 切片走关键帧，而不是「精确到毫秒」**
`-c copy` 不能重编码，切片只能落在关键帧上。硬切到 GOP 中间会让尾部几帧失去参考帧（播放器上表现为花屏/绿帧），
而 iPhone 的 GOP 约 1 秒，对齐关键帧的代价只是多几百毫秒画面，换来的是**比特级无损**和 HDR/旋转元数据完整保留。
所以 `--lead 1.0 --tail 1.5` 之后还会向外扩到最近的关键帧，`manifest.json` 里记录了真实的 `cut_start/cut_end`。

**2. 合集必须从原片一次性重编，不能 `concat -c copy`**
把无损片段用 concat demuxer 直接拼会同时踩三个坑（本项目实测）：

- 每条片段的音频起点和视频起点相差不到一个音频包，拼接后 ffmpeg 反复报 `Non-monotonic DTS`；
- 成品 **视频比音频晚 1.01 秒起步**，整条素材永久性音画不同步；
- HEVC 跨片段参考帧断裂，报 `Could not find ref with POC`，切点后出现坏帧。

因此 `reel` 一律「trim → concat → 单次编码」，音画采样级对齐、没有断裂 GOP。
`concat` 子命令保留为逃生通道，但会把上述问题直接报出来而不是假装成功。

**3. 角标用 PNG overlay，不用 `drawtext`**
很多 ffmpeg 发行版（含 Homebrew 默认构建）不带 libfreetype，`drawtext` 根本不存在。
本工具用 Pillow 渲染透明 PNG 再 `overlay`，好处是：字体可控（CJK 不糊）、排版可设计、
同一套 PNG 还能给别的软件复用；HDR 素材上也用半透明深底保证白字可读。

**4. 输出必须自检**
`verify` 会检查：解码零错误、音视频时长一致、两条流都从 0 秒开始（音画偏移的排雷项）、
首尾帧不是黑帧、音频不是静音。`all` 把结果写进 `manifest.json` 并作为退出码。

## 环境与兼容性

- **HDR**：源片为 HLG（iPhone 常见，可能同时是 Dolby Vision profile 8）。合集输出保留 HLG 色彩标签与 10-bit；
  Dolby Vision 的 RPU 动态元数据在重编码时会被丢弃（x265 不支持写回），HDR10/HLG 基础层完好。
  需要投放到只认 SDR 的平台时，自行加 tonemap 即可（未内置，避免把创作者的选择藏起来）。
- **编码器**：默认 `libx265`（`--crf 19 --preset fast`，M 系列 Mac 约 2.3 倍速，3 分 49 秒片子约 100 秒）。
  `--codec x264` 可在没有 x265 的环境下回退（8-bit，适合预览）。
- **旋转**：默认把竖屏方向烧进画面（`1080×1920`），避免部分播放器/平台忽略旋转元数据；`--keep-rotation` 保留元数据方式。
- **时区**：角标钟点 = 源片 `creation_time` + 该回合起点，`--timezone` 决定显示时区；没有 `creation_time` 时退回文件修改时间并告警。

## 作为 Agent Skill 使用

`skills/tennis-evolution/SKILL.md` 是一份可直接加载的技能说明（适用 DeepSeek Harness / Claude 等支持 skill 的 Agent）：

```bash
mkdir -p ~/.dsh/skills && cp -r skills/tennis-evolution ~/.dsh/skills/
```

之后对 Agent 说「把这段录像里最长的五个回合剪成带角标的合集」，它会按 SKILL.md 调用
`tennis-evolution all`，并依据 `manifest.json` 的 `ok` 字段判断是否需要重试。

## 检测环节怎么办？

本工具刻意**不内置**击球检测：拍数统计（`40 hits`）需要一个可靠的音频瞬态检测器，
调参依赖球馆混响、收音距离、是否有多球同时落地——把它做进剪辑工具会让整条流水线变脆。
现在的设计是「检测可插拔」：任何会输出时间区间的工具都能喂进来。

自己实现的难度评估与路线图见 [`docs/detection.md`](docs/detection.md)：onset 检测拿到候选击球点不难，
难点在「击球 / 落地 / 回声」的区分和回合聚类，以及 90% 以上计数准确率的调优。

## 自检项一览

| 检查 | 说明 |
| --- | --- |
| decode clean | 全片解码零错误（`ffmpeg -v error` 无输出） |
| start offset | 视频/音频都从 0 起始，> 0.05 s 即视为音画偏移 |
| A/V duration match | 两条流时长差 ≤ 0.3 s |
| duration | 与预期总长（切片时长之和）一致 |
| frame not black | 首帧/尾帧 YAVG ≥ 40（防黑场、防坏帧） |
| audio not silent | 平均音量 > -70 dB |

## 开发

```bash
pip install -e ".[dev]"
pytest -q                      # 单元测试 + 用 ffmpeg 合成小视频的集成测试
ruff check . && ruff format --check .
```

---

### English summary

**tennis-evolution** turns a long tennis recording into highlight reels with burned-in corner labels
(hit count, venue, wall clock, source timestamp). You bring a highlight list — from sliceapp, a
detector of your choice, or by hand — and the tool does the rest locally:

- **keyframe-accurate lossless cuts** (`-c copy`, no re-encode, HDR/rotation preserved);
- **sync-correct reel assembly** in a single re-encode pass (naive `concat -c copy` produced a 1.01 s
  A/V offset and broken HEVC reference frames — see `docs/pipeline.md`);
- **corner labels rendered with Pillow** and composited as PNG overlays (works on ffmpeg builds
  without libfreetype);
- **self checks** (clean decode, zero start offsets, A/V duration match, non-black frames, audio level)
  written to `manifest.json`, with a non-zero exit code on failure.

Requirements: Python ≥ 3.11 and `ffmpeg`/`ffprobe` on `PATH`. Detection is deliberately out of scope.

## License

MIT
