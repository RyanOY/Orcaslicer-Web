"""网格读取（纯 stdlib）：STL(bin/ascii)、OBJ、3MF → 三角形列表。

摆盘只需要三样东西：外轮廓（给 2D 画布画占地）、包围盒（算摆放和居中）、
三角形（拼出新 3MF 喂给 orca）。所以这里不做半边结构、不做合并顶点，
读到什么就是什么 —— 最多几万个三角形，Python 也够。
"""
from __future__ import annotations

import json
import math
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

Vec3 = tuple[float, float, float]
Tri = tuple[Vec3, Vec3, Vec3]

NS = {
    "m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02",
    "p": "http://schemas.microsoft.com/3dmanufacturing/production/2015/02",
}


class MeshError(Exception):
    pass


@dataclass
class Mesh:
    tris: list[Tri] = field(default_factory=list)
    # 源文件里可能带多个对象，这里记一下每个对象的名字（摆盘时显示用）
    name: str = ""
    source: str = ""
    # 3MF 子对象的 id（整文件/单对象时为 ""）
    part_id: str = ""
    # 惰性缓存的焊接顶点/面（给 3D 渲染 + 任意朝向占地用），避免重复焊接大网格
    _welded: object = field(default=None, repr=False, compare=False)

    @property
    def count(self) -> int:
        return len(self.tris)

    def welded(self) -> tuple[list[Vec3], list[tuple[int, int, int]]]:
        if self._welded is None:
            self._welded = index_mesh(self.tris)
        return self._welded

    def bbox(self) -> tuple[float, float, float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        for t in self.tris:
            for v in t:
                xs.append(v[0])
                ys.append(v[1])
                zs.append(v[2])
        if not xs:
            raise MeshError("网格是空的")
        return min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)

    def footprint(self, max_points: int = 48) -> list[list[float]]:
        """XY 凸包 —— 2D 画布上画占地轮廓、判断越界/重叠都用它。"""
        pts = sorted({(round(v[0], 3), round(v[1], 3)) for t in self.tris for v in t})
        if len(pts) < 3:

            def r(v: float) -> float:
                return round(v, 3)

            return [[r(p[0]), r(p[1])] for p in pts]
        hull = _convex_hull(pts)
        if len(hull) > max_points:
            step = len(hull) / max_points
            hull = [hull[int(i * step)] for i in range(max_points)]
        return [[round(p[0], 3), round(p[1], 3)] for p in hull]


def _convex_hull(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    # Andrew monotone chain
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
    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------- STL

def load_stl(path: Path) -> Mesh:
    raw = path.read_bytes()
    if len(raw) >= 84:
        n = struct.unpack_from("<I", raw, 80)[0]
        if 84 + n * 50 == len(raw) and n > 0:
            tris: list[Tri] = []
            off = 84
            for _ in range(n):
                vals = struct.unpack_from("<12f", raw, off)
                tris.append(((vals[3], vals[4], vals[5]),
                             (vals[6], vals[7], vals[8]),
                             (vals[9], vals[10], vals[11])))
                off += 50
            return Mesh(tris=tris, name=path.stem)
    return _load_ascii_stl(raw, path)


def _load_ascii_stl(raw: bytes, path: Path) -> Mesh:
    tris: list[Tri] = []
    cur: list[Vec3] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        s = line.strip()
        if s.startswith("vertex"):
            parts = s.split()
            if len(parts) >= 4:
                cur.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif s.startswith("endfacet"):
            if len(cur) == 3:
                tris.append((cur[0], cur[1], cur[2]))
            cur = []
    if not tris:
        raise MeshError("不是有效的 STL: %s" % path.name)
    return Mesh(tris=tris, name=path.stem)


# ---------------------------------------------------------------- OBJ

def load_obj(path: Path) -> Mesh:
    verts: list[Vec3] = []
    tris: list[Tri] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            p = line.split()
            verts.append((float(p[1]), float(p[2]), float(p[3])))
        elif line.startswith("f "):
            idx = []
            for tok in line.split()[1:]:
                i = int(tok.split("/")[0])
                idx.append(i - 1 if i > 0 else len(verts) + i)
            for k in range(1, len(idx) - 1):  # 扇形三角化
                tris.append((verts[idx[0]], verts[idx[k]], verts[idx[k + 1]]))
    if not tris:
        raise MeshError("OBJ 里没有面: %s" % path.name)
    return Mesh(tris=tris, name=path.stem)


# ---------------------------------------------------------------- 3MF

def _apply(m: list[float], v: Vec3) -> Vec3:
    """行向量右乘 4×3（第 4 行是平移）。"""
    x, y, z = v
    return (x * m[0] + y * m[3] + z * m[6] + m[9],
            x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11])


IDENT = [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 0, 0, 0]


def _compose(a: list[float], b: list[float]) -> list[float]:
    """先 a 后 b（行向量：p·a·b）。"""
    out = [0.0] * 12
    for r in range(3):
        for c in range(3):
            out[r * 3 + c] = sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
    for c in range(3):
        out[9 + c] = sum(a[9 + k] * b[k * 3 + c] for k in range(3)) + b[9 + c]
    return out


def _xform(el: ET.Element, attr: str = "transform") -> list[float]:
    raw = el.get(attr)
    if not raw:
        return list(IDENT)
    vals = [float(x) for x in raw.split()]
    return vals if len(vals) == 12 else list(IDENT)


def _load_3mf(path: Path) -> tuple[Mesh, list[Mesh]]:
    """解析 3MF，返回 (整文件合并网格, 子对象网格列表)。

    3MF 有两种结构，都要吃：
    1. 一份 3dmodel.model 里直接写 mesh（我们自己写的、PrusaSlicer 的）
    2. 对象只是 <components>，真正的 mesh 在 3D/Objects/*.model 里（Bambu/Orca 工程文件）

    子对象 = 每个 build item（没有 build item 时是每个顶层 object）。
    每个子对象带上它的 build item 变换（保留打印朝向；原始摆放的平移随后由
    摆盘的「按各自包围盒重新居中/贴床」吸收），这样能像独立 STL 一样单独
    移动/旋转/缩放，又不会丢掉 3MF 里已经调好的朝向。
    """
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise MeshError("3MF 不是 zip: %s" % e) from e
    model_names = [n for n in zf.namelist() if n.lower().endswith(".model")]
    if not model_names:
        raise MeshError("3MF 里没有 .model 文件")

    trees: dict[str, ET.Element] = {}
    for name in model_names:
        try:
            root = ET.fromstring(zf.read(name))
        except ET.ParseError:
            continue
        for el in root.iter():
            # 命名空间变体太多（core / production），标签和属性都剥成 local name，
            # 不然 <component p:path="/3D/Objects/object_1.model"> 里的 path 取不到
            if "}" in el.tag:
                el.tag = el.tag.split("}", 1)[1]
            if any("{" in k for k in el.attrib):
                el.attrib = {(k.split("}", 1)[1] if "{" in k else k): v
                             for k, v in el.attrib.items()}
        trees[name] = root

    main = next((n for n in model_names if n.lower().endswith("3dmodel.model")), model_names[0])

    # Bambu/Orca 的工程 3MF 把「修改器/支撑阻挡」也画成零件，它们不是实体，
    # 混进网格会把切片搞崩（empty layer 报错）。model_settings.config 里
    # 每个 part 有 subtype，只保留 normal_part。
    subtypes: dict[str, dict[str, str]] = {}
    cfg_name = next((n for n in zf.namelist()
                     if n.lower().endswith("model_settings.config")), None)
    if cfg_name:
        try:
            cfg_root = ET.fromstring(zf.read(cfg_name))
            for el in cfg_root.iter():
                if "}" in el.tag:
                    el.tag = el.tag.split("}", 1)[1]
            for obj in cfg_root.findall("object"):
                oid = obj.get("id") or ""
                parts: dict[str, str] = {}
                for part in obj.findall("part"):
                    pid = part.get("id")
                    if pid:
                        parts[pid] = part.get("subtype") or "normal_part"
                if parts:
                    subtypes[oid] = parts
        except ET.ParseError:
            subtypes = {}

    def objects_of(name: str) -> dict[str, ET.Element]:
        root = trees.get(name)
        if root is None:
            return {}
        return {o.get("id") or "": o for o in root.findall("resources/object")}

    def mesh_of(obj: ET.Element) -> tuple[list[Vec3], list[tuple[int, int, int]]]:
        vs = [(float(v.get("x", 0)), float(v.get("y", 0)), float(v.get("z", 0)))
              for v in obj.findall("mesh/vertices/vertex")]
        ts = [(int(t.get("v1", 0)), int(t.get("v2", 0)), int(t.get("v3", 0)))
              for t in obj.findall("mesh/triangles/triangle")]
        return vs, ts

    def norm(name: str) -> str:
        return name.lstrip("/")

    def flatten(model_name: str, oid: str, outer: list[float], depth: int = 0,
                root: str | None = None) -> list[Tri]:
        """把一个对象（可能是组件树）摊平成最终坐标的三角形。"""
        if depth > 6:
            return []
        root = root if root is not None else oid
        obj = objects_of(model_name).get(oid)
        if obj is None:
            return []
        out: list[Tri] = []
        vs, ts = mesh_of(obj)
        if vs and ts:
            pv = [_apply(outer, v) for v in vs] if outer != IDENT else vs
            out.extend((pv[a], pv[b], pv[c]) for a, b, c in ts)
        for comp in obj.findall("components/component"):
            child = norm(comp.get("path") or model_name)
            if not child.lower().endswith(".model"):
                child = model_name
            cid = comp.get("objectid") or ""
            kind = (subtypes.get(root) or {}).get(cid)
            if kind and kind != "normal_part":
                continue          # 修改器/阻挡块：不是实体
            m = _compose(_xform(comp), outer)
            out.extend(flatten(child, cid, m, depth + 1, root))
        return out

    items = trees[main].findall("build/item") if main in trees else []
    if items:
        entries = [(it.get("objectid") or "", _xform(it)) for it in items]
    else:
        entries = [(oid, list(IDENT)) for oid in objects_of(main)]

    def part_label(oid: str, idx: int) -> str:
        obj = objects_of(main).get(oid)
        nm = obj.get("name") if obj is not None else None
        return (nm or "").strip() or ("部件 %d" % (idx + 1))

    parts: list[Mesh] = []
    merged_tris: list[Tri] = []
    for idx, (oid, xf) in enumerate(entries):
        # 子对象 = 每个 build item，带上它自己的变换（保留打印朝向 + 原始摆放）。
        # 摆盘时 transform_tris3d 会按各自包围盒重新居中/贴床，所以这里的
        # 平移不影响最终摆放，只决定朝向与形状。
        tris = flatten(main, oid, xf)
        if not tris:
            continue
        parts.append(Mesh(tris=tris, name=part_label(oid, idx), source=path.name, part_id=oid))
        merged_tris.extend(tris)

    if not merged_tris:
        raise MeshError("3MF 里没有可用的网格（可能是纯组件/装配体）")
    merged = Mesh(tris=merged_tris, name=path.stem, source=path.name)
    return merged, parts


def load_3mf(path: Path) -> Mesh:
    """整文件合并网格（单对象时的默认视图；多对象时是它们的原始摆放合并）。"""
    return _load_3mf(path)[0]


def load_with_parts(path: Path) -> tuple[Mesh, list[Mesh]]:
    """一次性解析：返回 (整文件合并网格, 子对象网格列表)。"""
    suf = path.suffix.lower()
    if suf == ".3mf":
        merged, parts = _load_3mf(path)
        return merged, (parts if parts else [merged])
    m = load(path)
    return m, [m]


def load_parts(path: Path) -> list[Mesh]:
    """把模型文件拆成可独立摆放的子网格：3MF 多对象 → 多个；STL/OBJ → 1 个。"""
    return load_with_parts(path)[1]


def load(path: Path) -> Mesh:
    suf = path.suffix.lower()
    if suf == ".stl":
        return load_stl(path)
    if suf == ".obj":
        return load_obj(path)
    if suf == ".3mf":
        return load_3mf(path)
    raise MeshError("不支持的网格格式 %r（STL/OBJ/3MF）" % suf)


# ---------------------------------------------------------------- 写 3MF

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""


PALETTE = ["#FF7D38", "#4D80BA", "#4CAF7D", "#C86BD8", "#E8B23A", "#7EC8E3",
           "#E0564F", "#9AD34F"]

PROJECT_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<model unit="millimeter" xml:lang="zh-CN" '
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/02">')

OBJECT_MODEL = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<model unit="millimeter" '
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
    '<resources><object id="1" type="model">%s</object></resources>'
    "<build/></model>")


def _indexed_mesh(tris: list[Tri]) -> str:
    """索引化 <mesh>：按坐标去重顶点。

    直接写「每个三角形三个新顶点」的汤，Orca 的 3MF 解析**不会**替你焊接，
    结果 `manifold=no` / 每个三角形算一个 part，切片时报 empty layer。
    所以这里必须自己合并顶点。
    """
    index: dict[tuple[float, float, float], int] = {}
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for tri in tris:
        idx = []
        for v in tri:
            key = (round(v[0], 4), round(v[1], 4), round(v[2], 4))
            j = index.get(key)
            if j is None:
                j = len(verts)
                index[key] = j
                verts.append(key)
            idx.append(j)
        if len(set(idx)) == 3:
            faces.append((idx[0], idx[1], idx[2]))
    out = ["<mesh><vertices>"]
    out.extend('<vertex x="%.4f" y="%.4f" z="%.4f"/>' % v for v in verts)
    out.append("</vertices><triangles>")
    out.extend('<triangle v1="%d" v2="%d" v3="%d"/>' % f for f in faces)
    out.append("</triangles></mesh>")
    return "".join(out)


def write_3mf(path: Path, objects: list[dict], plate_name: str = "plate",
              filament_docs: list[dict] | None = None) -> None:
    """objects: [{name, tris, extruder}] —— 三角形已是最终坐标（含摆放变换）。

    单耗材 → 朴素 3MF（对象内联 mesh，Orca 当模型文件导入）。
    多耗材 → 同样的内联网格，另加 Metadata/model_settings.config（每个对象的
    挤出机号）和 project_settings.config（几个耗材槽）。不拆外部文件，原因见
    _write_project 的说明 —— 拆文件会让 Orca 把所有部件当成同一份网格。
    """
    multi = any((o.get("extruder") or 1) > 1 for o in objects)
    if multi:
        _write_project(path, objects, plate_name, filament_docs or [])
    else:
        _write_flat(path, objects, plate_name)


def _write_flat(path: Path, objects: list[dict], plate_name: str) -> None:
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="zh-CN" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
        '<metadata name="Application">orca-web-slicer</metadata>',
        '<metadata name="Title">%s</metadata>' % _xml(plate_name),
        "<resources>",
    ]
    for i, obj in enumerate(objects, start=1):
        parts.append('<object id="%d" type="model" name="%s">' % (i, _xml(obj["name"])))
        parts.append(_indexed_mesh(obj["tris"]))
        parts.append("</object>")
    parts.append("</resources><build>")
    for i in range(1, len(objects) + 1):
        parts.append('<item objectid="%d" printable="1"/>' % i)
    parts.append("</build></model>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("3D/3dmodel.model", "".join(parts))


def _first(value) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def _write_project(path: Path, objects: list[dict], plate_name: str,
                   filament_docs: list[dict]) -> None:
    """多耗材工程布局：网格全部内联在主 3dmodel.model，元数据放 Metadata/。

    踩过的坑：早先按「主模型只放 <components>、网格拆到
    3D/Objects/object_N.model、component 用 p:path 引用」来写，结果
    OrcaSlicer 2.4.2 解析 component 时**按 objectid 在全局 id 空间里找**，
    不看 p:path；每个外部文件的网格都叫 id="1"，互相覆盖 →
    摆盘上两个不同部件被切成两个一模一样的部件。
    所以干脆不拆文件：每个对象一个内联 <mesh>，id 唯一。
    """
    main = [PROJECT_HEAD,
            '<metadata name="Application">orca-web-slicer</metadata>',
            '<metadata name="Title">%s</metadata>' % _xml(plate_name),
            "<resources>"]
    for i, obj in enumerate(objects, start=1):
        main.append('<object id="%d" type="model" name="%s">%s</object>'
                    % (i, _xml(obj["name"]), _indexed_mesh(obj["tris"])))
    main.append("</resources><build>")
    for i in range(1, len(objects) + 1):
        main.append('<item objectid="%d" printable="1"/>' % i)
    main.append("</build></model>")

    cfg = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for i, obj in enumerate(objects, start=1):
        cfg.append('<object id="%d">' % i)
        cfg.append('  <metadata key="name" value="%s"/>' % _xml(obj["name"]))
        cfg.append('  <metadata key="extruder" value="%d"/>' % (obj.get("extruder") or 1))
        cfg.append('  <part id="1" subtype="normal_part">')
        cfg.append('    <metadata key="name" value="%s"/>' % _xml(obj["name"]))
        cfg.append("  </part></object>")
    cfg.append("</config>")

    # 工程文件里要有一份「耗材槽」定义，Orca 才知道有几种料。
    # 真正的参数仍由 --load-filaments 覆盖，这里只给数量/颜色/类型。
    count = max([len(filament_docs)] + [(o.get("extruder") or 1) for o in objects] + [1])
    names, colours, types, ids = [], [], [], []
    for i in range(count):
        doc = filament_docs[i] if i < len(filament_docs) and isinstance(filament_docs[i], dict) else {}
        names.append(_first(doc.get("name")) or "Filament %d" % (i + 1))
        colours.append(_first(doc.get("filament_colour")) or PALETTE[i % len(PALETTE)])
        types.append(_first(doc.get("filament_type")) or "PLA")
        ids.append((_first(doc.get("filament_id")) or "F%d" % i).upper())
    project = {
        "filament_settings_id": names,
        "filament_colour": colours,
        "filament_type": types,
        "filament_ids": ids,
        "single_extruder_multi_material": "1",
        "print_settings_id": "orca-web",
    }

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("3D/3dmodel.model", "".join(main))
        zf.writestr("Metadata/model_settings.config", "".join(cfg))
        zf.writestr("Metadata/project_settings.config",
                    json.dumps(project, ensure_ascii=False, indent=4))




def _xml(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def rot_z_matrix(deg: float) -> list[float]:
    """绕 Z 轴旋转矩阵（列向量约定，行主序 9 元组 [r00..r22]）。

    与旧 transform_tris 的 rot 方向一致：x' = cos·x − sin·y, y' = sin·x + cos·y。
    """
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [ca, -sa, 0.0, sa, ca, 0.0, 0.0, 0.0, 1.0]


def mul_m3(a: list[float], b: list[float]) -> list[float]:
    """3×3 矩阵乘（列向量约定，行主序），返回 a @ b。"""
    o = [0.0] * 9
    for r in range(3):
        for c in range(3):
            o[r * 3 + c] = sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
    return o


def transform_tris(tris: list[Tri], scale: float, rot_z_deg: float,
                   cx: float, cy: float, tx: float, ty: float, tz: float) -> list[Tri]:
    """绕对象自身 XY 中心转 rot_z，再平移 (tx,ty,tz)。rot 单位为度。

    保留原签名与旧语义（z' = z·scale + tz，不校正 Z）。新代码请用 transform_tris3d。
    """
    R = rot_z_matrix(rot_z_deg)
    out: list[Tri] = []
    for tri in tris:
        pts = []
        for (x, y, z) in tri:
            u = (x - cx) * scale
            v = (y - cy) * scale
            w = z * scale
            pts.append((R[0] * u + R[1] * v + tx,
                        R[3] * u + R[4] * v + ty,
                        w + tz))
        out.append((pts[0], pts[1], pts[2]))
    return out


def transform_tris3d(tris: list[Tri], scale: float, R: list[float],
                     center: tuple[float, float, float],
                     tx: float, ty: float, tz: float) -> list[Tri]:
    """完整三维摆放：绕包围盒中心做 3×3 旋转 R + 均匀缩放，再把最低点落到 tz。

    R 为 9 元组（列向量约定，行主序），作用在缩放后的「去中心」坐标上。
    保证：XY 中心落在 (tx, ty)，网格最小 z 落在 tz（即贴床）。
    R=rot_z(rot) 且模型 z0=0 时，结果与旧 transform_tris 完全一致。
    """
    cx, cy, cz = center
    s = scale
    minqz: float | None = None
    for tri in tris:
        for (x, y, z) in tri:
            dx, dy, dz = x - cx, y - cy, z - cz
            qz = R[6] * dx + R[7] * dy + R[8] * dz
            if minqz is None or qz < minqz:
                minqz = qz
    mz = s * minqz
    rcx = R[0] * cx + R[1] * cy + R[2] * cz
    rcy = R[3] * cx + R[4] * cy + R[5] * cz
    rcz = R[6] * cx + R[7] * cy + R[8] * cz
    ox = tx - s * rcx
    oy = ty - s * rcy
    oz = tz - mz - s * rcz
    out: list[Tri] = []
    for tri in tris:
        pts = []
        for (x, y, z) in tri:
            sx, sy, sz = s * x, s * y, s * z
            pts.append((R[0] * sx + R[1] * sy + R[2] * sz + ox,
                        R[3] * sx + R[4] * sy + R[5] * sz + oy,
                        R[6] * sx + R[7] * sy + R[8] * sz + oz))
        out.append((pts[0], pts[1], pts[2]))
    return out


def index_mesh(tris: list[Tri]) -> tuple[list[Vec3], list[tuple[int, int, int]]]:
    """按坐标焊接顶点，返回 (verts, faces)。"""
    index: dict[tuple[float, float, float], int] = {}
    verts: list[Vec3] = []
    faces: list[tuple[int, int, int]] = []
    for tri in tris:
        idx = []
        for v in tri:
            key = (round(v[0], 4), round(v[1], 4), round(v[2], 4))
            j = index.get(key)
            if j is None:
                j = len(verts)
                index[key] = j
                verts.append(key)
            idx.append(j)
        if len(set(idx)) == 3:
            faces.append((idx[0], idx[1], idx[2]))
    return verts, faces


def mesh_binary(mesh: Mesh) -> bytes:
    """二进制网格：uint32 顶点数、uint32 面数、float32×3×顶点、uint32×3×面（小端）。

    给前端 3D 摆盘渲染 + 面拾取用；顶点已焊接、索引化，避免重复上传。
    """
    verts, faces = mesh.welded()
    out = bytearray()
    out += struct.pack("<II", len(verts), len(faces))
    for v in verts:
        out += struct.pack("<3f", v[0], v[1], v[2])
    for f in faces:
        out += struct.pack("<3I", f[0], f[1], f[2])
    return bytes(out)
