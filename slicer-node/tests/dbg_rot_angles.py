import math, json, os, subprocess, tempfile, shutil
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


def ry(deg):
    a = math.radians(deg); c, sn = math.cos(a), math.sin(a)
    return [c, 0, sn, 0, 1, 0, -sn, 0, c]


def try_slice(label, extra, idx):
    o = {"file": "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf", "part": "", "name": "boat",
         "x": 100, "y": 100, "z": 0, "rot": 0, "scale": 1, "extruder": 1}
    o.update(extra)
    work = Path("/tmp/rot%d" % idx)
    work.mkdir(parents=True, exist_ok=True)
    tmf = work / "plate.3mf"
    r = P.build(s, MO, [o], tmf)
    out = work / "out"
    out.mkdir(exist_ok=True)
    argv = [s.orca_bin, str(tmf), "--slice", "0", "--arrange", "0",
            "--load-settings", "%s;%s" % (PROF / "process.json", PROF / "machine.json"),
            "--load-filaments", str(PROF / "filament_1.json"),
            "--outputdir", str(out), "--logfile", str(work / "orca.log")]
    p = subprocess.run(argv, env=ENV, cwd=str(work), capture_output=True, timeout=900)
    tail = ""
    lg = work / "orca.log"
    if lg.is_file():
        lines = lg.read_text(errors="replace").splitlines()
        for ln in lines[-600:]:
            if "max_cantilever" in ln or "error" in ln.lower():
                tail += ln + " | "
    print("%-14s rc=%-4s gcode=%s %s" % (label, p.returncode,
          (out / "plate_1.gcode").is_file(), tail[:200]))


try_slice("identity", {}, 1)
try_slice("rx90", {"m": rx(90)}, 2)
try_slice("rx89.9", {"m": rx(89.9)}, 3)
try_slice("rx45", {"m": rx(45)}, 4)
try_slice("ry90", {"m": ry(90)}, 5)
try_slice("rx90+rz30", {"m": rx(90), "rot": 30}, 6)
