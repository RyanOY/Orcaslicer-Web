"""把 SimplyPrint 的 export-full 配置导入成本节点的 profile（扁平 JSON）。

SimplyPrint 导出的两种壳子：
  filament/process: {profile:{...}, settings:{全部设置}}
  machine:          {profile:{type, name, variants:[{nozzle_size, settings:{...}}]}}
                    —— 机器设置藏在 variants 里，每个喷嘴一个变体

这个工具把壳子拆掉，挑出要用的那一份，按 Orca 原生 preset 的样子落成
profiles/<kind>/<name>.json；空值（"" / [] / [""]）一律删掉，
否则 CLI 会在 ConfigOptionVector::set_at 上 abort。

用法：
  python tools/import_simplyprint.py <导出文件.json> --name voron24-350-sp
  python tools/import_simplyprint.py <导出文件.json> --kind filament --name jayo-pla-sp
  （--nozzle 0.15/0.2/0.4/0.6 …）机器默认取 0.4
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KINDS = ("machine", "process", "filament")
DROP_IF_EMPTY = True


def _empty(v) -> bool:
    return v == "" or v == [] or v == [""] or v is None


def detect_kind(doc: dict, filename: str) -> str:
    kind = (doc.get("profile") or {}).get("type")
    if kind in KINDS:
        return kind
    low = filename.lower()
    for k in KINDS:
        if k in low:
            return k
    raise SystemExit("看不出类型：profile.type 缺失，文件名里也没有 machine/filament/process")


def pick_settings(doc: dict, kind: str, nozzle: float) -> tuple[dict, str]:
    prof = doc.get("profile") or {}
    if kind == "machine":
        variants = prof.get("variants") or []
        if not variants:
            raise SystemExit("机器导出里没有 variants，没法选喷嘴")
        for v in variants:
            if abs(float(v.get("nozzle_size") or 0) - nozzle) < 1e-6:
                return dict(v.get("settings") or {}), v.get("name") or ""
        have = ", ".join(str(v.get("nozzle_size")) for v in variants)
        raise SystemExit("没有 %s 喷嘴的变体，可用的：%s" % (nozzle, have))
    if isinstance(doc.get("settings"), dict):
        return dict(doc["settings"]), prof.get("name") or ""
    return {k: v for k, v in doc.items() if k != "profile"}, prof.get("name") or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--kind", choices=KINDS)
    ap.add_argument("--name", help="落盘文件名（不含 .json）")
    ap.add_argument("--nozzle", type=float, default=0.4)
    ap.add_argument("--out", default="profiles")
    a = ap.parse_args(argv)

    src = Path(a.src)
    doc = json.loads(src.read_text(encoding="utf-8"))
    kind = a.kind or detect_kind(doc, src.name)
    settings, variant_name = pick_settings(doc, kind, a.nozzle)

    cleaned = {k: v for k, v in settings.items() if not (DROP_IF_EMPTY and _empty(v))}
    # 名字要写进去：CLI 把 preset 的 name 落到 gcode 注释里（printer_settings_id /
    # filament_settings_id）。导出的 settings 里那个 name 是**底稿**的名字
    # （jayo PLA 是抄自 "Generic PLA @System"，于是 settings.name 就是它），
    # 得用 profile.name 覆盖，否则 gcode 上看着像没生效。
    prof_name = (doc.get("profile") or {}).get("name") or ""
    cleaned["name"] = variant_name or prof_name or cleaned.get("name") or ""
    # from 保持导出里的值（"system"）。改成 "User" 会让 CLI 把它当用户 preset，
    # 打开 3mf 时按 name 做打印机兼容性校验，直接 rc=-16 拒绝切片。
    if not cleaned:
        raise SystemExit("选出来的设置是空的，检查导出文件结构")

    name = a.name
    if not name:
        base = (doc.get("profile") or {}).get("name") or src.stem
        name = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    out = Path(a.out) / kind / (name + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cleaned, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    dropped = len(settings) - len(cleaned)
    print(f"{kind}/{out.name}: {len(cleaned)} 项设置（丢掉 {dropped} 个空值）← "
          f"{src.name} / {variant_name or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
