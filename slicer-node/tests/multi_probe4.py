"""多材料 3MF：在工程布局下逐项排查 segfault。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

from app import models, plate
from app.config import load_settings

MODEL = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"
SRC = Path("/models") / MODEL
MACHINE = "/app/profiles/machine/voron24-350.json"
PROCESS = "/app/profiles/process/020-standard.json"
FILS = ["/app/profiles/filament/generic-pla.json",
        "/app/profiles/filament/jayo-pla.json"]

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<model unit="millimeter" xml:lang="zh-CN" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/02">')
OBJ_MODEL = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<resources><object id="1" type="model"><mesh>%s</mesh></object>'
             "</resources><build/></model>")
CT = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/xml"/>
</Types>'''
RELS = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>'''

MINI_PROJECT = {
    "filament_settings_id": ["a", "b"],
    "filament_colour": ["#FFFFFF", "#7A7A7A"],
    "filament_type": ["PLA", "PLA"],
    "filament_ids": ["GFL99", "GFL99"],
    "single_extruder_multi_material": "1",
    "print_settings_id": "020-standard",
}


def parse_flat(doc: str) -> list[tuple[str, str]]:
    out = []
    pos = 0
    while True:
        i = doc.find('<object id="', pos)
        if i < 0:
            return out
        j = doc.find("</object>", i) + len("</object>")
        blk = doc[i:j]
        name = blk[blk.find('name="') + 6: blk.find('">', blk.find('name="'))]
        mesh = blk[blk.find("<mesh>"): blk.find("</mesh>") + 7]
        out.append((name, mesh))
        pos = j


def write_project(dest: Path, chunks, extruders, project_cfg: dict | None) -> None:
    main = [HEAD, "<resources>"]
    for i in range(1, len(chunks) + 1):
        main.append('<object id="%d" type="model"><components>'
                    '<component objectid="1" p:path="/3D/Objects/object_%d.model"/>'
                    "</components></object>" % (i, i))
    main.append("</resources><build>")
    for i in range(1, len(chunks) + 1):
        main.append('<item objectid="%d" printable="1"/>' % i)
    main.append("</build></model>")
    cfg = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for i, (name, _m) in enumerate(chunks, start=1):
        cfg.append('<object id="%d">' % i)
        cfg.append('  <metadata key="name" value="%s"/>' % name)
        cfg.append('  <metadata key="extruder" value="%d"/>' % extruders[i - 1])
        cfg.append('  <part id="1" subtype="normal_part">')
        cfg.append('    <metadata key="name" value="%s"/>' % name)
        cfg.append("  </part></object>")
    cfg.append("</config>")
    rels = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(1, len(chunks) + 1):
        rels.append('<Relationship Target="/3D/Objects/object_%d.model" Id="rel%d" '
                    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
                    % (i, i + 1))
    rels.append("</Relationships>")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CT)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("3D/3dmodel.model", "".join(main))
        zf.writestr("3D/_rels/3dmodel.model.rels", "".join(rels))
        for i, (_n, mesh) in enumerate(chunks, start=1):
            zf.writestr("3D/Objects/object_%d.model" % i, OBJ_MODEL % mesh)
        zf.writestr("Metadata/model_settings.config", "".join(cfg))
        if project_cfg is not None:
            zf.writestr("Metadata/project_settings.config",
                        json.dumps(project_cfg, ensure_ascii=False, indent=4))


def run(work: Path, name: str, filaments: list[str]) -> None:
    s = load_settings()
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(s.home),
           "LC_ALL": "C", "LANG": "C", "APPDIR": "/opt/orca",
           "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin"}
    argv = ["/opt/orca/bin/orca-slicer", str(work / "p.3mf"),
            "--slice", "0", "--load-settings", "%s;%s" % (PROCESS, MACHINE),
            "--load-filaments", ";".join(filaments),
            "--outputdir", str(work), "--logfile", str(work / "orca.log")]
    p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=600)
    res = {}
    if (work / "result.json").is_file():
        res = json.loads((work / "result.json").read_text())
    print("%-22s exit=%-4s rc=%-5s gcode=%s err=%r" % (
        name, p.returncode, res.get("return_code"),
        (work / "plate_1.gcode").is_file(), (res.get("error_string") or "")[:60]))


def main() -> None:
    s = load_settings()
    root = Path(tempfile.mkdtemp(prefix="probe4-", dir="/data/tmp"))
    for name, extruders, pcfg in [
        ("t1_nocfg", [1, 1], None),
        ("t1_cfg1", [1, 1], None),
        ("t2_cfg2", [1, 2], None),
        ("t2_cfg2_minproj", [1, 2], MINI_PROJECT),
        ("t1_minproj", [1, 1], MINI_PROJECT),
    ]:
        work = root / name
        work.mkdir()
        flat = work / "flat.3mf"
        objs = [{"file": MODEL, "x": 100, "y": 175, "extruder": extruders[0]},
                {"file": MODEL, "x": 250, "y": 175, "rot": 90, "extruder": extruders[1]}]
        plate.build(s, models, objs, flat)
        with zipfile.ZipFile(flat) as zf:
            chunks = parse_flat(zf.read("3D/3dmodel.model").decode())
        write_project(work / "p.3mf", chunks, extruders, pcfg)
        run(work, name, FILS)
    print("root:", root)


if __name__ == "__main__":
    main()
