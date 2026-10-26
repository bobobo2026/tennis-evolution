# 流水线细节与踩坑记录

这份文档记录每个环节的取舍，以及真实素材上测到的数字。目标是：别人遇到同样现象时能直接对上号。

素材：iPhone Air 拍摄，`IMG_5155.MOV`，47 分 29 秒，1920×1080 + 旋转元数据 −90°，HEVC Main10
`yuv420p10le`，BT.2020 / HLG（`arib-std-b67`），Dolby Vision profile 8 基础层，
音轨 1 = AAC 48 kHz 立体声 183 kb/s，音轨 2 = 4 声道空间音频（`apac`，ffmpeg 无法解码）。

## 1. 探测：先别急着剪

`tennis-evolution probe` 输出的信息里，有四项会直接影响后续命令：

| 字段 | 为什么重要 |
| --- | --- |
| `rotation` | −90 表示素材是竖屏拍摄但编码为横向；ffmpeg 默认会 autorotate，输出需按显示尺寸排版角标 |
| `color_transfer` | `arib-std-b67` = HLG HDR。重编码必须显式带上色彩标签，否则播放器按 SDR 解释 → 画面发灰 |
| `streams[*]` | iPhone 会多带一条空间音频（`apac`）或数据轨，`-map` 必须指定，否则切片报 codec 参数错误 |
| `created_at` | 角标里的钟点由它推算；QuickTime 同时写 UTC `creation_time` 和带时区的 `creation_date` |

实测 GOP 长度 1.0667 秒（32 帧 @ 29.97 fps），即关键帧误差上限约 1.07 秒 —— 这就是「无损切片能有多准」的物理上限。

## 2. 切片：关键帧对齐

```python
start = max(k for k in keyframes if k <= segment.start - lead)
end = min(k for k in keyframes if k >= segment.end + tail)
```

- `lead`（默认 1.0 s）保证不丢第一个击球前的引拍动作；
- `tail`（默认 1.5 s）保证最后一拍落地和随挥不被切掉；
- 再向外扩到最近关键帧，让 `-c copy` 输出既**无损**又可解码。

为什么不在结尾任意位置截断：`-c copy` 会在 GOP 中间留下尾部若干帧，它们依赖被丢弃的后续参考帧，
解码器输出 `Could not find ref with POC`，播放器上就是切点处一闪而过的花屏。

实测：40 拍回合（原片 27:14–28:08，54 秒）对齐后为 `1632.968 → 1690.565`，共 57.6 秒，文件 61 MB，
像素与源片完全一致（PSNR = ∞，因为是同一码流）。

## 3. 合集：为什么必须重编一次

先试了最省事的写法：

```bash
ffmpeg -f concat -safe 0 -i list.txt -c copy reel.mp4
```

结果（本项目实测）：

1. **音画永久偏移 1.01 秒**。成片中 `video.start_time = 1.011979`，`audio.start_time = 0`；
   也就是说整条 4 分钟素材画面一直比声音慢 1 秒。原因是每条片段的音频首包与视频首包存在小于一个
   音频包的差，concat 按时长累加后累积成固定偏移。
2. **`Non-monotonic DTS` 刷屏**。每个拼接点 ffmpeg 都要把倒流的 DTS 强行改写，音频时间戳被重新排序。
3. **HEVC 参考帧断裂**。`Could not find ref with POC 2236` 等错误出现在切点附近
   —— 因为每条片段虽然从 IDR 开始，但 concat 后解码器状态并不干净。

修正方案（`reel.py`）：从**原片**按计划 trim，一次 filter graph 内 concat 并编码：

```text
[0:v]trim/ss,setpts ─┐
[0:a]asetpts ────────┤
[1:v] ...            ├─ concat=n=5:v=1:a=1 ─ encode
[1:a] ...            ┘
```

- 音画在采样级对齐（都是同一输入、同一 `setpts/asetpts` 重置）；
- 没有断裂 GOP；
- 只编码一次，避免二次损失；
- 代价是需要重编码（x265 CRF 19，2.3 倍速），换来的是可交付的成品。

`--codec x264` 可在没有 libx265 的环境下回退（8-bit/8-bit HDR 标签会丢失，仅适合预览）。

## 4. 角标：Pillow 渲染 + overlay

`drawtext` 在很多 ffmpeg 构建里根本不存在（本机 Homebrew 8.1.1：`configuration` 里没有
`--enable-libfreetype`）。所以：

1. `labels.py` 用 Pillow 在 1080 宽的画布上按比例排版，导出 RGBA PNG；
2. `reel.py` 把每段 PNG 作为独立输入，在 concat **之前** overlay 到对应片段
   —— 角标天然只出现在自己那一段，不需要 `enable='between(t,..)'` 算时间窗；
3. 底色用 42% 黑圆角块，白字；在 HLG 亮背景（球馆顶棚）也能读清。

钟点计算：`creation_time + segment.start`，再按 `--timezone` 转成当地时间。
实测素材录制于 2026-10-24 17:16:54 (+0800)，因此：

| 片源位置 | 钟点 | 拍数 |
| --- | --- | --- |
| 27:14 | 17:44 | 40 |
| 38:49 | 17:55 | 36 |
| 34:28 | 17:51 | 27 |
| 2:15 | 17:19 | 24 |
| 14:50 | 17:31 | 24 |

合集按拍数排序，所以钟点不是递增的 —— 这是「按精彩程度排」而不是「按时间排」的必然结果。

## 5. 自检：把踩过的坑变成断言

| 现象 | 对应检查 |
| --- | --- |
| 切点花屏 / 坏帧 | `decode clean`（全片解码零错误）+ 首尾帧亮度 |
| 音画不同步 | `start offset ≤ 0.05s` + `A/V duration match ≤ 0.3s` |
| 少切/多切、拼接丢段 | `duration ≈ Σ 片段时长`（manifest 里逐个记录） |
| 音轨选错（空间音频/静音） | `audio not silent`（平均音量 > −70 dB） |

`all` 会把每个文件的检查结果写进 `manifest.json`，任一失败 → 退出码 1。

## 6. 性能参考（M 系列 Mac，1080p30，229 秒成品）

| 步骤 | 耗时 |
| --- | --- |
| 关键帧扫描（3 GB 源片） | ~2 秒 |
| 5 段无损切片 | < 1 秒 |
| x265 CRF 19 编码 3 分 49 秒 | ~100 秒（2.3×） |
| 全量自检（7 个文件全解码） | ~30 秒 |
