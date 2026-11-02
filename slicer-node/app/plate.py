"""摆盘：把「模型库里的文件 + 每个对象的位置/旋转/缩放/耗材」拼成一个 3MF。

为什么不改模型的原始文件：3MF 的 build item 支持 transform，但 Orca 打开时
更认「顶点已经摆好」的模型；而且我们还要给每个对象打 extruder 标记。
所以这里直接算出最终坐标的三角形，再写一份干净的 3MF。

坐标约定：
- 单位 mm，Z=0 是热床面
- (x, y) 是对象**俯视包围盒中心**的落点，rot 绕这个中心旋转
- 摆盘页面上看到的就是这个坐标系，和 Orca 的俯视图一致
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from . import mesh as meshmod

GAP = 5.0  # 自动摆盘时对象之间的最小间隙（mm）


class PlateError(Exception):
    pass


def _key(o: dict) -> str:
    """对象 → 几何摘要/网格缓存的键：整文件 = file；3MF 子对象 = file#part。"""
    f = o.get("file") or ""
    p = o.get("part") or ""
    return f if not p else "%s#%s" % (f, p)


def _label(o: dict) -> str:
    """对象显示名：摆盘列表里的名字（部件名），退回到文件名。"""
    return str(o.get("name") or o.get("file") or "")


def _clean_objects(objects: list[dict]) -> list[dict]:
    out = []
    for i, o in enumerate(objects):
        if not isinstance(o, dict) or not o.get("file"):
            raise PlateError("第 %d 个对象缺少 file" % (i + 1))
        m = o.get("m")
        if m is None:
            m = None
        elif isinstance(m, (list, tuple)) and len(m) == 9:
            m = [float(x) for x in m]
        else:
            raise PlateError("第 %d 个对象的 m 必须是 9 个数的旋转矩阵" % (i + 1))
        out.append({
            "file": o["file"],
            "part": str(o.get("part") or ""),
            "name": str(o.get("name") or o["file"]),
            "x": float(o.get("x", 0)),
            "y": float(o.get("y", 0)),
            "z": float(o.get("z", 0)),
            "rot": float(o.get("rot", 0)) % 360.0,
            "scale": float(o.get("scale", 1.0)),
            "extruder": max(1, int(o.get("extruder", 1))),
            "m": m,
        })
    if not out:
        raise PlateError("摆盘是空的")
    if len(out) > 64:
        raise PlateError("一次最多 64 个对象")
    return out


def _rot_matrix(o: dict) -> list[float]:
    """对象的完整旋转矩阵 = Rz(rot) @ m（先贴面旋转 m，再绕世界 Z 转 rot）。"""
    R = meshmod.rot_z_matrix(o.get("rot", 0))
    m = o.get("m")
    if m is not None:
        R = meshmod.mul_m3(R, m)
    return R


def _pct(value: Any, default: float = 100.0) -> float:
    """「90%」/ ["90%"] / 90 / "90" → 90.0。取不到就返回 default。"""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip().rstrip("%")
        if not value:
            return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f > 0 else default


def shrink_of(filaments: list[dict] | None,
              overrides: dict | None = None) -> list[tuple[float, float]]:
    """每个料槽的收缩率补偿系数 (sx, sz)，100% 收缩 → 1.0（不放大）。

    为什么自己做而不用 Orca 的 filament_shrink：实测 2.4.2 的 headless CLI
    （--slice 0 + --load-filaments）**根本不应用收缩补偿**，值写进去了也不缩放
    （试过 ["90%"] / "90%" / ["90"] 三种形态，gcode 尺寸一模一样）。这个补偿
    在 Orca 里是 GUI 摆件时乘到对象矩阵上的，命令行切片没有那一步。
    所以补偿只能由我们自己乘到顶点上 —— 反正网格是我们自己烘焙的。
    """
    ov = overrides or {}
    out: list[tuple[float, float]] = []
    for fd in (filaments or []):
        fd = fd or {}
        xy = _pct(ov.get("filament_shrink", fd.get("filament_shrink")))
        z = _pct(ov.get("filament_shrinkage_compensation_z",
                        fd.get("filament_shrinkage_compensation_z")))
        out.append((100.0 / xy, 100.0 / z))
    return out


def _apply_shrink(tris, sx: float, sz: float):
    """按世界轴各向异性放大：XY 绕包围盒中心、Z 绕底面（保证还贴在床上）。"""
    if abs(sx - 1.0) < 1e-9 and abs(sz - 1.0) < 1e-9:
        return tris
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    z0 = min(zs)
    return [tuple((cx + (v[0] - cx) * sx,
                   cy + (v[1] - cy) * sx,
                   z0 + (v[2] - z0) * sz) for v in t) for t in tris]


def build(settings, models, objects: list[dict], dest: Path,
          filaments: list[dict] | None = None,
          overrides: dict | None = None) -> dict[str, Any]:
    """返回 {path, objects:[{file, extruder, w,d,h}]}。

    filaments 是耗材 profile 文档（顺序 = 槽位号），多耗材时写进工程 3MF，
    让 Orca 知道有几个料槽以及各自的颜色/类型；同时按各自的收缩率补偿尺寸
    （见 shrink_of 的说明）。overrides 里的收缩率优先于 profile（切片页的「参数覆盖」）。
    """
    shrinks = shrink_of(filaments, overrides)
    objs = _clean_objects(objects)
    built: list[dict] = []
    for o in objs:
        # 3MF 子对象：单独读该部件的网格（局部坐标）；整文件对象读合并网格
        m = models.load_mesh(settings, o["file"], o["part"] or None)
        x0, y0, z0, x1, y1, z1 = m.bbox()
        center = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        R = _rot_matrix(o)
        tris = meshmod.transform_tris3d(m.tris, o["scale"], R, center, o["x"], o["y"], o["z"])
        if shrinks:
            sx, sz = shrinks[min(max(o["extruder"], 1), len(shrinks)) - 1]
            tris = _apply_shrink(tris, sx, sz)
        mm = meshmod.Mesh(tris=tris)
        xs = [v[0] for t in tris for v in t]
        ys = [v[1] for t in tris for v in t]
        zs = [v[2] for t in tris for v in t]
        built.append({
            "name": o["name"],
            "tris": tris,
            "extruder": o["extruder"],
            "bbox": [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)],
            "file": o["file"],
        })
        del mm
    dest.parent.mkdir(parents=True, exist_ok=True)
    meshmod.write_3mf(dest, built, plate_name="plate_1", filament_docs=filaments)
    return {
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "objects": [{"file": b["file"], "extruder": b["extruder"],
                     "bbox": [round(v, 2) for v in b["bbox"]]} for b in built],
        "extruders_used": sorted({b["extruder"] for b in built}),
    }


def check_bounds(objects: list[dict], shapes: dict[str, dict], bed: dict,
                 meshes: dict | None = None) -> list[str]:
    """床外 / 重叠 检查。shapes: file → {bbox, hull}；meshes: file → Mesh（带 m 时用）。"""
    warnings: list[str] = []
    meshes = meshes or {}
    if not bed or not bed.get("w") or not bed.get("d"):
        return warnings
    bw, bd = float(bed["w"]), float(bed["d"])
    polys: list[list[tuple[float, float]]] = []
    for o in objects:
        sh = shapes.get(_key(o))
        if not sh:
            continue
        pts = _footprint_hull(sh, meshes.get(_key(o)), o)
        if not pts:
            continue
        polys.append(pts)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if bed.get("circular"):
            r = min(bw, bd) / 2
            if any(math.hypot(x, y) > r + 0.01 for x, y in pts):
                warnings.append("%s 超出圆形热床" % _label(o))
        else:
            if min(xs) < -0.01 or min(ys) < -0.01 or max(xs) > bw + 0.01 or max(ys) > bd + 0.01:
                warnings.append("%s 超出热床范围" % _label(o))
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            if _poly_overlap(polys[i], polys[j]):
                warnings.append("%s 和 %s 可能重叠" % (_label(objects[i]), _label(objects[j])))
    return warnings


def _identity(m: list[float] | None) -> bool:
    if m is None:
        return True
    I = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    return all(abs(v - I[i]) < 1e-6 for i, v in enumerate(m))


_mhull_cache: dict[tuple, list[list[float]]] = {}


def _hull2d(pts: list[tuple[float, float]], max_points: int = 48) -> list[list[float]]:
    """二维凸包（Andrew 单调链），最多保留 max_points 个点。"""
    if len(pts) < 3:
        return [[p[0], p[1]] for p in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    if len(hull) > max_points:
        step = len(hull) / max_points
        hull = [hull[int(i * step)] for i in range(max_points)]
    return [[round(p[0], 2), round(p[1], 2)] for p in hull]


def _m_hull(mesh, file: str, m: list[float]) -> list[list[float]]:
    """R=m（去中心、scale=1、不平移）投影的 XY 凸包，按 (file, m) 缓存。

    贴面后 m 基本不变，之后绕 Z 旋转 / 平移 / 缩放都只在这个凸包上做，
    不用每次重新投影几十万个顶点。
    """
    key = (file, tuple(round(v, 4) for v in m))
    hit = _mhull_cache.get(key)
    if hit is not None:
        return hit
    verts, _faces = mesh.welded()
    bbox = mesh.bbox()
    cx = (bbox[0] + bbox[3]) / 2
    cy = (bbox[1] + bbox[4]) / 2
    cz = (bbox[2] + bbox[5]) / 2
    pts = set()
    for (x, y, z) in verts:
        dx, dy, dz = x - cx, y - cy, z - cz
        pts.add((round(m[0] * dx + m[1] * dy + m[2] * dz, 2),
                 round(m[3] * dx + m[4] * dy + m[5] * dz, 2)))
    hull = _hull2d(sorted(pts))
    if len(_mhull_cache) > 128:
        _mhull_cache.clear()
    _mhull_cache[key] = hull
    return hull


def _footprint_hull(shape: dict, mesh, o: dict) -> list[tuple[float, float]]:
    """对象落地后的 XY 凸包（含平移、旋转、缩放）。"""
    hull = shape.get("hull") or []
    bbox = shape.get("bbox")
    m = o.get("m")
    if (m is None or _identity(m)) and hull and bbox:
        return _placed_outline(hull, bbox, o)
    if mesh is None:
        return []
    base = _m_hull(mesh, _key(o), m if m is not None else [1, 0, 0, 0, 1, 0, 0, 0, 1])
    a = math.radians(o.get("rot", 0))
    ca, sa = math.cos(a), math.sin(a)
    s = float(o.get("scale", 1.0))
    out = []
    for x, y in base:
        u, v = x * s, y * s
        out.append((u * ca - v * sa + o.get("x", 0), u * sa + v * ca + o.get("y", 0)))
    return out


def _placed_outline(hull: list, bbox: list | None, o: dict) -> list[tuple[float, float]]:
    if not hull or not bbox:
        return []
    cx = (bbox[0] + bbox[3]) / 2
    cy = (bbox[1] + bbox[4]) / 2
    a = math.radians(o.get("rot", 0))
    ca, sa = math.cos(a), math.sin(a)
    s = o.get("scale", 1.0)
    out = []
    for x, y in hull:
        u = (x - cx) * s
        v = (y - cy) * s
        out.append((u * ca - v * sa + o.get("x", 0), u * sa + v * ca + o.get("y", 0)))
    return out


def _poly_overlap(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    """SAT（分离轴）：凸多边形不重叠的充要条件是存在分离轴。"""
    for poly in (a, b):
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            ax, ay = -(y2 - y1), x2 - x1
            n = math.hypot(ax, ay)
            if n < 1e-9:
                continue
            ax, ay = ax / n, ay / n
            pa = [x * ax + y * ay for x, y in a]
            pb = [x * ax + y * ay for x, y in b]
            if max(pa) < min(pb) - 0.01 or max(pb) < min(pa) - 0.01:
                return False
    return True


def _shelf_pack_units(sizes: list[tuple[float, float]], bw: float, bd: float,
                      margin: float, gap: float) -> list[tuple[float, float] | None]:
    """货架式打包：sizes 是每个对象足迹外接盒的 (宽,进深)，按输入顺序传入。

    返回与输入**同序**，给的是足迹**最小角（左上角）**坐标，放不下为 None。
    用最小角而非中心：对象旋转后凸包相对中心并不对称，把中心直接放到
    margin+宽/2 会把对象推到床外。调用方再用 (ox, oy) 换算成中心。
    内部按 (进深,宽) 从大到小排以保证紧凑，但结果映射回原下标。
    """
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    pos: list[tuple[float, float] | None] = [None] * len(sizes)
    x = margin
    y = margin
    row_h = 0.0
    for i in order:
        rw, rd = sizes[i]
        # 单个对象本身比可用区域还大 → 无论怎么摆都放不下
        if rw > bw - 2 * margin + 1e-6 or rd > bd - 2 * margin + 1e-6:
            pos[i] = None
            continue
        if x + rw > bw - margin + 1e-6 and x > margin:
            x = margin
            y += row_h + gap
            row_h = 0.0
        if y + rd > bd - margin + 1e-6 and y > margin:
            pos[i] = None          # 这一行也放不下 → 未放置
            continue
        pos[i] = (x, y)            # 足迹最小角
        x += rw + gap
        row_h = max(row_h, rd)
    return pos


def _footprint_metrics(objects: list[dict], shapes: dict[str, dict],
                       meshes: dict | None) -> list[tuple[float, float, float, float]]:
    """每个对象落地凸包相对中心的 (宽, 进深, ox, oy)。

    ox/oy 是「中心 → 足迹最小角」的偏移（通常为负）。已含 obj.scale / rot。
    """
    meshes = meshes or {}
    out = []
    for o in objects:
        sh = shapes.get(_key(o)) or {}
        hull = _footprint_hull(sh, meshes.get(_key(o)), o)
        if hull:
            xs = [p[0] for p in hull]
            ys = [p[1] for p in hull]
            minx, maxx = min(xs), max(xs)
            miny, maxy = min(ys), max(ys)
            out.append((maxx - minx, maxy - miny, -minx, -miny))
        else:
            bbox = sh.get("bbox") or [0, 0, 0, 1, 1, 1]
            s = float(o.get("scale", 1))
            rw = (bbox[3] - bbox[0]) * s
            rd = (bbox[4] - bbox[1]) * s
            out.append((rw, rd, rw / 2, rd / 2))
    return out


def arrange(objects: list[dict], shapes: dict[str, dict], bed: dict,
            gap: float = GAP, meshes: dict | None = None) -> list[dict]:
    """货架式自动摆盘：按行填，放不下换行。每个对象用落地凸包占地。

    返回顺序 == 输入顺序（前端按下标写回 x/y，不能乱）。
    """
    bw = float(bed.get("w") or 200)
    bd = float(bed.get("d") or 200)
    margin = float(bed.get("margin") or 3)
    metrics = _footprint_metrics(objects, shapes, meshes)
    sizes = [(m[0], m[1]) for m in metrics]
    origins = _shelf_pack_units(sizes, bw, bd, margin, gap)
    out: list[dict] = []
    for o, (rw, rd, ox, oy), p in zip(objects, metrics, origins):
        if p is None:
            # 放不下了，塞回原点附近（前端会提示超出热床/重叠）
            out.append({**o, "x": round(margin + ox, 2), "y": round(margin + oy, 2)})
        else:
            out.append({**o, "x": round(p[0] + ox, 2), "y": round(p[1] + oy, 2)})
    return out


def compute_fit(objects: list[dict], shapes: dict[str, dict], bed: dict,
                gap: float = GAP, meshes: dict | None = None,
                iters: int = 24, lo: float = 0.1) -> dict:
    """求出能让**所有**对象都放进热床的最大统一缩放比例 factor（只在需要缩小 ≤1 时有用）。

    返回 {all_fit, factor, objects}：
      - all_fit：缩放后是否全部放得下；
      - factor：相对当前尺寸的统一缩放（≤1.0；1.0 表示当前就放得下）；
      - objects：按输入顺序给出的新落点（已按 factor 缩放后的占地打包）。
    前端把每个对象 scale *= factor、x/y 用这里的值即可。
    """
    bw = float(bed.get("w") or 200)
    bd = float(bed.get("d") or 200)
    margin = float(bed.get("margin") or 3)
    metrics = _footprint_metrics(objects, shapes, meshes)

    def try_pack(f: float):
        sizes = [(m[0] * f, m[1] * f) for m in metrics]
        origins = _shelf_pack_units(sizes, bw, bd, margin, gap)
        return all(p is not None for p in origins), origins

    ok1, pos1 = try_pack(1.0)
    if ok1:
        return {"all_fit": True, "factor": 1.0,
                "objects": [{**o, "x": round(p[0] + m[2], 2), "y": round(p[1] + m[3], 2)}
                            for o, m, p in zip(objects, metrics, pos1)]}

    ok_lo, pos_lo = try_pack(lo)
    best_f, best_pos, best_ok = lo, pos_lo, ok_lo
    if ok_lo:
        a, b = lo, 1.0
        for _ in range(iters):
            mid = (a + b) / 2
            okm, posm = try_pack(mid)
            if okm:
                a = mid
                best_f, best_pos, best_ok = mid, posm, True
            else:
                b = mid
    objs = []
    for o, m, p in zip(objects, metrics, best_pos):
        if p is None:                       # 极端：连 lo 都放不下
            objs.append({**o, "x": round(margin + 1, 2), "y": round(margin + 1, 2)})
        else:
            objs.append({**o, "x": round(p[0] + m[2] * best_f, 2),
                              "y": round(p[1] + m[3] * best_f, 2)})
    return {"all_fit": best_ok, "factor": round(best_f, 4), "objects": objs}
