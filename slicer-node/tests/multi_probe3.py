"""验证「Orca 工程版 3MF 布局」：3dmodel 里只有 <components>，网格放在 3D/Objects/。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

from app import models, plate
from app.config import load_settings

MODEL = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"
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


def to_project(src: Path, dest: Path) -> None:
    """把 plate.build 的朴素 3MF 转成 Orca 工程布局。"""
    with zipfile.ZipFile(src) as zf:
        old = zf.read("3D/3dmodel.model").decode()
    # 抠出每个 <object ...>...</object> 里的 <mesh>...</mesh>
    chunks = []
    pos = 0
    while True:
        i = old.find('<object id="', pos)
        if i < 0:
            break
        j = old.find("</object>", i) + len("</object>")
        blk = old[i:j]
        name = blk[blk.find('name="') + 6: blk.find('">', blk.find('name="'))]
        mesh = blk[blk.find("<mesh>"): blk.find("</mesh>") + 7]
        chunks.append((name, mesh))
        pos = j

    main = [HEAD, "<resources>"]
    for i, (_name, _mesh) in enumerate(chunks, start=1):
        main.append('<object id="%d" type="model"><components>'
                    '<component objectid="1" p:path="/3D/Objects/object_%d.model"/>'
                    "</components></object>" % (i, i))
    main.append("</resources><build>")
    for i in range(1, len(chunks) + 1):
        main.append('<item objectid="%d" printable="1"/>' % i)
    main.append("</build></model>")

    cfg = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for i, (name, _mesh) in enumerate(chunks, start=1):
        cfg.append('<object id="%d">' % i)
        cfg.append('  <metadata key="name" value="%s"/>' % name)
        cfg.append('  <metadata key="extruder" value="%d"/>' % EXTRUDERS[i - 1])
        cfg.append('  <part id="1" subtype="normal_part">')
        cfg.append('    <metadata key="name" value="%s"/>' % name)
        cfg.append("  </part>")
        cfg.append("</object>")
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
        for i, (_name, mesh) in enumerate(chunks, start=1):
            zf.writestr("3D/Objects/object_%d.model" % i, OBJ_MODEL % mesh)
        zf.writestr("Metadata/model_settings.config", "".join(cfg))


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

EXTRUDERS = [1, 2]

OBJ = [
    {"file": MODEL, "x": 100, "y": 175, "extruder": 1},
    {"file": MODEL, "x": 250, "y": 175, "rot": 90, "extruder": 2},
]


def main() -> None:
    s = load_settings()
    root = Path(tempfile.mkdtemp(prefix="proj-", dir="/data/tmp"))
    work = root / "w"
    work.mkdir()
    plate.build(s, models, OBJ, work / "flat.3mf")
    to_project(work / "flat.3mf", work / "p.3mf")

    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(s.home),
           "LC_ALL": "C", "LANG": "C", "APPDIR": "/opt/orca",
           "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin"}
    argv = ["/opt/orca/bin/orca-slicer", str(work / "p.3mf"),
            "--slice", "0", "--load-settings", "%s;%s" % (PROCESS, MACHINE),
            "--load-filaments", ";".join(FILS),
            "--outputdir", str(work), "--logfile", str(work / "orca.log")]
    p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=600)
    res = {}
    if (work / "result.json").is_file():
        res = json.loads((work / "result.json").read_text())
    print("exit", p.returncode, "rc", res.get("return_code"))
    print(json.dumps(res.get("sliced_plates"), ensure_ascii=False)[:400])
    g = work / "plate_1.gcode"
    if g.is_file():
        txt = g.read_text(errors="replace")
        print("gcode bytes", g.stat().st_size, "toolchanges", txt.count("M600") + txt.count("T1"))
    print(work)


if __name__ == "__main__":
    main()
