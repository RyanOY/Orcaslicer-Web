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


def rx(d):
    a = math.radians(d); c, sn = math.cos(a), math.sin(a)
    return [1, 0, 0, 0, c, -sn, 0, sn, c]


def run(label, idx, extra):
    o = {"file": "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf", "part": "", "name": "x",
         "x": 100, "y": 100, "z": 0, "rot": 0, "scale": 1, "extruder": 1}
    o.update(extra)
    work = Path("/tmp/zx%d" % idx); work.mkdir(parents=True, exist_ok=True)
    tmf = work / "plate.3mf"
    P.build(s, MO, [o], tmf)
    out = work / "out"; out.mkdir(exist_ok=True)
    argv = [s.orca_bin, str(tmf), "--slice", "0", "--arrange", "0",
            "--load-settings", "%s;%s" % (PROF / "process.json", PROF / "machine.json"),
            "--load-filaments", str(PROF / "filament_1.json"),
            "--outputdir", str(out), "--logfile", str(work / "orca.log")]
    p = subprocess.run(argv, env=ENV, cwd=str(work), capture_output=True, timeout=900)
    print("%-30s rc=%-4s gcode=%s" % (label, p.returncode, (out / "plate_1.gcode").is_file()), flush=True)


run("rx90 scale1.0001", 71, {"m": rx(90), "scale": 1.0001})
run("rx90 x=200 y=200", 72, {"m": rx(90), "x": 200, "y": 200})
run("rx86", 73, {"m": rx(86)})
run("rx87", 74, {"m": rx(87)})
run("rx90 rot=1", 75, {"m": rx(90), "rot": 1})
run("rx90 rot=0.5", 76, {"m": rx(90), "rot": 0.5})
