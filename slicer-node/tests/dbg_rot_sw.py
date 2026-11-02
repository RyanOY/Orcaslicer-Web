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
    work = Path("/tmp/sw%d" % idx); work.mkdir(parents=True, exist_ok=True)
    tmf = work / "plate.3mf"
    P.build(s, MO, [o], tmf)
    out = work / "out"; out.mkdir(exist_ok=True)
    argv = [s.orca_bin, str(tmf), "--slice", "0", "--arrange", "0",
            "--load-settings", "%s;%s" % (PROF / "process.json", PROF / "machine.json"),
            "--load-filaments", str(PROF / "filament_1.json"),
            "--outputdir", str(out), "--logfile", str(work / "orca.log")]
    p = subprocess.run(argv, env=ENV, cwd=str(work), capture_output=True, timeout=900)
    print("%-22s rc=%-4s gcode=%s" % (label, p.returncode, (out / "plate_1.gcode").is_file()))


BOAT = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"
DYSON = "Dyson+hair+dryer.3mf"
run("boat rx90 y=100", 11, BOAT, {"m": rx(90)})
run("boat rx90 y=60", 12, BOAT, {"m": rx(90), "y": 60})
run("boat rx90 x=60", 13, BOAT, {"m": rx(90), "x": 60})
run("boat rx-90", 14, BOAT, {"m": rx(-90)})
run("dyson rx90", 15, DYSON, {"m": rx(90)})
run("dyson rx45", 16, DYSON, {"m": rx(45)})
