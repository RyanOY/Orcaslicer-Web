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
a = math.radians(90); c, sn = math.cos(a), math.sin(a)
Rx = [1, 0, 0, 0, c, -sn, 0, sn, c]
o = {"file": "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf", "part": "", "name": "boat",
     "x": 100, "y": 100, "z": 0, "rot": 0, "scale": 1, "extruder": 1, "m": Rx}
work = Path("/tmp/rot_dbg90"); work.mkdir(parents=True, exist_ok=True)
tmf = work / "plate.3mf"
r = P.build(s, MO, [o], tmf)
print("bbox", r["objects"][0]["bbox"])
out = work / "out2"; out.mkdir(exist_ok=True)
argv = [s.orca_bin, str(tmf), "--slice", "0", "--arrange", "0",
        "--load-settings", "%s;%s" % (PROF / "process.json", PROF / "machine.json"),
        "--load-filaments", str(PROF / "filament_1.json"),
        "--outputdir", str(out), "--logfile", str(work / "orca2.log")]
p = subprocess.run(argv, env=ENV, cwd=str(work), capture_output=True, timeout=900)
print("rc", p.returncode)
print("STDOUT>>>", p.stdout.decode(errors="replace")[-2000:])
print("STDERR>>>", p.stderr.decode(errors="replace")[-3000:])
