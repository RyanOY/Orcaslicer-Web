import math, json
from pathlib import Path
from app.config import load_settings
from app import plate as P, mesh as M
from app import models as MO

s = load_settings()
a = math.radians(90); c, sn = math.cos(a), math.sin(a)
Rx = [1, 0, 0, 0, c, -sn, 0, sn, c]
base = {"file": "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf", "part": "", "name": "boat",
        "x": 100, "y": 100, "z": 0, "rot": 0, "scale": 1, "extruder": 1}
for label, extra in (("identity", {}), ("rx45", {"m": [1,0,0, 0,math.cos(math.radians(45)),-math.sin(math.radians(45)), 0,math.sin(math.radians(45)),math.cos(math.radians(45))]}),
                     ("rz30", {"rot": 30})):
    o = dict(base, **extra)
    r = P.build(s, MO, [o], Path("/tmp/t_%s.3mf" % label))
    bb = r["objects"][0]["bbox"]
    print(label, "bbox", bb, "size", [round(bb[i + 3] - bb[i], 3) for i in range(3)])
