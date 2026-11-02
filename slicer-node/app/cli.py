"""命令行用法（不启 HTTP，方便在容器里做冒烟测试 / 批量脚本）。

  python -m app.cli slice -m model.stl -M voron24-350 -P 020-standard -F generic-pla -o /tmp/out
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import profiles as prof
from .config import load_settings
from .slicer import SliceError, run_slice


async def _do(args: argparse.Namespace) -> int:
    s = load_settings()
    docs = {
        "machine": prof.load_profile(s.profile_dir, "machine", args.machine),
        "process": prof.load_profile(s.profile_dir, "process", args.process),
        "filament": prof.load_profile(s.profile_dir, "filament", args.filament) if args.filament else None,
    }
    overrides = json.loads(args.overrides) if args.overrides else {}

    work = Path(tempfile.mkdtemp(prefix="slice-"))
    model = Path(args.model)
    local_model = work / ("model" + model.suffix.lower())
    shutil.copy2(model, local_model)

    try:
        summary, gcode, log = await run_slice(s, work, local_model, docs, overrides, args.timeout)
    except SliceError as e:
        print("FAILED: %s" % e, file=sys.stderr)
        print("work dir kept: %s" % work, file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.output:
        shutil.copy2(gcode, args.output)
        print("gcode -> %s" % args.output)
    shutil.rmtree(work, ignore_errors=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("slice", help="切一个模型")
    p.add_argument("-m", "--model", required=True)
    p.add_argument("-M", "--machine", required=True)
    p.add_argument("-P", "--process", required=True)
    p.add_argument("-F", "--filament", default=None)
    p.add_argument("--overrides", default=None, help='JSON，如 \'{"layer_height":"0.28"}\'')
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("-o", "--output", default=None, help="把 gcode 拷到这里")

    pv = sub.add_parser("preview", help="把已有 gcode 拆成逐层线段")
    pv.add_argument("-g", "--gcode", required=True)
    pv.add_argument("-o", "--outdir", required=True)

    sub.add_parser("profiles", help="列出可用 profile")
    args = ap.parse_args(argv)

    if args.cmd == "preview":
        from . import gcode as gm
        out = Path(args.outdir)
        idx = gm.parse(Path(args.gcode), out / "segments.bin")
        gm.write_index(idx, out / "index.json")
        print(json.dumps({k: idx[k] for k in
                          ("layer_count", "segment_count", "bin_bytes", "parse_ms", "bbox")},
                         ensure_ascii=False))
        return 0

    if args.cmd == "profiles":
        s = load_settings()
        print(json.dumps(prof.catalog(s.profile_dir), ensure_ascii=False, indent=1))
        return 0
    return asyncio.run(_do(args))


if __name__ == "__main__":
    sys.exit(main())
