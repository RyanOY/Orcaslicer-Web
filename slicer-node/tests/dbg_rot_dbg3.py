"""定位「立起来切片失败」：给 orca 加 --debug 3，把失败/成功两种情况的日志尾段并排看。

跑法：docker exec -w /app orcaweb3d python3 /tmp/dbg_rot_dbg3.py
"""
import math, subprocess
from pathlib import Path
from app.config import load_settings
from app import plate as P
from app import models as MO

s = load_settings()
PROF = Path("/data/jobs/3a1a055f70e9")
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LC_ALL": "C", "LANG": "C",
       "APPDIR": "/opt/orca", "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin",
       "TMPDIR": "/tmp"}


def rx(deg):
    a = math.radians(deg); c, sn = math.cos(a), math.sin(a)
    return [1, 0, 0, 0, c, -sn, 0, sn, c]


def run(label, idx, file, extra):
    o = {"file": file, "part": "", "name": "x", "x": 100, "y": 100, "z": 0,
         "rot": 0, "scale": 1, "extruder": 1}
    o.update(extra)
    work = Path("/tmp/d3_%d" % idx); work.mkdir(parents=True, exist_ok=True)
    tmf = work / "plate.3mf"
    P.build(s, MO, [o], tmf)
    out = work / "out"; out.mkdir(exist_ok=True)
    argv = [s.orca_bin, str(tmf), "--slice", "0", "--arrange", "0", "--debug", "3",
            "--load-settings", "%s;%s" % (PROF / "process.json", PROF / "machine.json"),
            "--load-filaments", str(PROF / "filament_1.json"),
            "--outputdir", str(out), "--logfile", str(work / "orca.log")]
    p = subprocess.run(argv, env=ENV, cwd=str(work), capture_output=True, timeout=900)
    lg = work / "orca.log"
    lines = lg.read_text(errors="replace").splitlines() if lg.is_file() else []
    print("=== %s rc=%s gcode=%s lines=%d" % (label, p.returncode,
          (out / "plate_1.gcode").is_file(), len(lines)))
    for ln in lines[-18:]:
        print("   ", ln[:160])


BOAT = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"
run("boat rx90 FAIL", 21, BOAT, {"m": rx(90)})
run("boat rx-90 OK", 22, BOAT, {"m": rx(-90)})
run("boat rx90+rz30 OK", 23, BOAT, {"m": rx(90), "rot": 30})
