"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import __version__
from .cut import ClipPlan, cache_path, cut_clips, plan_clips
from .highlights import Segment, select_top
from .highlights import load as load_highlights
from .highlights import save as save_highlights
from .labels import discover_fonts, specs_for_plans, write_overlays
from .media import MediaInfo, StreamInfo, ensure_tools, keyframes, probe
from .reel import concat_lossless, render_reel
from .util import format_duration, format_timecode, note, warn
from .verify import CheckResult, all_ok, verify_file

__all__ = ["main", "build_parser"]

SORT_CHOICES = ("hits", "duration", "start")


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("video", help="source recording (MOV/MP4/MKV)")
    parser.add_argument(
        "--highlights", required=True, help="highlight list (.txt from sliceapp or .json)"
    )
    parser.add_argument(
        "--top", type=int, default=5, help="keep the N best rallies (default: 5, 0 = all)"
    )
    parser.add_argument(
        "--sort",
        choices=SORT_CHOICES,
        default="hits",
        help="ranking: hits (longest rally first), duration, start (default: hits)",
    )
    parser.add_argument(
        "--lead", type=float, default=1.0, help="seconds of padding before the rally (default: 1.0)"
    )
    parser.add_argument(
        "--tail", type=float, default=1.5, help="seconds of padding after the rally (default: 1.5)"
    )
    parser.add_argument(
        "--audio-stream",
        type=int,
        default=None,
        help="ffprobe index of the audio stream to keep (default: best stereo track)",
    )
    parser.add_argument(
        "--crf", type=float, default=19.0, help="x265/x264 quality, lower is better (default: 19)"
    )
    parser.add_argument(
        "--preset", default="fast", help="encoder preset (default: fast; slow = smaller file)"
    )
    parser.add_argument(
        "--codec",
        choices=("hevc", "x264"),
        default="hevc",
        help="reel codec (default: hevc, keeps 10-bit HDR)",
    )
    parser.add_argument(
        "--keep-rotation",
        action="store_true",
        help="keep rotation metadata instead of baking portrait orientation",
    )
    parser.add_argument(
        "--no-verify", action="store_true", help="skip the self checks (not recommended)"
    )


def _add_label_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-labels", action="store_true", help="skip the burned-in corner labels")
    parser.add_argument("--place", default=None, help='venue text, e.g. "宁波 ECHO Tennis"')
    parser.add_argument("--unit", default="拍", help='hit counter unit (default: "拍")')
    parser.add_argument(
        "--source-label", default="片源", help='prefix for the source timestamp (default: "片源")'
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help="timezone for the wall clock, e.g. Asia/Shanghai (default: system local)",
    )
    parser.add_argument(
        "--font", default=None, help="font file for secondary text, optionally /path/font.ttc:index"
    )
    parser.add_argument("--font-bold", default=None, help="font file for headings and hit count")


def _resolve_audio(info: MediaInfo, index: int | None) -> StreamInfo | None:
    if index is None:
        return None
    for stream in info.audio_streams:
        if stream.index == index:
            return stream
    raise SystemExit(
        f"audio stream {index} not found; available: {[s.index for s in info.audio_streams]}"
    )


def _tz(name: str | None):
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"unknown timezone: {name}") from exc


def _print_plan(plans: list[ClipPlan]) -> None:
    for plan in plans:
        print("  " + plan.describe())
        for entry in plan.notes:
            print(f"      note: {entry}")


def _print_checks(results: list[CheckResult], prefix: str = "") -> bool:
    ok = all_ok(results)
    for result in results:
        print(f"{prefix}{result.line()}")
    return ok


def _check_block(
    paths: list[tuple[str, Path]], *, expected: bool = True
) -> tuple[dict[str, list[CheckResult]], bool]:
    report: dict[str, list[CheckResult]] = {}
    ok = True
    for name, path in paths:
        results = verify_file(path)
        report[name] = results
        ok = _print_checks(results, prefix=f"  {name}: ") and ok
    return report, ok


def _verification_json(report: dict[str, list[CheckResult]]) -> dict[str, list[dict[str, object]]]:
    return {
        name: [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in results]
        for name, results in report.items()
    }


def _cmd_probe(args: argparse.Namespace) -> int:
    info = probe(args.video)
    payload = {
        "path": str(info.path),
        "duration": info.duration,
        "duration_human": format_duration(info.duration),
        "size": info.size,
        "format": info.format_name,
        "created_at": info.created_at.isoformat() if info.created_at else None,
        "created_at_raw": info.created_at_raw,
        "rotation": info.rotation,
        "display_size": [info.display_width, info.display_height],
        "hdr": info.hdr,
        "color": {
            "primaries": info.color_primaries,
            "transfer": info.color_transfer,
            "space": info.color_space,
        },
        "streams": [
            {
                "index": s.index,
                "type": s.codec_type,
                "codec": s.codec_name,
                "width": s.width,
                "height": s.height,
                "fps": s.fps,
                "pix_fmt": s.pix_fmt,
                "channels": s.channels,
                "sample_rate": s.sample_rate,
                "duration": s.duration,
                "bit_rate": s.bit_rate,
            }
            for s in info.streams
        ],
    }
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        note(f"wrote {args.json}")
    if args.quiet and args.json:
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_keyframes(args: argparse.Namespace) -> int:
    info = probe(args.video)
    kfs = keyframes(info.path)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"keyframes": kfs}, indent=2) + "\n", encoding="utf-8"
        )
        note(f"wrote {args.json}")
    gaps = [b - a for a, b in zip(kfs, kfs[1:], strict=False) if 0 < b - a < 30]
    avg = sum(gaps) / len(gaps) if gaps else 0.0
    print(f"{len(kfs)} keyframes, average GOP {avg:.2f}s, first {kfs[0] if kfs else '-'}")
    return 0


def _cmd_highlights(args: argparse.Namespace) -> int:
    segments = load_highlights(args.highlights)
    chosen = select_top(segments, args.top, args.sort)
    print(f"{len(segments)} highlights, showing {len(chosen)} (sort={args.sort})")
    for index, segment in enumerate(chosen, 1):
        print(
            f"  {index:02d}. {format_timecode(segment.start)}-{format_timecode(segment.end)}"
            f"  {segment.label:<12} {format_duration(segment.duration)}"
        )
    if args.out:
        save_highlights(chosen, args.out)
        note(f"wrote {args.out}")
    return 0


def _prepare(
    args: argparse.Namespace,
) -> tuple[MediaInfo, list[Segment], list[ClipPlan]]:
    info = probe(args.video)
    segments = load_highlights(args.highlights)
    chosen = select_top(segments, args.top, args.sort)
    if not chosen:
        raise SystemExit("no highlights selected")
    note(f"scanning keyframes in {info.path.name} ...")
    kfs = keyframes(info.path)
    plans = plan_clips(info, chosen, lead=args.lead, tail=args.tail, keyframe_list=kfs)
    print(
        f"source: {info.path.name}  {format_duration(info.duration)}  "
        f"{info.display_width}x{info.display_height}  {info.video.codec_name if info.video else '?'}"
        f"{'  HDR' if info.hdr else ''}"
    )
    if info.created_at:
        print(f"recorded at: {info.created_at.isoformat()}")
    print(f"selected {len(plans)} highlight(s), sort={args.sort}:")
    _print_plan(plans)
    return info, chosen, plans


def _cmd_cut(args: argparse.Namespace) -> int:
    info, _segments, plans = _prepare(args)
    if args.dry_run:
        return 0
    audio = _resolve_audio(info, args.audio_stream)
    size = info.size
    note(f"cutting {len(plans)} clip(s) from {size / 1e9:.2f} GB with stream copy (bit exact) ...")
    cut_clips(info, plans, args.outdir, audio=audio, overwrite=not args.no_overwrite)
    for plan in plans:
        print(f"  wrote {plan.path}")
    cache = cache_path(args.outdir)
    cache.write_text(
        json.dumps(
            {"source": str(info.path), "clips": [p.to_dict() for p in plans]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    note(f"wrote {cache}")
    if not args.no_verify:
        _check_block([(Path(p.path).name, Path(p.path)) for p in plans if p.path])
    return 0


def _render(
    args: argparse.Namespace,
    info: MediaInfo,
    plans: list[ClipPlan],
    out: Path,
    *,
    with_labels: bool,
) -> Path:
    audio = _resolve_audio(info, args.audio_stream)
    overlays = None
    if with_labels:
        tz = _tz(args.timezone)
        created = info.created_at or datetime.fromtimestamp(info.path.stat().st_mtime, tz=UTC)
        if info.created_at is None:
            warn(
                "no creation_time in the source, using the file modification time for the wall clock"
            )
        specs = specs_for_plans(
            plans,
            created_at=created,
            tz=tz,
            place=args.place,
            unit=args.unit,
            source_label=args.source_label,
        )
        fonts = discover_fonts(args.font, args.font_bold)
        overlays = write_overlays(
            (info.display_width, info.display_height), specs, Path(args.outdir) / "labels", fonts
        )
        note(f"rendered {len(overlays)} label overlay(s)")
    note(f"encoding {out.name} from the source in one pass (this is the slow step) ...")
    return render_reel(
        info,
        plans,
        out,
        overlays=overlays,
        crf=args.crf,
        preset=args.preset,
        codec=args.codec,
        audio=audio,
        keep_rotation=args.keep_rotation,
        dry_run=args.dry_run,
    )


def _cmd_reel(args: argparse.Namespace) -> int:
    info, _segments, plans = _prepare(args)
    if args.dry_run:
        return 0
    out = Path(args.out)
    with_labels = not args.no_labels
    if with_labels and not args.place:
        warn("no --place given, the venue line will be omitted")
    reel = _render(args, info, plans, out, with_labels=with_labels)
    print(f"reel: {reel} ({format_duration(probe(reel).duration)})")
    if not args.no_verify:
        _check_block([(reel.name, reel)])
    return 0


def _cmd_all(args: argparse.Namespace) -> int:
    outdir = Path(args.outdir)
    clips_dir = outdir / "clips"
    args.outdir = str(outdir)
    info, chosen, plans = _prepare(args)
    normalized = save_highlights(chosen, outdir / "highlights.normalized.json")
    note(f"wrote {normalized}")

    verification: dict[str, list[CheckResult]] = {}
    ok = True
    cut_clips(
        info,
        plans,
        clips_dir,
        audio=_resolve_audio(info, args.audio_stream),
        overwrite=not args.no_overwrite,
    )
    for plan in plans:
        print(f"  clip {plan.path}")
    if not args.no_verify:
        report, ok = _check_block([(Path(p.path).name, Path(p.path)) for p in plans if p.path])
        verification.update(report)

    reels: dict[str, str] = {}
    plain = outdir / "reel_plain.mp4"
    _render(args, info, plans, plain, with_labels=False)
    reels["plain"] = str(plain)
    if not args.no_verify:
        report, passed = _check_block([(plain.name, plain)])
        verification.update(report)
        ok = ok and passed

    labeled: Path | None = None
    if not args.no_labels:
        labeled = outdir / "reel_labeled.mp4"
        _render(args, info, plans, labeled, with_labels=True)
        reels["labeled"] = str(labeled)
        if not args.no_verify:
            report, passed = _check_block([(labeled.name, labeled)])
            verification.update(report)
            ok = ok and passed

    manifest = {
        "tool": f"tennis-evolution {__version__}",
        "source": {
            "path": str(info.path),
            "duration": round(info.duration, 3),
            "size": info.size,
            "rotation": info.rotation,
            "hdr": info.hdr,
            "color_transfer": info.color_transfer,
            "created_at": info.created_at.isoformat() if info.created_at else None,
        },
        "settings": {
            "top": args.top,
            "sort": args.sort,
            "lead": args.lead,
            "tail": args.tail,
            "crf": args.crf,
            "preset": args.preset,
            "codec": args.codec,
            "place": args.place,
            "timezone": args.timezone,
            "labels": not args.no_labels,
        },
        "clips": [plan.to_dict() for plan in plans],
        "reels": reels,
        "verification": _verification_json(verification),
        "ok": ok,
    }
    manifest_path = outdir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    note(f"wrote {manifest_path}")
    print("\nsummary")
    for key, value in reels.items():
        print(f"  {key:<8} {value}")
    print(f"  verify   {'PASS' if ok else 'FAIL'}  (details in {manifest_path})")
    return 0 if ok else 1


def _cmd_concat(args: argparse.Namespace) -> int:
    _out, warnings = concat_lossless(args.clips, args.out)
    print(f"wrote {args.out}")
    if warnings:
        print("this lossless concat has problems; prefer `tennis-evolution reel`:", file=sys.stderr)
        for entry in warnings:
            print(f"  - {entry}", file=sys.stderr)
        return 1
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    expected = args.expected_duration
    results = verify_file(
        args.file,
        expected_duration=expected,
        deep_decode=not args.fast,
        check_frames=not args.fast,
    )
    ok = _print_checks(results)
    if args.json:
        Path(args.json).write_text(
            json.dumps(_verification_json({"file": results}), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        note(f"wrote {args.json}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tennis-evolution",
        description="Turn a long tennis recording into label-burned highlight reels.",
        epilog=(
            "typical run:\n"
            "  tennis-evolution all IMG_5155.MOV --highlights highlights.txt --top 5 \\\n"
            '      --place "宁波 ECHO Tennis" --outdir out'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"tennis-evolution {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="inspect a recording (streams, HDR, rotation, creation time)")
    p.add_argument("video")
    p.add_argument("--json", help="write the probe result to a JSON file")
    p.add_argument("--quiet", action="store_true", help="only write the --json file")
    p.set_defaults(func=_cmd_probe)

    p = sub.add_parser("keyframes", help="list key frames and the average GOP length")
    p.add_argument("video")
    p.add_argument("--json", help="write the keyframe list to a JSON file")
    p.set_defaults(func=_cmd_keyframes)

    p = sub.add_parser("highlights", help="inspect or normalize a highlight list")
    p.add_argument("highlights")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--sort", choices=SORT_CHOICES, default="hits")
    p.add_argument("--out", help="write the selected segments as JSON")
    p.set_defaults(func=_cmd_highlights)

    p = sub.add_parser("cut", help="cut top-N highlights with keyframe-accurate stream copy")
    _add_source_args(p)
    p.add_argument("--outdir", required=True, help="directory for the lossless clips")
    p.add_argument("--dry-run", action="store_true", help="print the plan without touching ffmpeg")
    p.add_argument("--no-overwrite", action="store_true")
    p.set_defaults(func=_cmd_cut)

    p = sub.add_parser("reel", help="render one reel (optionally labeled) without keeping clips")
    _add_source_args(p)
    _add_label_args(p)
    p.add_argument("--out", required=True, help="output reel path")
    p.add_argument(
        "--outdir", default="out", help="working directory for label PNGs (default: out)"
    )
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_cmd_reel)

    p = sub.add_parser("all", help="clips + plain reel + labeled reel + manifest, with self checks")
    _add_source_args(p)
    _add_label_args(p)
    p.add_argument("--outdir", required=True, help="output directory")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-overwrite", action="store_true")
    p.set_defaults(func=_cmd_all)

    p = sub.add_parser("concat", help="lossless concat escape hatch (reports timestamp problems)")
    p.add_argument("clips", nargs="+")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_concat)

    p = sub.add_parser("verify", help="run the self checks on a finished file")
    p.add_argument("file")
    p.add_argument("--expected-duration", type=float, default=None)
    p.add_argument("--fast", action="store_true", help="skip the full decode and frame checks")
    p.add_argument("--json")
    p.set_defaults(func=_cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"cut", "reel", "all", "probe", "keyframes", "verify", "concat"}:
        ensure_tools()
    if getattr(args, "outdir", None):
        Path(args.outdir).mkdir(parents=True, exist_ok=True)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
