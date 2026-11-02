"""找「我们拼的 3MF + 多耗材」能不崩的最小工程骨架。"""
from __future__ import annotations

import json
import re
import shutil
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

OBJ = [
    {"file": MODEL, "x": 100, "y": 175, "extruder": 1},
    {"file": MODEL, "x": 250, "y": 175, "rot": 90, "extruder": 2},
]


def do_slice(work: Path, name: str) -> None:
    s = load_settings()
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
    print("%-18s exit=%-4s rc=%-5s gcode=%s" % (
        name, p.returncode, res.get("return_code"), (work / "plate_1.gcode").is_file()))


def main() -> None:
    s = load_settings()
    root = Path(tempfile.mkdtemp(prefix="skel-", dir="/data/tmp"))
    base = root / "base.3mf"
    plate.build(s, models, OBJ, base)

    with zipfile.ZipFile(base) as zf:
        ours = {n: zf.read(n) for n in zf.namelist()}
    with zipfile.ZipFile(SRC) as zf:
        proj = {n: zf.read(n) for n in zf.namelist()}

    ours_cfg = ours["Metadata/model_settings.config"].decode()
    print("our cfg:\n", ours_cfg[:400])

    def emit(name: str, extra: dict[str, bytes], cfg: str | None = None) -> Path:
        w = root / name
        w.mkdir()
        files = dict(ours)
        if cfg is not None:
            files["Metadata/model_settings.config"] = cfg.encode()
        files.update(extra)
        with zipfile.ZipFile(w / "p.3mf", "w", zipfile.ZIP_DEFLATED) as zf:
            for n, b in files.items():
                zf.writestr(n, b)
        do_slice(w, name)
        return w

    emit("a_ours", {})
    emit("b_projectcfg", {"Metadata/project_settings.config":
                          proj["Metadata/project_settings.config"]})
    emit("c_rels", {"Metadata/_rels/model_settings.config.rels":
                    proj["Metadata/_rels/model_settings.config.rels"]})
    emit("d_sliceinfo", {"Metadata/project_settings.config":
                         proj["Metadata/project_settings.config"],
                         "Metadata/_rels/model_settings.config.rels":
                         proj["Metadata/_rels/model_settings.config.rels"],
                         "Metadata/slice_info.config":
                         proj["Metadata/slice_info.config"]})
    # e: 用 Orca 的 cfg 骨架，只换 extruder
    cfg = proj["Metadata/model_settings.config"].decode()
    cfg = re.sub(r'(<metadata key="extruder" value=")1(")', r"\g<1>2\g<2>", cfg, count=1)
    emit("e_keepid8", {"Metadata/project_settings.config":
                       proj["Metadata/project_settings.config"]}, cfg)


if __name__ == "__main__":
    main()
