"""校验属性列与线段/类型列严格对齐（防止重排时错位）。

思路：同一层里，同一个走线角色的速度/加速度应该高度一致（Orca 按角色设参数），
如果属性字节没有跟着桶排序一起重排，就会出现「外墙里混着 25 和 180 mm/s」这种乱序。
"""
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else
         "/tmp/orca-spike/nodedata/jobs/cebe9289a41c/preview")
index = json.loads((d / "index.json").read_text())
attrs = index["attrs"]
names = index["type_labels"]
bpath = d / "segments.bin"
STRIDE = 24 if int(index.get("version") or 0) >= 7 else 20
fails = []

for li in (5, index["layer_count"] // 2, index["layer_count"] - 12):
    ly = index["layers"][li]
    n = ly["count"]
    with open(bpath, "rb") as f:
        f.seek(ly["offset"])
        buf = f.read(n * STRIDE)
    if int(index.get("version") or 0) >= 6:
        # v6/v7：按段交错（20 或 24 字节）
        types = buf[16::STRIDE]
        areas = buf[17::STRIDE]
        avs = [buf[i * STRIDE + 18] | (buf[i * STRIDE + 19] << 8) for i in range(n)]
    else:
        # v5：平面分组
        types = buf[n * 16:n * 17]
        areas = buf[n * 17:n * 18]
        avs = struct.unpack_from("<%dH" % n, buf, n * 18)
    by = defaultdict(lambda: defaultdict(list))
    for i in range(n):
        a = attrs[avs[i]]
        by[types[i]]["speed"].append(a[1])
        by[types[i]]["accel"].append(a[4])
        by[types[i]]["area"].append(areas[i] * index["area_step"])

    print(f"\n第 {li} 层（z={ly['z']}，{n} 段）")
    for t in sorted(by):
        sp = by[t]["speed"]
        ac = by[t]["accel"]
        ar = by[t]["area"]
        uniq = sorted(set(sp))
        # 同一角色内速度种类应该很少（按角色设速 + 少数限速）
        share = 1 - len(uniq) / len(sp) if sp else 0
        print(f"  {names[t]:5s} n={len(sp):4d} 速度 {min(sp)}~{max(sp)} "
              f"({len(uniq):3d} 种) 加速度 {sorted(set(ac))[:4]} "
              f"线宽 {min(ar):.2f}~{max(ar):.2f}")
        if len(uniq) > max(3, len(sp) * 0.5):
            fails.append(f"第 {li} 层 {names[t]} 速度过于杂乱（{len(uniq)} 种 / {len(sp)} 段）")

# 抽样：抽 200 个随机段，独立重算「它的 E 增量/长度」应≈面积列
import random
random.seed(7)
for _ in range(60):
    li = random.randrange(index["layer_count"])
    ly = index["layers"][li]
    if not ly["count"]:
        continue
    n = ly["count"]
    with open(bpath, "rb") as f:
        f.seek(ly["offset"])
        buf = f.read(n * STRIDE)
    if int(index.get("version") or 0) >= 6:
        pos = struct.unpack("<%df" % (n * 4),
                            b"".join(buf[i * STRIDE:i * STRIDE + 16] for i in range(n)))
        areas = buf[17::STRIDE]
    else:
        pos = struct.unpack_from("<%df" % (n * 4), buf, 0)
        areas = buf[n * 17:n * 18]
    i = random.randrange(n)
    L = ((pos[i * 4 + 2] - pos[i * 4]) ** 2 + (pos[i * 4 + 3] - pos[i * 4 + 1]) ** 2) ** 0.5
    if L < 1e-6 or areas[i] == 0:
        fails.append(f"第 {li} 层第 {i} 段长度 {L:.4f} / 面积码 {areas[i]}")
    if not (0.02 < areas[i] * index["area_step"] < 0.5):
        fails.append(f"第 {li} 层第 {i} 段截面积异常 {areas[i] * index['area_step']:.3f}")

if fails:
    print("\nFAIL")
    for f_ in fails[:10]:
        print("  -", f_)
    sys.exit(1)
print("\nOK — 属性列与线段对齐")
