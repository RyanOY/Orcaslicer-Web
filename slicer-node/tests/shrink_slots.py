"""双耗材：只有 T2 的耗材设了收缩率，验证是按槽位生效、不是全局生效。

跑法：python3 tests/shrink_slots.py
"""
import json
import time
import urllib.request

B = "http://127.0.0.1:8039"
FILE = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


try:
    api("/profiles/filament/generic-pla/clone", "POST", {"to": "shrink-slot2"})
except Exception:
    pass
doc = api("/profiles/filament/shrink-slot2")["doc"]
doc["filament_shrink"] = ["90%"]
doc["filament_shrinkage_compensation_z"] = "100%"
api("/profiles/filament/shrink-slot2", "PUT", {"doc": doc})

payload = {
    "machine": "voron24-350", "process": "020-standard",
    "filaments": ["generic-pla", "shrink-slot2"], "overrides": {},
    "objects": [
        {"file": FILE, "part": "", "name": "t1", "x": 60, "y": 175, "z": 0,
         "rot": 0, "scale": 1, "extruder": 1, "m": None},
        {"file": FILE, "part": "", "name": "t2", "x": 140, "y": 175, "z": 0,
         "rot": 0, "scale": 1, "extruder": 2, "m": None},
    ],
}
r = api("/plate/slice", "POST", payload)
print("job", r["job_id"])
for o in r["plate"]["objects"]:
    bb = o["bbox"]
    print("  %s T%d 尺寸 %.2f × %.2f × %.2f" % (o["file"][:8], o["extruder"],
                                                bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]))
t1 = r["plate"]["objects"][0]["bbox"]
t2 = r["plate"]["objects"][1]["bbox"]
w1, w2 = t1[3] - t1[0], t2[3] - t2[0]
print("  比值 %.4f（期望 1.1111）" % (w2 / w1))
assert abs(w2 / w1 - 100 / 90) < 0.02, "T2 的收缩率没有按槽位生效"
assert abs(t2[5] - t1[5]) < 0.01, "Z 都是 100%，高度不该变"

jid = r["job_id"]
for _ in range(180):
    j = api("/jobs/" + jid)
    if j["status"] in ("done", "failed"):
        break
    time.sleep(2)
print("切片:", j["status"], (j.get("summary") or {}).get("layers"), "层")
assert j["status"] == "done"

api("/profiles/filament/shrink-slot2", "DELETE")
print("按槽位生效通过（测试 profile 已删除）")
