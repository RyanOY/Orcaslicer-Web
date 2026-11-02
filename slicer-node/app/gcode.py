"""gcode → 逐层线段二进制（给手机端 WebGL 直接喂 typed array）。

设计取舍：
- **不重建 mesh**，只保留挤出线段。手机端 gl.LINES 画，几十万顶点一帧毫无压力，
  比在浏览器里重建几何体便宜太多。
- **按层切分**，每层一段连续字节。UI 只拉当前层 + 上下缓冲层，
  500 层的 15 MB gcode 手机上也能秒开。
- 二进制布局（每层，format ≥ 2）：
      [Float32 × 4 × count]   x1,y1,x2,y2 ...
      [Uint8   × 1 × count]   type
      [Uint8   × 1 × count]   area  （挤出截面积 / AREA_STEP，量化成 1 字节）
      [Uint8   × 1 × count]   attr  （下标 → index["attrs"] 的
                                    [挤出机, 速度, 风扇, 温度, 加速度, 抖动]）
  → format 7 起每段 24 字节（16 位置 + 1 类型 + 1 截面 + 2 attrs 下标 +
    2 真实速度 + 2 真实加速度）。属性只存下标：一层的属性组合通常不到 10 种，
    所以多一种着色依据的代价是每段 1 字节，而不是每种依据 4 字节。
    真实速度/加速度是逐段算出来的（Klipper lookahead），拼不出小表，
    直接 uint16 落盘（v×100、a×1）；4 字节对齐所以整段 24 字节。
  位置在前、类型、挤出量在后，前端三个 typed array 视图直接取，不用解包。
  挤出截面积 = E 增量 × 丝材截面积 ÷ 该段长度，前端再除以层高就是线宽 ——
  这样外墙(0.42)、稀疏填充(0.45)、gap fill(0.28) 的粗细差异是真的，不是猜的。

坐标用 gcode 原始毫米，Y 翻转/居中交给前端。
"""
from __future__ import annotations

import json
import math
import re
import time
from array import array
from dataclasses import dataclass, field
from pathlib import Path

# 线段类型码（与前端约定，改动即 +version）
# 顺序 = 前端调色板顺序 = 图例顺序，尽量对齐 OrcaSlicer 的 ExtrusionRole。
# v5 起把原来「并入内墙/其他」的角色单列：悬垂墙、底面、内部桥接、支撑界面、
# 缝隙填充、裙边、自定义 —— 否则图例里根本看不到它们（用户反馈的第一个问题）。
TYPE_OUTER_WALL = 0
TYPE_INNER_WALL = 1
TYPE_OVERHANG_WALL = 2
TYPE_SPARSE_INFILL = 3
TYPE_SOLID_INFILL = 4
TYPE_TOP_SURFACE = 5
TYPE_BOTTOM_SURFACE = 6
TYPE_BRIDGE = 7
TYPE_INTERNAL_BRIDGE = 8
TYPE_SUPPORT = 9
TYPE_SUPPORT_IFACE = 10
TYPE_GAP_FILL = 11
TYPE_BRIM = 12
TYPE_CUSTOM = 13
TYPE_OTHER = 14

# 顺序即颜色表顺序（前端 palette 与此一一对应）
TYPE_NAMES = ["outer_wall", "inner_wall", "overhang_wall", "sparse_infill",
              "solid_infill", "top_surface", "bottom_surface", "bridge",
              "internal_bridge", "support", "support_interface", "gap_fill",
              "brim", "custom", "other"]

# 中文名给 UI 用（与 OrcaSlicer 预览图例一致）
TYPE_LABELS = ["外墙", "内墙", "悬垂墙", "稀疏填充", "实心填充", "顶面", "底面",
               "桥接", "内部桥接", "支撑", "支撑界面", "缝隙填充",
               "裙边/边框", "自定义", "其他"]

# 兼容 Orca / Bambu / Prusa / Cura / Klipper 的几种 ;TYPE: 写法
_TYPE_MAP = {
    "outer wall": TYPE_OUTER_WALL,
    "external perimeter": TYPE_OUTER_WALL,
    "external wall": TYPE_OUTER_WALL,
    "inner wall": TYPE_INNER_WALL,
    "internal perimeter": TYPE_INNER_WALL,
    "perimeter": TYPE_INNER_WALL,
    "overhang wall": TYPE_OVERHANG_WALL,
    "sparse infill": TYPE_SPARSE_INFILL,
    "internal infill": TYPE_SPARSE_INFILL,
    "fill": TYPE_SPARSE_INFILL,
    "internal solid infill": TYPE_SOLID_INFILL,
    "solid infill": TYPE_SOLID_INFILL,
    "top surface": TYPE_TOP_SURFACE,
    "top solid infill": TYPE_TOP_SURFACE,
    "bottom surface": TYPE_BOTTOM_SURFACE,
    "bottom solid infill": TYPE_BOTTOM_SURFACE,
    "bridge": TYPE_BRIDGE,
    "internal bridge": TYPE_INTERNAL_BRIDGE,
    "support": TYPE_SUPPORT,
    "support material": TYPE_SUPPORT,
    "support interface": TYPE_SUPPORT_IFACE,
    "gap fill": TYPE_GAP_FILL,
    "thin wall": TYPE_GAP_FILL,
    "brim": TYPE_BRIM,
    "skirt": TYPE_BRIM,
    "skirt/brim": TYPE_BRIM,
    "brim and skirt": TYPE_BRIM,
    "custom": TYPE_CUSTOM,
}

_FLOAT = r"(-?\d+(?:\.\d+)?)"
_LAYER_LINE = re.compile(r"^;LAYER_CHANGE")
_Z_COMMENT = re.compile(r"^;Z:" + _FLOAT)
_TYPE_LINE = re.compile(r"^;TYPE:\s*(.+)$")
_MIN_SEG_SQ = 1e-6

# 挤出截面积量化步长（mm²）：0.002 对应 0.2 层高下 0.01 mm 的线宽精度。
# 典型值 0.056(gap fill)~0.09(0.45×0.2) 落到 28~45，1 字节够用。
AREA_STEP = 0.002
# 丝材截面积用 1.75 mm 的常量：解析时读不到 header 里的 diameter（Orca 写在文件尾）
FILAMENT_AREA = 3.141592653589793 * (1.75 / 2.0) ** 2


@dataclass
class Layer:
    index: int
    z: float
    offset: int = 0
    count: int = 0
    types: dict[int, int] = field(default_factory=dict)
    ranges: dict[int, tuple[int, int]] = field(default_factory=dict)  # type -> (start, len)
    mm: float = 0.0            # 本层挤出路径总长，用来摊时间/耗材
    t: float = 0.0             # 本层估算耗时（s）
    fil: float = 0.0           # 本层耗材（mm 丝长）
    bbox: list[float] | None = None
    paths: int = 0             # 本层折线（polyline）条数

    def as_dict(self) -> dict:
        return {
            "i": self.index,
            "z": round(self.z, 4),
            "offset": self.offset,
            "count": self.count,
            "types": {str(k): v for k, v in sorted(self.types.items())},
            "ranges": {str(k): list(v) for k, v in sorted(self.ranges.items())},
            "mm": round(self.mm, 2),
            "t": self.t,
            "fil": self.fil,
            "paths": self.paths,
            "bbox": [round(v, 3) for v in self.bbox] if self.bbox else None,
        }


class GcodeError(Exception):
    pass


def _arc_points(x0: float, y0: float, x1: float, y1: float,
                i_off: float, j_off: float, clockwise: bool,
                max_step_deg: float = 6.0):
    """G2/G3 → 折线采样，圆心 = 起点 + (I,J)。"""
    cx, cy = x0 + i_off, y0 + j_off
    r = math.hypot(x0 - cx, y0 - cy)
    if r < 1e-9:
        return [(x1, y1)]
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if clockwise:
        while a1 >= a0:
            a1 -= 2 * math.pi
    else:
        while a1 <= a0:
            a1 += 2 * math.pi
    sweep = a1 - a0
    steps = max(2, int(abs(math.degrees(sweep)) / max_step_deg) + 1)
    return [(cx + r * math.cos(a0 + sweep * k / steps),
             cy + r * math.sin(a0 + sweep * k / steps)) for k in range(1, steps + 1)]


def parse(gcode_path: Path, bin_path: Path, min_segment_mm: float = 0.001) -> dict:
    """解析 gcode、写二进制、返回 index dict。"""
    t0 = time.time()
    layers: list[Layer] = []
    cur: Layer | None = None
    x = y = z = e = 0.0
    z_hint = 0.0
    abs_xyz = True
    abs_e = True
    seg_type = TYPE_OTHER
    has_e_axis = False
    total = 0
    gminx = gminy = gminz = float("inf")
    gmaxx = gmaxy = gmaxz = float("-inf")
    min_sq = min_segment_mm * min_segment_mm
    type_len = [0.0] * len(TYPE_NAMES)
    e_used = 0.0
    header: dict[str, float] = {}
    # 折线（polyline）组装状态：G0 / ΔE=0 空移 / E 回抽 / 换层都会关闭当前折线
    path_open = False
    path_count = 0
    retract_splits = 0
    dedupe_skipped = 0

    # ---- 模态机器状态（v5：着色依据需要） ----
    # F 是模态的：Orca 只在速率变化时写一行 `G1 F1500`，后面的挤出指令继承它。
    f_rate = 0.0          # mm/min
    fan_pct = 0           # 0..100
    temp_c = 0.0          # 当前喷嘴目标温度
    accel = 0.0           # mm/s²
    jerk = 0.0            # mm/s（Klipper 的 square_corner_velocity 与 M205 J 同一语义）
    tool = 0              # 挤出机 / 耗材号
    tool_temp: dict[int, float] = {}
    seen_tools: set[int] = set([0])
    saw = {"f": False, "fan": False, "temp": False, "accel": False,
           "jerk": False, "tool": False}
    attr_table: list[list] = []      # [tool, speed, fan, temp, accel, jerk]
    attr_map: dict[tuple, int] = {}
    attr_overflow = 0
    # 速度量化到 1 mm/s：Orca 里有 189.0 / 180.1 这种逐 move 算出来的值，
    # 不量化的话组合数会炸（着色也看不出 0.1 mm/s 的差别）。
    ATTR_MAX = 65535

    bin_path.parent.mkdir(parents=True, exist_ok=True)

    def attr_index() -> int:
        """把当前模态状态映射成 attr 表下标（同层内高度复用，1 字节/段）。"""
        nonlocal attr_overflow
        speed = int(round(f_rate / 60.0)) if f_rate > 0 else 0
        key = (tool, speed, int(fan_pct), int(round(temp_c)),
               int(round(accel)), int(round(jerk * 10)))
        i = attr_map.get(key)
        if i is None:
            if len(attr_table) >= ATTR_MAX:
                attr_overflow += 1
                return 0
            i = len(attr_table)
            attr_map[key] = i
            attr_table.append([tool, speed, int(fan_pct), int(round(temp_c)),
                               int(round(accel)), round(jerk, 1)])
        return i

    def new_layer() -> Layer:
        nonlocal path_open, prev_end
        ly = Layer(index=len(layers), z=z_hint)
        layers.append(ly)
        # 换层一定是折线边界（哪怕 XY 刚好接上）
        path_open = False
        prev_end = None
        return ly

    # 每段的运动学输入：长度、单位方向、所属 move（F/加速度/scv 在 move 上）
    seg_len = array("f")
    seg_dx = array("f")
    seg_dy = array("f")
    seg_mv = array("I")
    seg_new = bytearray()      # 1 = 新折线起点（上一段终点 ≠ 本段起点）
    moves: list[tuple[float, float, float]] = []   # (指令速度 mm/s, 加速度, scv)
    chunks: list[tuple | None] = []                # 每层重排后的记录（等运动学算完再写）
    gseg = 0

    with open(gcode_path, "r", encoding="utf-8", errors="replace") as src:
        pos: list[float] = []
        kinds = bytearray()
        areas = bytearray()
        avals = array("H")
        prev_end: tuple[float, float] | None = None

        def flush(layer: Layer) -> None:
            nonlocal total, gseg
            n = len(kinds)
            layer.offset = 0
            layer.count = n
            if n:
                # 按类型重排：同类型连续存放 → 前端过滤时只画一段连续区间，
                # 不用在 JS 里筛 51 万条；服务端过滤也只是两次 seek。
                # 桶排序（类型码只有 0..15）比 sorted(key=...) 快一倍。
                buckets: list[list[int]] = [[] for _ in range(len(TYPE_NAMES))]
                for i in range(n):
                    buckets[kinds[i]].append(i)
                order: list[int] = []
                layer.ranges = {}
                for t, idxs in enumerate(buckets):
                    if not idxs:
                        continue
                    layer.ranges[t] = (len(order), len(idxs))
                    order.extend(idxs)
                newpos = [0.0] * (4 * n)
                newkinds = bytearray(n)
                newareas = bytearray(n)
                newavals = array("H", bytes(2 * n))
                for dst, src_i in enumerate(order):
                    j = src_i * 4
                    k = dst * 4
                    newpos[k] = pos[j]
                    newpos[k + 1] = pos[j + 1]
                    newpos[k + 2] = pos[j + 2]
                    newpos[k + 3] = pos[j + 3]
                    newkinds[dst] = kinds[src_i]
                    newareas[dst] = areas[src_i]
                    newavals[dst] = avals[src_i]
                # v7：每段 24 字节按段交错
                # （x0 y0 x1 y1 | type | area | attr | vact | aeff）。
                # 这样整份 segments.bin 就是一条可以直接当「每段实例属性」用的
                # 定长流：客户端一次上传、一条 draw call 就能画完全部层
                # （Orca 的 GCodeViewer 也是预建静态缓冲、只切 range）。
                # v7 新加的两列是 Klipper 限速后的**真实**速度/加速度，
                # 逐段不同、拼不出小表，所以直接存 uint16（v×100、a×1）。
                chunks.append((order, newpos, newkinds, newareas, newavals, gseg))
            else:
                chunks.append(None)
            gseg += n
            total += n
            pos.clear()
            kinds.clear()
            areas.clear()
            del avals[:]

        for raw in src:
            line = raw.strip()
            if not line:
                continue

            head = line[0]
            if head == ";":
                m = _TYPE_LINE.match(line)
                if m:
                    seg_type = _TYPE_MAP.get(m.group(1).strip().lower(), TYPE_OTHER)
                    continue
                if _LAYER_LINE.match(line):
                    if cur is not None:
                        flush(cur)
                    cur = new_layer()
                    continue
                if _parse_header_line(line, header):
                    continue
                m = _Z_COMMENT.match(line)
                if m:
                    v = float(m.group(1))
                    z = z_hint = v
                    if cur is not None and cur.count == 0:
                        cur.z = v
                continue

            if not (head in "GMTS" or line.startswith("SET_")):
                continue
            cmd = line.split(None, 1)[0]

            if cmd in ("G0", "G1", "G2", "G3"):
                nx, ny, nz = x, y, z
                ne = e
                got_xy = has_e = False
                i_off = j_off = 0.0
                for tok in line.split()[1:]:
                    c = tok[0]
                    if c not in "XYZEIJF":
                        continue
                    try:
                        v = float(tok[1:])
                    except ValueError:
                        continue
                    if c == "X":
                        nx = v if abs_xyz else x + v
                        got_xy = True
                    elif c == "Y":
                        ny = v if abs_xyz else y + v
                        got_xy = True
                    elif c == "Z":
                        nz = v if abs_xyz else z + v
                        z_hint = nz
                        if nz > gmaxz:
                            gmaxz = nz
                        if nz < gminz:
                            gminz = nz
                    elif c == "E":
                        ne = v if abs_e else e + v
                        has_e = True
                    elif c == "F":
                        if v > 0:
                            f_rate = v
                            saw["f"] = True
                    elif c == "I":
                        i_off = v
                    elif c == "J":
                        j_off = v

                if got_xy and cur is not None and has_e and (ne - e) > 1e-9:
                    has_e_axis = True
                    pts = ([(nx, ny)] if cmd == "G1"
                           else _arc_points(x, y, nx, ny, i_off, j_off, cmd == "G2"))
                    # 这条 move 的挤出量摊到折线各段：面积 = E增量×丝材截面 ÷ 总长。
                    # 圆弧被拆成多段时面积相同（均匀挤压），所以摊完再量化一次。
                    tot_len = 0.0
                    px, py = x, y
                    for qx, qy in pts:
                        tot_len += math.hypot(qx - px, qy - py)
                        px, py = qx, qy
                    area_q = 0
                    if tot_len > 1e-9:
                        area = (ne - e) * FILAMENT_AREA / tot_len
                        area_q = int(min(255, max(1, round(area / AREA_STEP))))
                    ai = attr_index()
                    # 这条 move 的运动学参数（缺省值 = Klipper 的常见默认值）
                    mv_i = len(moves)
                    moves.append((f_rate / 60.0 if f_rate > 0 else 60.0,
                                  accel if accel > 0 else 3000.0,
                                  jerk if jerk > 0 else 5.0))
                    px, py = x, y
                    for qx, qy in pts:
                        dx, dy = qx - px, qy - py
                        if dx * dx + dy * dy >= min_sq:
                            seglen = math.hypot(dx, dy)
                            cur.mm += seglen
                            type_len[seg_type] += seglen
                            # ---- 折线（polyline）归属 ----
                            # 一段挤出属于「当前折线」的条件是：路径还开着，且上一段
                            # 的终点就是这一段的起点。路径在 G0 / ΔE=0 的空移 /
                            # E 回抽（ΔE<0）/ 换层 / 换类型处被显式关闭，所以同一
                            # 条折线里不会混进空驶。seg_new=1 表示新折线起点。
                            if (path_open and prev_end is not None
                                    and abs(prev_end[0] - px) < 1e-6
                                    and abs(prev_end[1] - py) < 1e-6):
                                seg_new.append(0)
                            else:
                                seg_new.append(1)
                                path_open = True
                                cur.paths += 1
                                path_count += 1
                            prev_end = (qx, qy)
                            seg_len.append(seglen)
                            seg_dx.append(dx / seglen)
                            seg_dy.append(dy / seglen)
                            seg_mv.append(mv_i)
                            pos.extend((px, py, qx, qy))
                            kinds.append(seg_type)
                            areas.append(area_q)
                            avals.append(ai)
                            if px < gminx:
                                gminx = px
                            if qx < gminx:
                                gminx = qx
                            if px > gmaxx:
                                gmaxx = px
                            if qx > gmaxx:
                                gmaxx = qx
                            if py < gminy:
                                gminy = py
                            if qy < gminy:
                                gminy = qy
                            if py > gmaxy:
                                gmaxy = py
                            if qy > gmaxy:
                                gmaxy = qy
                            b = cur.bbox
                            if b is None:
                                cur.bbox = [min(px, qx), min(py, qy),
                                            max(px, qx), max(py, qy)]
                            else:
                                if px < b[0] or qx < b[0]:
                                    b[0] = min(px, qx)
                                if py < b[1] or qy < b[1]:
                                    b[1] = min(py, qy)
                                if px > b[2] or qx > b[2]:
                                    b[2] = max(px, qx)
                                if py > b[3] or qy > b[3]:
                                    b[3] = max(py, qy)
                            cur.types[seg_type] = cur.types.get(seg_type, 0) + 1
                        else:
                            # 与上一点重合（< 1 µm）：跳过这个点，但**不打断折线**
                            dedupe_skipped += 1
                        px, py = qx, qy
                else:
                    # 走到这里 = 不是「挤出」的移动：G0 快速空驶、G1 只给 F 的空走、
                    # E 回抽（ΔE<0，擦嘴/回抽）、或者换层换刀。这些都是路径分隔符：
                    # 立刻关闭当前折线，下一个挤出点会开一条新的 polyline。
                    if path_open:
                        path_open = False
                        prev_end = None
                        if has_e and ne < e:
                            retract_splits += 1

                if has_e and ne > e:
                    e_used += ne - e
                x, y, z, e = nx, ny, nz, ne
                if cur is not None and cur.count == 0 and cur.z == 0.0 and nz > 0:
                    cur.z = nz          # 没有 ;Z: 注释的方言，用移动兜底
            elif cmd == "G90":
                abs_xyz = True
            elif cmd == "G91":
                abs_xyz = False
            elif cmd == "M82":
                abs_e = True
            elif cmd == "M83":
                abs_e = False
            elif cmd == "G92":
                for tok in line.split()[1:]:
                    c = tok[0]
                    if c not in "XYZE":
                        continue
                    try:
                        v = float(tok[1:])
                    except ValueError:
                        continue
                    if c == "X":
                        x = v
                    elif c == "Y":
                        y = v
                    elif c == "Z":
                        z = z_hint = v
                    elif c == "E":
                        e = v
            elif cmd in ("T0", "T1", "T2", "T3", "T4", "T5", "T6", "T7"):
                try:
                    n_tool = int(cmd[1:])
                except ValueError:
                    continue
                tool = n_tool
                seen_tools.add(n_tool)
                saw["tool"] = True
                # 换工具时喷嘴温度跟着换（M104 缺省时用 header 里的每耗材温度）
                if not saw["temp"] and n_tool in tool_temp:
                    temp_c = tool_temp[n_tool]
            elif cmd == "M106":
                s_val = _param(line, "S")
                if s_val is not None:
                    fan_pct = max(0, min(100, int(round(s_val / 255.0 * 100))))
                    saw["fan"] = True
            elif cmd in ("M104", "M109"):
                s_val = _param(line, "S")
                t_val = _param(line, "T")
                if t_val is not None and 0 <= int(t_val) <= 7:
                    tool = int(t_val)
                    seen_tools.add(tool)
                    saw["tool"] = True
                if s_val is not None and s_val > 0:
                    temp_c = s_val
                    tool_temp[tool] = s_val
                    saw["temp"] = True
            elif cmd == "M204":
                v = _param(line, "S")
                if v is None:
                    v = _param(line, "P")
                if v is not None and v > 0:
                    accel = v
                    saw["accel"] = True
            elif cmd == "M205":
                v = _param(line, "J")
                if v is None:
                    v = _param(line, "X")
                if v is not None and v >= 0:
                    jerk = v
                    saw["jerk"] = True
            elif cmd == "SET_VELOCITY_LIMIT":
                v = _param(line, "ACCEL")
                if v is not None and v > 0:
                    accel = v
                    saw["accel"] = True
                v = _param(line, "SQUARE_CORNER_VELOCITY")
                if v is not None and v >= 0:
                    jerk = v
                    saw["jerk"] = True
            elif cmd == "SET_PRESSURE_ADVANCE":
                continue

        if cur is not None:
            flush(cur)

    # ---- 运动学：Klipper 的 lookahead 限速 → 每段真实速度/加速度 ----
    vact, aeff = _velocity_profile(seg_len, seg_dx, seg_dy, seg_mv, seg_new, moves)

    # 现在才落盘：vact/aeff 是逐段算出来的，必须等整条路径算完。
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bin_path, "wb") as out:
        for k, ch in enumerate(chunks):
            ly = layers[k] if k < len(layers) else None
            if ly is not None:
                ly.offset = out.tell()
            if ch is None:
                continue
            order, newpos, newkinds, newareas, newavals, gbase = ch
            n = len(newkinds)
            if not n:
                continue
            rec = bytearray(24 * n)
            rv = memoryview(rec).cast("f")
            pv = memoryview(array("f", newpos))
            for j in range(4):
                rv[j::6] = pv[j::4]          # 位置 4 个 float 交叉写入
            rec[16::24] = bytes(newkinds)
            rec[17::24] = bytes(newareas)
            av = newavals.tobytes()
            rec[18::24] = av[0::2]
            rec[19::24] = av[1::2]
            vq = array("H", bytes(2 * n))
            aq = array("H", bytes(2 * n))
            for dst, src_i in enumerate(order):
                gi = gbase + src_i
                vq[dst] = vact[gi]
                aq[dst] = aeff[gi]
            vb = vq.tobytes()
            ab = aq.tobytes()
            rec[20::24] = vb[0::2]
            rec[21::24] = vb[1::2]
            rec[22::24] = ab[0::2]
            rec[23::24] = ab[1::2]
            out.write(rec)

    bbox = None
    if gminx != float("inf"):
        bbox = [round(gminx, 3), round(gminy, 3), round(gminz, 3),
                round(gmaxx, 3), round(gmaxy, 3), round(gmaxz, 3)]

    type_totals = [0] * len(TYPE_NAMES)
    for ly in layers:
        for t, c in ly.types.items():
            type_totals[t] += c

    # ---- 温度兜底 ----
    # Orca 只在换温时写 M104，单挤出机 PLA 全程没有 M104，
    # 温度只出现在文件尾注释里（`; nozzle_temperature = 205,220,220`）。
    # attr 表很小，这里直接按工具号回填，不用回头重扫 gcode。
    hdr_temps = header.get("nozzle_temp") or []
    if not tool_temp and hdr_temps:
        for i, v in enumerate(hdr_temps):
            tool_temp[i] = v
        if not saw["temp"]:
            for ent in attr_table:
                ent[3] = int(round(tool_temp.get(ent[0], hdr_temps[0])))
            saw["temp"] = True

    # ---- 各着色依据的取值范围 / 是否可用 ----
    def rng(k: int) -> list[float]:
        vals = [e[k] for e in attr_table]
        if k == 1:                      # 速度为 0 表示「没读到 F」，不参与范围
            vals = [v for v in vals if v > 0] or [0.0]
        return [min(vals or [0.0]), max(vals or [0.0])]

    attr_ranges = {
        "tool": [min(seen_tools), max(seen_tools)],
        "speed": rng(1), "fan": rng(2), "temp": rng(3),
        "accel": rng(4), "jerk": rng(5),
    }
    # 运动学算出来的两列（逐段不同，走单独的 uint16 通道，不占 attr 表）
    v_max = (max(vact) / 100.0) if len(vact) else 0.0
    a_max = float(max(aeff)) if len(aeff) else 0.0
    attr_ranges["vact"] = [0.0, round(v_max, 1)]
    attr_ranges["aeff"] = [0.0, round(a_max, 0)]
    attr_avail = {
        "tool": len(seen_tools) > 1,
        "speed": saw["f"] and attr_ranges["speed"][1] > 0,
        "fan": saw["fan"],
        "temp": saw["temp"] and attr_ranges["temp"][1] > 0,
        "accel": saw["accel"] and attr_ranges["accel"][1] > 0,
        "jerk": saw["jerk"] and attr_ranges["jerk"][1] > 0,
        "vact": v_max > 0,
        "aeff": a_max > 0,
    }

    # ---- 时间 / 耗材：优先用 gcode 头部自带的统计，摊到每层 ----
    diameter = header.get("diameter") or 1.75
    density = header.get("density") or 1.24
    total_mm = sum(ly.mm for ly in layers)
    fil_mm = header.get("filament_mm")
    fil_src = "header" if fil_mm else None
    if not fil_mm:
        fil_mm, fil_src = e_used, "e_axis"
    if not fil_mm:
        fil_mm, fil_src = total_mm * 0.084, "estimated"   # 0.4×0.2 线 ≈ 0.08 mm²
    cross = math.pi * (diameter / 2.0) ** 2                 # mm²
    grams = header.get("grams")
    if grams is None:
        grams = fil_mm * cross * density / 1000.0          # mm³→cm³ × g/cm³
    time_s = header.get("time_s")
    if time_s is None and total_mm > 0:
        time_s = total_mm / 60.0                             # 60 mm/s 兜底
    for ly in layers:
        if total_mm > 0:
            share = ly.mm / total_mm

            ly.t = round((time_s or 0.0) * share, 1)
            ly.fil = round(fil_mm * share, 2)
        else:
            ly.t = 0.0
            ly.fil = 0.0

    # 全模型概览时的建议抽层步长：把总段数压到 ~15 万，同时至少留 30 层
    stride = 1
    if total > 150_000 and layers:
        stride = max(1, math.ceil(total / 150_000))
        stride = min(stride, max(1, len(layers) // 30))

    return {
        "version": 7,
        "area_step": AREA_STEP,
        "stride": 24,
        "type_names": TYPE_NAMES,
        "type_labels": TYPE_LABELS,
        "attr_keys": ["tool", "speed", "fan", "temp", "accel", "jerk"],
        "attr_labels": ["挤出机", "速度", "风扇", "温度", "加速度", "抖动"],
        "attr_units": ["", "mm/s", "%", "°C", "mm/s²", "mm/s"],
        "attrs": attr_table,
        "attr_ranges": attr_ranges,
        "attr_avail": attr_avail,
        "attr_overflow": attr_overflow,
        # 运动学回放用的机器参数（缺省即 Klipper 默认值）
        "kinematics": {
            "accel": max((m[1] for m in moves), default=0.0),
            "scv": max((m[2] for m in moves), default=0.0),
            "accel_seen": saw["accel"],
            "scv_seen": saw["jerk"],
        },
        "tool_temps": [tool_temp[k] for k in sorted(tool_temp)],
        "totals": {
            "mm": round(total_mm, 1),
            "filament_mm": round(fil_mm, 1),
            "grams": round(grams, 2),
            "time_s": round(time_s or 0.0, 1),
            "time_source": "header" if header.get("time_s") else "estimated",
            "filament_source": fil_src,
            "diameter": diameter,
            "density": density,
        },
        "type_lengths": {str(i): round(v, 1) for i, v in enumerate(type_len) if v},
        "bbox": bbox,
        "bin_bytes": bin_path.stat().st_size,
        "segment_count": total,
        "path_count": path_count,
        "retract_splits": retract_splits,
        "dedupe_skipped": dedupe_skipped,
        "layer_count": len(layers),
        "type_totals": {str(i): c for i, c in enumerate(type_totals) if c},
        "overview_stride": stride,
        "has_e_axis": has_e_axis,
        "parse_ms": int((time.time() - t0) * 1000),
        "layers": [ly.as_dict() for ly in layers],
    }


def _junction_v2(ux: float, uy: float, wx: float, wy: float,
                 scv: float, vmax2: float) -> float:
    """拐角允许的速度平方（Klipper square_corner_velocity 模型）。

    v_j = scv · √((1+cosθ)/(1−cosθ))，θ 为转角：
      直线(cos=1) → ∞（由巡航速度封顶）；90° → scv；反向(cos=−1) → 0。
    这和 Klipper 文档里「scv = 过 90° 转角的速度」是同一语义。
    """
    dot = ux * wx + uy * wy
    if dot > 0.9999:
        return vmax2
    if dot < -0.9999:
        return 0.0
    return min(vmax2, scv * scv * (1.0 + dot) / (1.0 - dot))


def _velocity_profile(seg_len, seg_dx, seg_dy, seg_mv, seg_new, moves):
    """逐段重放 Klipper 的梯形速度规划。

    做法就是 Klipper/Marlin 的 lookahead 三遍扫描（前向 → 反向 → 再前向）：
      1. 每段按 `max_accel` 从入口速度能爬到多快，出口速度再被拐角限速压住；
      2. 反向扫一遍，保证到下一段之前刹得住（这就是「转角前先减速」）；
      3. 再前向扫一遍，把反向压下来的入口速度传下去。
    输出每段的 (平均速度, 有效加速度)，量化成 uint16 直接进二进制。
    长直段被 Orca 按 0.4 mm 一段切好，所以逐段颜色就是加减速的坡度；
    一整段内的速度变化取平均（面积 = 距离/时间，误差在视觉上看不出来）。
    """
    n = len(seg_len)
    vq = array("H", bytes(2 * n))
    aq = array("H", bytes(2 * n))
    if not n:
        return vq, aq
    vin = array("f", bytes(4 * n))
    vout = array("f", bytes(4 * n))
    sqrt = math.sqrt
    big = 1e9

    def limit(i: int, v: float) -> float:
        """第 i 段出口速度上限：下一段拐角 + 折线断开则必须停。"""
        if i + 1 >= n:
            return v
        if seg_new[i + 1]:
            return 0.0
        vc, _, scv = moves[seg_mv[i]]
        vn, _, scv_n = moves[seg_mv[i + 1]]
        vmax2 = min(vc, vn) ** 2
        j2 = _junction_v2(seg_dx[i], seg_dy[i], seg_dx[i + 1], seg_dy[i + 1],
                          min(scv, scv_n), vmax2)
        return min(v, sqrt(j2)) if j2 < v * v else v

    for _pass in range(2):
        v = 0.0
        for i in range(n):
            if seg_new[i]:
                v = 0.0
            vc, acc, _scv = moves[seg_mv[i]]
            L = seg_len[i]
            vo = vc
            if L > 0.0:
                reach = sqrt(v * v + 2.0 * acc * L)
                if reach < vo:
                    vo = reach
            vo = limit(i, vo)
            vin[i] = v
            vout[i] = vo
            v = vo

        # 反向：刹得住吗？
        for i in range(n - 1, -1, -1):
            vc, acc, _scv = moves[seg_mv[i]]
            L = seg_len[i]
            nxt = vin[i + 1] if (i + 1 < n and not seg_new[i + 1]) else vout[i]
            if L > 0.0:
                allow = sqrt(nxt * nxt + 2.0 * acc * L)
                if vin[i] > allow:
                    vin[i] = allow
                reach = sqrt(vin[i] * vin[i] + 2.0 * acc * L)
                if vout[i] > reach:
                    vout[i] = reach

    for i in range(n):
        vc, acc, _scv = moves[seg_mv[i]]
        L = seg_len[i]
        vi, vo = vin[i], vout[i]
        if L <= 1e-9:
            vm = 0.0
            ae = 0.0
        else:
            # 段内重新规划一次梯形：两端速度已知，中途可能冲到一个峰值
            # （长段两头都要停时，中间是匀速平台 —— 直接取两端平均会把
            # 20 mm 的长线算成 0.005 mm/s，颜色和时间全错）。
            vp2 = 0.5 * (2.0 * acc * L + vi * vi + vo * vo)
            vp = sqrt(vp2) if vp2 > 0.0 else 0.0
            if vp > vc:
                vp = vc
            if vp < vi:
                vp = vi
            if vp < vo:
                vp = vo
            if acc > 0.0 and vp > 1e-6:
                t = (2.0 * vp - vi - vo) / acc
                d = (2.0 * vp * vp - vi * vi - vo * vo) / (2.0 * acc)
                if L - d > 1e-9:
                    t += (L - d) / vp
            else:
                t = L / vp if vp > 1e-6 else 0.0
            vm = (L / t) if t > 0.0 else vp
            ae = abs(vo * vo - vi * vi) / (2.0 * L)
            if ae > big:
                ae = big
        q = int(vm * 100.0 + 0.5)
        vq[i] = 65535 if q > 65535 else (0 if q < 0 else q)
        qa = int(ae + 0.5)
        aq[i] = 65535 if qa > 65535 else (0 if qa < 0 else qa)
    return vq, aq


def _param(line: str, letter: str) -> float | None:
    """取 gcode 参数值：`M106 S255` → 255，`SQUARE_CORNER_VELOCITY=9` → 9。"""
    ln = len(letter)
    for tok in line.split()[1:]:
        if tok[:ln] != letter:
            continue
        if len(tok) > ln and tok[ln] not in "=0123456789.-+":
            continue
        tail = tok[ln:].lstrip("=")
        try:
            return float(tail)
        except ValueError:
            continue
    return None


def write_index(index: dict, index_path: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
                          encoding="utf-8")

def _parse_header_line(line: str, header: dict) -> bool:
    """抓 gcode 自带的统计行（Orca/Prusa 写在文件尾部，Bambu 在头部）。

    形如：
        ; filament used [mm] = 28673.34
        ; filament used [g] = 85.52
        ; total estimated time: 2h 36m 14s
        ; estimated printing time (normal mode) = 2h 36m 14s
        ; filament_density: 1.24
    """
    if not line.startswith("; "):
        return False
    if line.startswith("; filament used [mm]") or line.startswith("; total filament used [mm]"):
        v = _num_after(line)
        if v is not None:
            header["filament_mm"] = v
        return True
    if line.startswith("; filament used [g]") or line.startswith("; total filament used [g]"):
        v = _num_after(line)
        if v is not None:
            header["grams"] = v
        return True
    if line.startswith("; estimated printing time") or line.startswith("; total estimated time"):
        v = _parse_duration(line)
        if v is not None:
            header["time_s"] = v
        return True
    if line.startswith("; filament_diameter"):
        v = _num_after(line)
        if v:
            header["diameter"] = v
        return True
    if line.startswith("; nozzle_temperature =") or line.startswith("; nozzle_temperature:"):
        i = max(line.find("="), line.find(":"))
        vals: list[float] = []
        for part in line[i + 1:].split(","):
            try:
                vals.append(float(part.strip()))
            except ValueError:
                break
        if vals:
            header["nozzle_temp"] = vals
        return True
    if line.startswith("; filament_density"):
        v = _num_after(line)
        if v:
            header["density"] = v
        return True
    return False


def _num_after(line: str) -> float | None:
    """取 '= 1.75' 或 ': 1.75' 后面的第一个数字。"""
    for sep in ("=", ":"):
        i = line.rfind(sep)
        if i >= 0:
            tail = line[i + 1:].split(",")[0].strip()
            try:
                return float(tail)
            except ValueError:
                return None
    return None


_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([dhms])")


def _parse_duration(text: str) -> float | None:
    """'1d 2h 3m 4s' → 秒。"""
    total = 0.0
    for val, unit in _DUR_RE.findall(text):
        total += float(val) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total or None
