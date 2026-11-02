"""定位多材料 segfault：用「Orca 自己的工程文件」做对照。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from app.config import load_settings

SRC = Path("/models/SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf")
MACHINE = "/app/profiles/machine/voron24-350.json"
PROCESS = "/app/profiles/process/020-standard.json"
FILS = ["/app/profiles/filament/generic-pla.json",
        "/app/profiles/filament/jayo-pla.json"]


def slice_it(work: Path, name: str) -> None:
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
        shutil.copyfile(work / "result.json", work.parent / (name + ".json"))
    print("%-24s exit=%-4s rc=%-5s gcode=%s" % (
        name, p.returncode, res.get("return_code"),
        (work / "plate_1.gcode").is_file()))


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="mm-", dir="/data/tmp"))

    # 1. 原样工程（1 耗材）
    w = root / "orig"
    w.mkdir()
    shutil.copyfile(SRC, w / "p.3mf")
    slice_it(w, "orig_1fil")

    # 2. 原工程 + 把对象 extruder 改成 2，外挂 project_settings
    w = root / "orig_t2"
    w.mkdir()
    with zipfile.ZipFile(SRC) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    cfg = files["Metadata/model_settings.config"].decode()
    cfg = re.sub(r'(<metadata key="extruder" value=")1(")', r"\g<1>2\g<2>", cfg, count=1)
    files["Metadata/model_settings.config"] = cfg.encode()
    with zipfile.ZipFile(w / "p.3mf", "w", zipfile.ZIP_DEFLATED) as zf:
        for n, b in files.items():
            zf.writestr(n, b)
    slice_it(w, "orig_t2")

    # 3. 原工程 + 2 耗材但 extruder 仍是 1
    w = root / "orig_2fil"
    w.mkdir()
    shutil.copyfile(SRC, w / "p.3mf")
    slice_it(w, "orig_2fil_t1")


if __name__ == "__main__":
    main()
