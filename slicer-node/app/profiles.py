"""profile 仓库：读、清洗、校验、保存、导入导出。

三种来源都要吃：
1. 我们自己的 flatten 工具导出的单文件（无 inherits）—— 首选
2. SimplyPrint 的 export-full（{profile:{...}, settings:{...}} 两层壳）
3. 直接手写的扁平 JSON

两个坑（实测踩过）：
- SimplyPrint 导出里带 ""/[]/[""] 的值会让 CLI 抛
  `ConfigOptionVector::set_at(): Assigning from an empty vector` 然后 abort
- 带 inherits 链的原始 profile 会被 CLI 拒（return -17）

编辑要落盘，但镜像里的 profiles/ 是只读的（容器里 /app 归 root），
所以用户改过的配置写到 $SLICER_DATA_DIR/profiles/<kind>/<name>.json，
读的时候先看用户目录、再看镜像自带的 —— 用户文件优先级高。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

KINDS = ("machine", "process", "filament")
NAME_RE = re.compile(r"^[A-Za-z0-9._@\- ]{1,64}$")


class ProfileError(Exception):
    pass


def _unwrap(doc: dict) -> tuple[dict, str | None]:
    """simplyprint export-full → settings；其他原样返回。返回 (doc, 原壳类型)。"""
    if isinstance(doc.get("settings"), dict):
        return dict(doc["settings"]), "simplyprint"
    if isinstance(doc.get("profile"), dict) and isinstance(doc["profile"].get("settings"), dict):
        return dict(doc["profile"]["settings"]), "simplyprint"
    return dict(doc), None


def _looks_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == [""]


def _orca_scalar(v: Any) -> Any:
    """把值转成 Orca 配置文件认的形态：标量 → 字符串，数组 → 字符串数组。

    实测（2.4.2）：数组里混进数字会直接拒载 ——
    `load_from_json: ... invalid json array for cool_plate_temp`。
    编辑器保存时如果原样存 {"cool_plate_temp": [48]} 就会炸，所以读写两端都过一遍。
    """
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return ("%d" % v) if v.is_integer() else repr(v)
    if isinstance(v, (list, tuple)):
        return [_orca_scalar(x) for x in v]
    return v


def clean(doc: dict, drop_meta: bool = False) -> dict:
    out = {k: _orca_scalar(v) for k, v in doc.items() if not _looks_empty(v)}
    if out.get("inherits"):
        raise ProfileError(
            "profile 带 inherits=%r，CLI 不解析。先用 tools/flatten_profiles.py 展开"
            % out["inherits"]
        )
    for k, v in out.items():
        if isinstance(v, dict):
            raise ProfileError("参数 %s 是嵌套对象，Orca 的配置只接受扁平值" % k)
    if drop_meta:
        for k in ("from", "type", "instantiation", "setting_id", "filament_id"):
            out.pop(k, None)
    return out


# ---------------------------------------------------------------- 位置

def user_dir(settings) -> Path:
    return Path(settings.data_dir) / "profiles"


def _paths(settings, kind: str, name: str) -> tuple[Path, Path]:
    if kind not in KINDS:
        raise ProfileError("未知类型: %r" % kind)
    if not NAME_RE.match(name or "") or name.startswith("."):
        raise ProfileError("非法名字: %r（只允许字母数字和 . _ - @ 空格）" % name)
    fn = name if name.endswith(".json") else name + ".json"
    return user_dir(settings) / kind / fn, Path(settings.profile_dir) / kind / fn


def resolve(settings, kind: str, name: str) -> tuple[Path, str]:
    """返回 (实际路径, 'user'|'baked')。用户目录优先。"""
    up, bp = _paths(settings, kind, name)
    if up.is_file():
        return up, "user"
    if bp.is_file():
        return bp, "baked"
    available = ", ".join(list_profiles(settings.profile_dir, kind)) or "(空)"
    raise ProfileError("找不到 %s/%s，可用: %s" % (kind, name, available))


# ---------------------------------------------------------------- 读

def list_profiles(profile_dir: Path, kind: str) -> list[str]:
    d = Path(profile_dir) / kind
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def load_profile(profile_dir: Path, kind: str, name: str) -> dict:
    """老接口：只读镜像里的目录（内部测试/工具用）。"""
    if kind not in KINDS:
        raise ProfileError("未知类型: %r" % kind)
    if "/" in name or name.startswith("."):
        raise ProfileError("非法名字: %r" % name)
    path = Path(profile_dir) / kind / (name if name.endswith(".json") else name + ".json")
    if not path.is_file():
        available = ", ".join(list_profiles(Path(profile_dir), kind)) or "(空)"
        raise ProfileError("找不到 %s/%s，可用: %s" % (kind, path.name, available))
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ProfileError("%s 不是 JSON 对象" % path)
    return clean(_unwrap(doc)[0])


def load(settings, kind: str, name: str) -> dict:
    path, _src = resolve(settings, kind, name)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ProfileError("%s 不是 JSON 对象" % path)
    return clean(_unwrap(doc)[0])


def catalog(profile_dir: Path | None = None, settings=None) -> dict[str, list[str]]:
    """向后兼容：只给名字列表。"""
    if settings is not None:
        return {k: [e["name"] for e in entries(settings, k)] for k in KINDS}
    return {k: list_profiles(Path(profile_dir), k) for k in KINDS}


def entries(settings, kind: str) -> list[dict[str, Any]]:
    """列表 + 来源标记（用户改过的 / 镜像自带）。"""
    seen: dict[str, dict] = {}
    for base, source in ((Path(settings.profile_dir), "baked"), (user_dir(settings), "user")):
        d = base / kind
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            item = seen.get(p.stem)
            if item and item["source"] == "user":
                continue
            info = {"name": p.stem, "source": source, "size": p.stat().st_size}
            seen[p.stem] = info
    for name, info in seen.items():
        info["baked"] = (Path(settings.profile_dir) / kind / (name + ".json")).is_file()
    return sorted(seen.values(), key=lambda x: x["name"].lower())


# ---------------------------------------------------------------- 写

def save(settings, kind: str, name: str, doc: dict) -> dict[str, Any]:
    """保存到用户目录。返回 {path, warnings}。"""
    up, bp = _paths(settings, kind, name)
    cleaned = clean(doc)
    if not cleaned:
        raise ProfileError("配置是空的")
    defaults, shell = _unwrap(doc)
    if shell:
        # 从 SimplyPrint 导出直接改完保存，别把壳写回去
        cleaned = clean(defaults)
    warnings: list[str] = []
    if kind == "machine":
        for key in ("printable_area", "bed_shape", "machine_max_speed_x"):
            if key not in cleaned:
                warnings.append("机器配置缺 %s，切片时可能报错" % key)
    if "name" in cleaned and cleaned["name"] != doc.get("name"):
        warnings.append("name 字段与文件名不一致，建议保持一致")
    cleaned.setdefault("name", name)
    cleaned.setdefault("from", "User")
    cleaned.setdefault("type", kind)
    up.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(up.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=1)
        os.replace(tmp, up)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return {"name": name, "kind": kind, "path": str(up), "warnings": warnings,
            "shadowing_baked": bp.is_file()}


def delete(settings, kind: str, name: str) -> dict[str, Any]:
    up, bp = _paths(settings, kind, name)
    if not up.is_file():
        if bp.is_file():
            raise ProfileError("%s 是镜像自带的，不能删；可以改（会另存为用户配置）" % name)
        raise ProfileError("没有这个用户配置: %s" % name)
    up.unlink()
    return {"deleted": name, "reverted_to_baked": bp.is_file()}


def import_doc(kind: str, doc: dict, name: str | None = None) -> tuple[str, dict]:
    """吃用户上传的 JSON（可能是 SimplyPrint 壳、可能是 Orca 的 preset）。"""
    if not isinstance(doc, dict):
        raise ProfileError("JSON 顶层必须是对象")
    body, shell = _unwrap(doc)
    body = {k: v for k, v in body.items() if not isinstance(v, dict)}
    if body.get("settings") or body.get("profile"):
        raise ProfileError("这个 JSON 结构不认识（既不是扁平配置也不是 SimplyPrint 导出）")
    guess = name or body.get("name") or "imported"
    guess = re.sub(r"[^A-Za-z0-9._@\- ]+", "-", str(guess)).strip()[:64] or "imported"
    return guess, clean(body)


def export(settings, kind: str, name: str) -> tuple[str, dict]:
    path, src = resolve(settings, kind, name)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return "%s-%s.json" % (kind, name), doc
