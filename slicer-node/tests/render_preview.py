#!/usr/bin/env python3
"""把 preview 的二进制离屏渲染成 PNG，用于无浏览器环境下的验证。

不引第三方库：手写 PNG（zlib only）。
两种视图：
  --view layer   单层俯视（看截面/填充是否合理）
  --view iso     斜投影叠层（看整体形状，最新 40 层高亮）

用法：
  python3 tests/render_preview.py --dir /data/jobs/<id>/preview --out /tmp/p.png --view iso
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

COLORS = [(255, 125, 56), (255, 230, 77), (176, 48, 41), (150, 84, 204),
          (179, 136, 255), (77, 208, 161), (107, 114, 128)]
BG = (13, 17, 23)


class Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.px = bytearray(bytes(BG) * (w * h))

    def line(self, x0: float, y0: float, x1: float, y1: float, color, alpha: float = 1.0):
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        if n > 4000:
            n = 4000
        for k in range(n + 1):
            t = k / n
            x = int(x0 + (x1 - x0) * t)
            y = int(y0 + (y1 - y0) * t)
            if 0 <= x < self.w and 0 <= y < self.h:
                o = (y * self.w + x) * 3
                for c in range(3):
                    self.px[o + c] = int(self.px[o + c] * (1 - alpha) + color[c] * alpha)

    def write_png(self, path: Path) -> None:
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw.append(0)
            raw += self.px[y * stride:(y + 1) * stride]

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        hdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", hdr)
                         + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
                         + chunk(b"IEND", b""))


def layer_segments(bin_path: Path, layer: dict, version: int = 6):
    n = layer["count"]
    if n == 0:
        return []
    stride = 20 if version >= 5 else (18 if version >= 4 else 17)
    with open(bin_path, "rb") as f:
        f.seek(layer["offset"])
        buf = f.read(n * stride)
    if version >= 6:          # 按段交错
        pos = struct.unpack("<%df" % (n * 4),
                            b"".join(buf[i * stride:i * stride + 16] for i in range(n)))
        types = buf[16::stride]
        areas = buf[17::stride]
    else:                     # 平面分组
        pos = struct.unpack_from("<%df" % (n * 4), buf, 0)
        types = buf[n * 16:n * 17]
        areas = buf[n * 17:n * 18] if version >= 4 else bytes(n)
    return [(pos[i * 4], pos[i * 4 + 1], pos[i * 4 + 2], pos[i * 4 + 3], types[i], areas[i])
            for i in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="preview 目录（含 index.json 与 segments.bin）")
    ap.add_argument("--out", default="/tmp/preview.png")
    ap.add_argument("--view", choices=["layer", "iso"], default="iso")
    ap.add_argument("--layer", type=int, default=-1, help="layer 视图用哪层，默认中间层")
    ap.add_argument("--size", type=int, default=900)
    a = ap.parse_args()

    d = Path(a.dir)
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    bin_path = d / "segments.bin"
    layers = index["layers"]
    ver = int(index.get("version") or 0)
    if not layers:
        print("没有层", file=sys.stderr)
        return 1
    b = index["bbox"]
    cv = Canvas(a.size, a.size)

    if a.view == "layer":
        li = a.layer if a.layer >= 0 else len(layers) // 2
        ly = layers[li]
        bb = ly["bbox"] or b
        span = max(bb[2] - bb[0], bb[3] - bb[1]) * 1.1
        ox, oy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        s = a.size / span
        for x0, y0, x1, y1, t, _a in layer_segments(bin_path, ly, ver):
            cv.line((x0 - ox) * s + a.size / 2, a.size / 2 - (y0 - oy) * s,
                    (x1 - ox) * s + a.size / 2, a.size / 2 - (y1 - oy) * s,
                    COLORS[t % len(COLORS)])
        print("layer %d z=%.2f segs=%d" % (li, ly["z"], ly["count"]))
    else:
        # 斜投影：x'=(x-y)*cos30, y'=(x+y)*sin30 - z
        cx, cy = (b[0] + b[3]) / 2, (b[1] + b[4]) / 2
        cos30, sin30 = 0.866, 0.5
        span = max(b[3] - b[0], b[4] - b[1]) * 1.15
        span = max(span, (b[5] - b[2]) * 1.5)
        s = a.size / span * 0.62
        zs = b[5] - b[2]
        top = max(0, len(layers) - 40)
        for li, ly in enumerate(layers):
            alpha = 0.9 if li >= top else 0.16
            z = ly["z"]
            for x0, y0, x1, y1, t, _a in layer_segments(bin_path, ly, ver):
                col = COLORS[t % len(COLORS)]
                a0 = ((x0 - cx) - (y0 - cy)) * cos30
                b0 = ((x0 - cx) + (y0 - cy)) * sin30 - (z - zs * 0.15) * 1.2
                a1 = ((x1 - cx) - (y1 - cy)) * cos30
                b1 = ((x1 - cx) + (y1 - cy)) * sin30 - (z - zs * 0.15) * 1.2
                cv.line(a0 * s + a.size / 2, a.size / 2 - b0 * s,
                        a1 * s + a.size / 2, a.size / 2 - b1 * s, col, alpha)
        print("iso: %d layers" % len(layers))

    out = Path(a.out)
    cv.write_png(out)
    print("wrote %s (%d bytes)" % (out, out.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
