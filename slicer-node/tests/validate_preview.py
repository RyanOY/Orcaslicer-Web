#!/usr/bin/env python3
"""preview 产物与 gcode 的交叉校验（独立实现，故意不复用 app/gcode.py 的逻辑）。

检查项：
  1. 层数 == gcode 里 ;LAYER_CHANGE 的个数
  2. 每层 z 与 ;Z: 注释逐一对齐
  3. bin_bytes == Σ(每层 count × stride)（v7 = 24：含真实速度/加速度两列）
  4. 每层类型计数之和 == count
  5. 抽样层的坐标有限且在全局 bbox 内
  6. 独立数一遍挤出段（M82/M83 + G92 都认），与 segment_count 对比
  7. **独立算挤出总长度**（G1 线段长 + G2/G3 弧长）与 bin 里线段总长对比
     —— 这条最狠：漏段、重复、坐标错位都会立刻暴露

用法：python3 tests/validate_preview.py --dir <preview dir> --gcode <plate_1.gcode>
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from pathlib import Path

RE_LAYER = re.compile(r"^;LAYER_CHANGE")
RE_Z = re.compile(r"^;Z:(-?\d+(?:\.\d+)?)")
RE_MOVE = re.compile(r"^G([0-3])\s+(.*)$")
RE_G92 = re.compile(r"^G92\s+(.*)$")


def independent_stats(gcode: Path):
    """独立实现：层 z 列表、挤出段数、挤出总长度。"""
    zs: list[float] = []
    moves = 0
    length = 0.0
    e = 0.0
    x = y = 0.0
    abs_e = True

    for line in gcode.open("r", encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        if line[0] == ";":
            if RE_LAYER.match(line):
                zs.append(float("nan"))
            else:
                m = RE_Z.match(line)
                if m and zs and math.isnan(zs[-1]):
                    zs[-1] = float(m.group(1))
            continue

        if line.startswith("M82"):
            abs_e = True
            continue
        if line.startswith("M83"):
            abs_e = False
            continue

        m = RE_G92.match(line)
        if m:
            for tok in m.group(1).split():
                if tok and tok[0] == "E":
                    try:
                        e = float(tok[1:])
                    except ValueError:
                        pass
            continue

        m = RE_MOVE.match(line)
        if not m:
            continue
        code = int(m.group(1))
        nx, ny, ne, i_off, j_off = None, None, None, 0.0, 0.0
        for tok in m.group(2).split():
            if len(tok) < 2:
                continue
            try:
                v = float(tok[1:])
            except ValueError:
                continue
            c = tok[0]
            if c == "X":
                nx = v
            elif c == "Y":
                ny = v
            elif c == "E":
                ne = v
            elif c == "I":
                i_off = v
            elif c == "J":
                j_off = v

        if ne is not None:
            delta = (ne - e) if abs_e else ne
            if abs_e:
                e = ne
            else:
                e += ne
            if delta > 1e-9 and nx is not None and ny is not None:
                if code == 1:
                    seg = math.hypot(nx - x, ny - y)
                else:
                    cx, cy = x + i_off, y + j_off
                    r = math.hypot(x - cx, y - cy)
                    a0 = math.atan2(y - cy, x - cx)
                    a1 = math.atan2(ny - cy, nx - cx)
                    sweep = a1 - a0
                    if code == 2:
                        while sweep >= 0:
                            sweep -= 2 * math.pi
                    else:
                        while sweep <= 0:
                            sweep += 2 * math.pi
                    seg = abs(r * sweep)
                if seg > 1e-6:
                    moves += 1
                    length += seg
        if nx is not None:
            x = nx
        if ny is not None:
            y = ny

    return zs, moves, length


def stride_of(index: dict) -> int:
    v = int(index.get("version") or 0)
    if v >= 7:
        return 24
    return 20 if v >= 5 else (18 if v >= 4 else 17)


def decode_layer(buf: bytes, n: int, version: int) -> dict:
    """把一层字节解成各字段。

    v6 起文件里是「按段交错」：每段 20 字节 = 4×Float32 位置 + 类型 + 截面 + 2 字节属性；
    v7 再加 2 字节真实速度（×100）和 2 字节有效加速度；
    v5 及更早是「平面分组」：位置块 / 类型块 / 截面块 / 属性块各自连续。
    """
    if version >= 6:
        stride = 24 if version >= 7 else 20
        pos = struct.unpack("<%df" % (n * 4),
                            b"".join(buf[i * stride:i * stride + 16] for i in range(n)))
        types = buf[16::stride]
        areas = buf[17::stride]
        attrs = [buf[i * stride + 18] | (buf[i * stride + 19] << 8) for i in range(n)]
        vel = ace = None
        if version >= 7:
            vel = struct.unpack("<%dH" % n, b"".join(buf[i * stride + 20:i * stride + 22]
                                                      for i in range(n)))
            ace = struct.unpack("<%dH" % n, b"".join(buf[i * stride + 22:i * stride + 24]
                                                      for i in range(n)))
        return {"pos": pos, "types": types, "areas": areas, "attrs": attrs,
                "vel": vel, "accel": ace}

    stride = 20 if version >= 5 else (18 if version >= 4 else 17)
    pos = struct.unpack_from("<%df" % (n * 4), buf, 0)
    types = buf[n * 16:n * 17]
    areas = buf[n * 17:n * 18] if version >= 4 else bytes(n)
    if version >= 5:
        attrs = list(struct.unpack_from("<%dH" % n, buf, n * 18))
    else:
        attrs = [0] * n
    return {"pos": pos, "types": types, "areas": areas, "attrs": attrs}


def bin_stats(bin_path: Path, layers: list[dict], version: int):
    total_len = 0.0
    stride = stride_of({"version": version})
    for ly in layers:
        n = ly["count"]
        if not n:
            continue
        with open(bin_path, "rb") as f:
            f.seek(ly["offset"])
            buf = f.read(n * stride)
        pos = decode_layer(buf, n, version)["pos"]
        for i in range(n):
            total_len += math.hypot(pos[i * 4 + 2] - pos[i * 4],
                                    pos[i * 4 + 3] - pos[i * 4 + 1])
    return total_len


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--gcode", required=True)
    ap.add_argument("--tolerance", type=float, default=0.01)
    a = ap.parse_args()

    d = Path(a.dir)
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    bin_path = d / "segments.bin"
    layers = index["layers"]
    stride = stride_of(index)
    fails: list[str] = []

    zs, moves, length_ref = independent_stats(Path(a.gcode))

    if index["layer_count"] != len(zs):
        fails.append("层数不一致: index=%d gcode=%d" % (index["layer_count"], len(zs)))

    bad_z = 0
    for i, (ly, z) in enumerate(zip(layers, zs)):
        if math.isnan(z):
            continue
        if abs(ly["z"] - z) > 1e-3:
            bad_z += 1
            if bad_z <= 5:
                fails.append("第 %d 层 z 不一致: %.4f vs %.4f" % (i, ly["z"], z))

    expect = sum(ly["count"] for ly in layers) * stride
    if expect != index["bin_bytes"] or bin_path.stat().st_size != expect:
        fails.append("字节数不一致: Σcount×%d=%d index=%d file=%d"
                     % (stride, expect, index["bin_bytes"],
                        bin_path.stat().st_size))

    for ly in layers:
        if sum(ly["types"].values()) != ly["count"]:
            fails.append("第 %d 层类型计数和 != count" % ly["i"])
            break

    # ranges 必须完整、连续、无重叠（前端靠它做零成本过滤）
    for ly in layers:
        if not ly["count"]:
            continue
        r = ly.get("ranges") or {}
        cursor = 0
        for t in sorted(r, key=int):
            s, c = r[t]
            if s != cursor:
                fails.append("第 %d 层 ranges 不连续: type %s start=%d 期望 %d"
                             % (ly["i"], t, s, cursor))
                break
            if c != ly["types"].get(t, -1):
                fails.append("第 %d 层 ranges[%s]=%d 与 types=%s 不一致"
                             % (ly["i"], t, c, ly["types"].get(t)))
                break
            cursor += c
        if cursor != ly["count"]:
            fails.append("第 %d 层 ranges 覆盖 %d != count %d" % (ly["i"], cursor, ly["count"]))

    b = index["bbox"]
    version = int(index.get("version") or 0)
    for idx in (0, len(layers) // 2, len(layers) - 1):
        ly = layers[idx]
        if not ly["count"]:
            continue
        with open(bin_path, "rb") as f:
            f.seek(ly["offset"])
            buf = f.read(ly["count"] * stride)
        dec = decode_layer(buf, ly["count"], version)
        pos = dec["pos"]
        if any(not math.isfinite(v) for v in pos):
            fails.append("第 %d 层出现非有限坐标" % idx)
            continue
        xs = pos[0::4] + pos[2::4]
        ys = pos[1::4] + pos[3::4]
        if (min(xs) < b[0] - 1e-3 or min(ys) < b[1] - 1e-3
                or max(xs) > b[3] + 1e-3 or max(ys) > b[4] + 1e-3):
            fails.append("第 %d 层坐标越界" % idx)
        # 类型码必须单调不减（构建时按类型排过序，过滤靠这个前提）
        types = dec["types"]
        if any(types[i] > types[i + 1] for i in range(len(types) - 1)):
            fails.append("第 %d 层类型未按序排列" % idx)

    # ---- v5 起：属性表 ----
    attrs = index.get("attrs")
    if stride >= 20:
        if not attrs:
            fails.append("v5 索引没有 attrs 表")
        else:
            nbad = 0
            for ly in layers:
                n = ly["count"]
                if not n:
                    continue
                with open(bin_path, "rb") as f:
                    f.seek(ly["offset"])
                    buf = f.read(n * stride)
                dec = decode_layer(buf, n, version)
                av = dec["attrs"]
                if any(t >= len(index["type_names"]) for t in dec["types"]):
                    fails.append("第 %d 层有越界类型码" % ly["i"])
                for ai in set(av):
                    if ai >= len(attrs):
                        nbad += 1
                if nbad > 3:
                    break
            if nbad:
                fails.append("有 %d 个属性下标越界" % nbad)
            # 属性列必须落在 index 声明的范围内
            for k, key in enumerate(index.get("attr_keys") or []):
                lo, hi = index["attr_ranges"][key]
                vals = [a[k] for a in attrs]
                if vals and (min(vals) < lo - 1e-6 or max(vals) > hi + 1e-6):
                    fails.append("attrs[%s] 超出声明范围 %s" % (key, (lo, hi)))
            print("attrs         : %d 种组合  %s" % (
                len(attrs),
                "  ".join("%s=%s" % (key, index["attr_ranges"][key])
                          for key in index.get("attr_keys") or [])))
            print("attr_avail    : %s" % index.get("attr_avail"))

    # ---- v7：运动学两列 ----
    if stride >= 24:
        vr = (index.get("attr_ranges") or {}).get("vact") or [0, 0]
        ar = (index.get("attr_ranges") or {}).get("aeff") or [0, 0]
        vmax = float(vr[1])          # attr_ranges 里已经是 mm/s
        acc_max = ar[1]
        nvel = nacc = nzero = 0
        vmax_seen = 0.0
        bad_jump = 0
        jumps = 0
        for ly in layers:
            n = ly["count"]
            if not n:
                continue
            with open(bin_path, "rb") as f:
                f.seek(ly["offset"])
                dec = decode_layer(f.read(n * stride), n, version)
            vel = dec["vel"]
            ace = dec["accel"]
            # 每类速度上限（属性表里的指令速度）
            cmd = [(attrs[dec["attrs"][i]][1] if attrs else 0) for i in range(n)]
            for i in range(n):
                v = vel[i] / 100.0
                if v > vmax + 0.05:
                    nvel += 1
                if v > vmax_seen:
                    vmax_seen = v
                if cmd[i] and v > cmd[i] + 0.5:
                    nvel += 1
                if ace[i] > acc_max + 1:
                    nacc += 1
                if ace[i] == 0:
                    nzero += 1
            # 相邻段的速度变化必须在加速度允许范围内（留 2 倍余量，
            # 因为存的是段内平均速度而不是端点速度）
            pos = dec["pos"]
            for i in range(n - 1):
                if (abs(pos[i * 4 + 2] - pos[i * 4 + 6]) > 1e-6
                        or abs(pos[i * 4 + 3] - pos[i * 4 + 7]) > 1e-6):
                    continue
                L = math.hypot(pos[i * 4 + 2] - pos[i * 4], pos[i * 4 + 3] - pos[i * 4 + 1])
                a = (attrs[dec["attrs"][i]][4] if attrs else 3000) or 3000
                v1 = vel[i] / 100.0
                v2 = vel[i + 1] / 100.0
                dv2 = abs(v2 * v2 - v1 * v1)
                if dv2 > 2.0 * 2.0 * a * L + 1.0:
                    bad_jump += 1
                jumps += 1
        print("运动学        : vmax=%.1f mm/s  amax=%.0f  零加速度段=%.1f%%"
              % (vmax_seen, acc_max, 100.0 * nzero / max(1, index["segment_count"])))
        print("                越界速度 %d 段 / 越界加速度 %d 段 / 加速度不可能的跳变 %.2f%%"
              % (nvel, nacc, 100.0 * bad_jump / max(1, jumps)))
        if nvel or nacc:
            fails.append("速度/加速度有 %d/%d 段越界" % (nvel, nacc))
        if jumps and bad_jump > 0.05 * jumps:
            fails.append("%.1f%% 的相邻段速度跳变超出加速度能力" % (100.0 * bad_jump / jumps))

    d_count = abs(moves - index["segment_count"]) / max(1, index["segment_count"])
    length_bin = bin_stats(bin_path, layers, version)
    d_len = abs(length_ref - length_bin) / max(1e-9, length_ref)

    print("gcode 独立统计 : layers=%d  extrusion=%d  length=%.1f mm" % (len(zs), moves, length_ref))
    print("preview       : layers=%d  segments=%d  length=%.1f mm  (%.2f MB, parse %d ms)"
          % (index["layer_count"], index["segment_count"], length_bin,
             index["bin_bytes"] / 1048576, index["parse_ms"]))
    print("偏差          : 段数 %.2f%% / 长度 %.3f%%  (容差 %.0f%%)"
          % (d_count * 100, d_len * 100, a.tolerance * 100))

    if d_count > a.tolerance:
        fails.append("段数偏差过大: %.2f%%" % (d_count * 100))
    if d_len > a.tolerance:
        fails.append("长度偏差过大: %.3f%%" % (d_len * 100))

    if fails:
        print("\nFAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("\nOK — 全部交叉检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
