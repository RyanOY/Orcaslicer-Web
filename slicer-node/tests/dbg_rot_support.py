import math, json, subprocess
from pathlib import Path
from app.config import load_settings
from app import plate as P
from app import models as MO

s = load_settings()
PROF = Path("/data/jobs/3a1a055f70e9")
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LC_ALL": "C", "LANG": "C",
       "APPDIR": "/opt/orca", "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin",
       "TMPDIR": "/tmp"}
base = json.loads((PROF / "process.json").read_text())
sup = dict(base); sup["enable_support"] = "1"
Path("/tmp/proc_sup.json").write_text(json.dumps(sup))


def rx(deg):
    a = math.radians(deg); c, sn = math.cos(a), math.sin(a)
    return [1, 0, 0, 0, c, -sn, 0, sn, c]


def run(label, idx, extra, proc):
    o = {"file": "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf", "part": "", "name": "x",
         "x": 100, "y": 100, "z": 0, "rot": 0, "scale": 1, "extruder": 1}
    o.update(extra)
    work = Path("/tmp/ts%d" % idx); work.mkdir(parents=True, exist_ok=True)
    tmf = work / "plate.3mf"
    P.build(s, MO, [o], tmf)
    out = work / "out"; out.mkdir(exist_ok=True)
    argv = [s.orca_bin, str(tmf), "--slice", "0", "--arrange", "0",
            "--load-settings", "%s;%s" % (proc, PROF / "machine.json"),
            "--load-filaments", str(PROF / "filament_1.json"),
            "--outputdir", str(out), "--logfile", str(work / "orca.log")]
    p = subprocess.run(argv, env=ENV, cwd=str(work), capture_output=True, timeout=900)
    print("%-28s rc=%-4s gcode=%s" % (label, p.returncode, (out / "plate_1.gcode").is_file()), flush=True)


run("rx180 support=OFF", 61, {"m": rx(180)}, PROF / "process.json")
run("rx180 support=ON", 62, {"m": rx(180)}, "/tmp/proc_sup.json")
run("identity support=OFF", 63, {}, PROF / "process.json")
run("rx45 support=OFF", 64, {"m": rx(45)}, PROF / "process.json")
