"""模型库：磁盘上的 STL/OBJ/3MF + 给摆盘用的轻量几何信息。

摆盘只需要轮廓和包围盒，但切片要三角形。为了不在页面里传几十万个顶点，
这里把「几何摘要」缓存到 $DATA/models/.index.json：
    {file: {size, mtime, tris, bbox, hull, zmin, zmax}}
第一次打开可能要解析一个大 3MF（几秒），之后就秒开。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import mesh as meshmod

SUFFIX = {".stl", ".obj", ".3mf"}
# 几何摘要的「算法版本」：mesh.py 的解析/换算逻辑一改就 +1，
# 否则磁盘上按 mtime 命中的旧缓存会把错误的轮廓/包围盒喂给摆盘。
INDEX_VERSION = 4
_lock = threading.Lock()
# 完整网格缓存：摆盘 3D 预览 / 面拾取 / 任意朝向的占地凸包都要读网格。
# 键 = str(path)，值 = (size, mtime, Mesh)。只有大小和 mtime 都对得上才复用。
_mesh_cache: dict[str, tuple[int, int, meshmod.Mesh]] = {}


class ModelError(Exception):
    pass


def models_dir(settings) -> Path:
    d = Path(settings.data_dir) / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def extra_dirs(settings) -> list[Path]:
    raw = os.environ.get("SLICER_MODEL_DIRS", "")
    return [Path(p) for p in raw.split(":") if p.strip()]


def _all_dirs(settings) -> list[Path]:
    dirs = [models_dir(settings)]
    for p in extra_dirs(settings):
        if p.is_dir():
            dirs.append(p)
    return dirs


def safe_name(name: str) -> str:
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not name or name.startswith("."):
        raise ModelError("非法文件名")
    if Path(name).suffix.lower() not in SUFFIX:
        raise ModelError("只收 %s" % ", ".join(sorted(SUFFIX)))
    return re.sub(r"[^\w\u4e00-\u9fff.@+\-()\[\] ]+", "_", name, flags=re.UNICODE)


def path_of(settings, name: str) -> Path:
    """在用户目录和只读模型目录里找，返回第一个存在的。"""
    for d in _all_dirs(settings):
        p = d / safe_name(name)
        if p.is_file():
            return p
    raise ModelError("找不到模型 %s" % name)


# ---------------------------------------------------------------- 索引

def _index_path(settings) -> Path:
    return models_dir(settings) / ".index.json"


def _load_index(settings) -> dict:
    p = _index_path(settings)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_index(settings, idx: dict) -> None:
    p = _index_path(settings)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


def summarize(path: Path) -> dict[str, Any]:
    st = path.stat()
    mesh, parts = meshmod.load_with_parts(path)
    bb = mesh.bbox()
    data = {
        "v": INDEX_VERSION,
        "file": path.name,
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "tris": mesh.count,
        "bbox": [round(v, 3) for v in bb],
        "hull": mesh.footprint(),
        "w": round(bb[3] - bb[0], 2),
        "d": round(bb[4] - bb[1], 2),
        "h": round(bb[5] - bb[2], 2),
    }
    # 子对象：只有多对象（3MF 里有 ≥2 个 build item）时才列出，单对象省略省 JSON。
    part_list = _summarize_parts(parts)
    if len(part_list) > 1:
        data["parts"] = part_list
    return data


def _summarize_parts(parts: list[meshmod.Mesh]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, pm in enumerate(parts):
        pbb = pm.bbox()
        out.append({
            "id": pm.part_id or str(i + 1),
            "name": pm.name or pm.part_id or ("部件 %d" % (i + 1)),
            "tris": pm.count,
            "bbox": [round(v, 3) for v in pbb],
            "hull": pm.footprint(),
            "w": round(pbb[3] - pbb[0], 2),
            "d": round(pbb[4] - pbb[1], 2),
            "h": round(pbb[5] - pbb[2], 2),
        })
    return out


def _fresh(hit: dict | None, st) -> bool:
    """缓存条目是否还能用：算法版本 + 大小 + mtime 三项都对得上。"""
    return bool(hit) and hit.get("v") == INDEX_VERSION and \
        hit.get("size") == st.st_size and hit.get("mtime") == int(st.st_mtime)


def info(settings, name: str, refresh: bool = False) -> dict[str, Any]:
    path = path_of(settings, name)
    key = str(path)
    idx = _load_index(settings)
    hit = idx.get(key)
    st = path.stat()
    if not refresh and _fresh(hit, st):
        return hit
    data = summarize(path)
    data["dir"] = str(path.parent)
    with _lock:
        idx[key] = data
        _save_index(settings, idx)
    return data


def list_models(settings) -> list[dict[str, Any]]:
    idx = _load_index(settings)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for d in _all_dirs(settings):
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.suffix.lower() not in SUFFIX or p.name.startswith("."):
                continue
            key = str(p)
            hit = idx.get(key)
            st = p.stat()
            item: dict[str, Any] = {"file": p.name, "size": st.st_size,
                                    "mtime": int(st.st_mtime), "dir": str(p.parent),
                                    "writable": d == models_dir(settings)}
            if _fresh(hit, st):
                item.update({k: hit[k] for k in ("tris", "w", "d", "h", "hull", "bbox")})
            if p.name in seen:
                continue
            seen.add(p.name)
            out.append(item)
    return out


def save_upload(settings, filename: str, data: bytes) -> dict[str, Any]:
    name = safe_name(filename)
    dest = models_dir(settings) / name
    stem, suf = dest.stem, dest.suffix
    n = 2
    while dest.exists():
        dest = models_dir(settings) / ("%s-%d%s" % (stem, n, suf))
        n += 1
    dest.write_bytes(data)
    try:
        return info(settings, dest.name, refresh=True)
    except meshmod.MeshError as e:
        dest.unlink(missing_ok=True)
        raise ModelError("网格解析失败：%s" % e) from e


def delete(settings, name: str) -> None:
    p = path_of(settings, name)
    if p.parent != models_dir(settings):
        raise ModelError("只读模型目录里的文件删不掉")
    p.unlink()
    idx = _load_index(settings)
    idx.pop(str(p), None)
    _save_index(settings, idx)


def copy_into_job(settings, name: str, dest_dir: Path) -> Path:
    src = path_of(settings, name)
    dest = dest_dir / ("src" + src.suffix.lower())
    shutil.copyfile(src, dest)
    return dest


def load_mesh(settings, name: str, part_id: str | None = None) -> meshmod.Mesh:
    """读取完整网格（带内存缓存）。摆盘 3D / 面拾取 / 任意朝向占地都用它。

    part_id 非空时返回 3MF 里对应子对象的网格（局部坐标），否则整文件合并网格。
    """
    path = path_of(settings, name)
    st = path.stat()
    key = str(path) if not part_id else "%s#%s" % (path, part_id)
    hit = _mesh_cache.get(key)
    if hit and hit[0] == st.st_size and hit[1] == int(st.st_mtime):
        return hit[2]
    if part_id:
        m: meshmod.Mesh | None = None
        for pm in meshmod.load_parts(path):
            if str(pm.part_id) == str(part_id):
                m = pm
                break
        if m is None:
            raise ModelError("找不到部件 %s" % part_id)
    else:
        m = meshmod.load(path)
    if len(_mesh_cache) > 64:
        _mesh_cache.clear()
    _mesh_cache[key] = (st.st_size, int(st.st_mtime), m)
    return m


def mesh_bytes(settings, name: str, part_id: str | None = None) -> bytes:
    return meshmod.mesh_binary(load_mesh(settings, name, part_id))


def cached_scan(settings) -> float:
    return time.time()
