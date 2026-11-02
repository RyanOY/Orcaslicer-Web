"""多耗材/多对象 3MF 的实验脚本（容器里跑）。

用法（在 /app 目录、容器内）：
    python3 /tmp/plate_multi.py

目标：找出「两个对象各用一个耗材」能被 Orca CLI 正常切片的 3MF 写法。
Orca 把带 Metadata/model_settings.config 的 3MF 当「工程文件」，
和单纯模型文件的解析路径不同，格式不对会直接 segfault（exit -11）。
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

from app import mesh as meshmod
from app import models, plate
from app.config import load_settings

MODEL = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"
MACHINE = "/app/profiles/machine/voron24-350.json"
PROCESS = "/app/profiles/process/020-standard.json"
FILAMENTS = ["/app/profiles/filament/generic-pla.json",
             "/app/profiles/filament/jayo-pla.json"]

MODEL_CFG_NS = ('<config xmlns="http://schemas.microsoft.com/3dmanufacturing/'
                'core/2015/02">')


def write_cfg_style(path: Path, objects: list[dict], style: str) -> None:
    """在 plate.build 产物上重写 Metadata/model_settings.config。

    style:
      simple  —— 只有 <object id><metadata key=extruder>（当前实现）
      part    —— object 里再包一层 <part id="1" subtype="normal_part">
      plate   —— part + <plate><model_instance>（Orca 自己写工程文件的样子）
    """
    with zipfile.ZipFile(path) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}

    model = files["3D/3dmodel.model"].decode()
    # 对象 id 就是 build 项的顺序 1..n，和我们写的一致
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    if style in ("plate",):
        lines.append('  <plate>')
        lines.append('    <metadata key="plater_id" value="1"/>')
        lines.append('    <metadata key="plater_name" value=""/>')
        for i in range(1, len(objects) + 1):
            lines.append("    <model_instance>")
            lines.append('      <metadata key="object_id" value="%d"/>' % i)
            lines.append('      <metadata key="instance_id" value="0"/>')
            lines.append('      <metadata key="identify_id" value="%d"/>' % (i + 100))
            lines.append("    </model_instance>")
        lines.append("  </plate>")
    for i, obj in enumerate(objects, start=1):
        lines.append('  <object id="%d">' % i)
        lines.append('    <metadata key="name" value="%s"/>' % obj["name"])
        lines.append('    <metadata key="extruder" value="%d"/>' % obj["extruder"])
        if style in ("part", "plate"):
            lines.append('    <part id="1" subtype="normal_part">')
            lines.append('      <metadata key="name" value="%s"/>' % obj["name"])
            lines.append('      <mesh_stat face_count="%d"/>' % len(obj["tris"]))
            lines.append("    </part>")
        lines.append("  </object>")
    lines.append("</config>")
    files["Metadata/model_settings.config"] = "".join(lines).encode()
    if style == "plate":
        files.setdefault("Metadata/project_settings.config", b"{}")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, b in files.items():
            zf.writestr(n, b)


CASES: list[tuple[str, list[dict], list[str], str | None]] = [
    # name, objects, filaments, cfg style (None = 不要 model_settings)
    ("2fil_t1only", [
        {"file": MODEL, "x": 100, "y": 175, "extruder": 1},
        {"file": MODEL, "x": 250, "y": 175, "rot": 90, "extruder": 1},
    ], FILAMENTS, None),
    ("2fil_t2_simple", [
        {"file": MODEL, "x": 100, "y": 175, "extruder": 1},
        {"file": MODEL, "x": 250, "y": 175, "rot": 90, "extruder": 2},
    ], FILAMENTS, "simple"),
    ("2fil_t2_part", [
        {"file": MODEL, "x": 100, "y": 175, "extruder": 1},
        {"file": MODEL, "x": 250, "y": 175, "rot": 90, "extruder": 2},
    ], FILAMENTS, "part"),
    ("2fil_t2_plate", [
        {"file": MODEL, "x": 100, "y": 175, "extruder": 1},
        {"file": MODEL, "x": 250, "y": 175, "rot": 90, "extruder": 2},
    ], FILAMENTS, "plate"),
]


def main() -> None:
    s = load_settings()
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(s.home), "LC_ALL": "C", "LANG": "C",
        "APPDIR": "/opt/orca",
        "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin",
    }
    root = Path(tempfile.mkdtemp(prefix="multi-", dir=str(Path(s.data_dir) / "tmp")))
    for name, objects, fils, style in CASES:
        work = root / name
        work.mkdir(parents=True)
        built = plate.build(s, models, objects, work / "plate.3mf")
        if style:
            # plate.build 已经写过 simple 版；这里换成对应风格
            objs = [{"name": Path(o["file"]).stem, "extruder": o["extruder"],
                     "tris": [None] * 0} for o in objects]
            # 需要真实三角形数：从 3dmodel 里数
            with zipfile.ZipFile(work / "plate.3mf") as zf:
                xml = zf.read("3D/3dmodel.model").decode()
            counts = [blk.count("<triangle ") for blk in xml.split("<object ")[1:]]
            for o, c in zip(objs, counts):
                o["tris"] = [None] * c
            write_cfg_style(work / "plate.3mf", objs, style)
        argv = ["/opt/orca/bin/orca-slicer", str(work / "plate.3mf"),
                "--slice", "0", "--load-settings", "%s;%s" % (PROCESS, MACHINE),
                "--load-filaments", ";".join(fils),
                "--outputdir", str(work), "--logfile", str(work / "orca.log")]
        p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=600)
        res = {}
        if (work / "result.json").is_file():
            res = json.loads((work / "result.json").read_text())
        plates = res.get("sliced_plates") or [{}]
        g = work / "plate_1.gcode"
        tool_changes = 0
        if g.is_file():
            tool_changes = sum(1 for ln in g.open("rb")
                               if ln.startswith(b";LAYER_CHANGE") or False)
        print("%-16s exit=%-4s rc=%-5s gcode=%-5s tris=%s warn=%r" % (
            name, p.returncode, res.get("return_code"), g.is_file(),
            plates[0].get("triangle_count"),
            (plates[0].get("warning_message") or "")[:70]))


if __name__ == "__main__":
    main()
