import json
import urllib.request

B = "http://127.0.0.1:8039"


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


try:
    api("/profiles/filament/generic-pla/clone", "POST", {"to": "shrink-qa"})
except Exception as e:
    print("clone:", e)

doc = api("/profiles/filament/shrink-qa")["doc"]
doc["filament_shrink"] = ["90%"]
doc["filament_shrinkage_compensation_z"] = "95%"
print("save:", api("/profiles/filament/shrink-qa", "PUT", {"doc": doc}).get("warnings"))
back = api("/profiles/filament/shrink-qa")["doc"]
print("读回:", back.get("filament_shrink"), back.get("filament_shrinkage_compensation_z"))

# 用这个 profile 切一刀，看尺寸
payload = {
    "machine": "voron24-350", "process": "020-standard", "filaments": ["shrink-qa"],
    "overrides": {},
    "objects": [{"file": "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf", "part": "", "name": "boat",
                 "x": 100, "y": 100, "z": 0, "rot": 0, "scale": 1, "extruder": 1, "m": None}],
}
r = api("/plate/slice", "POST", payload)
jid = r["job_id"]
print("job", jid, r["plate"]["objects"])
import time
for _ in range(180):
    j = api("/jobs/" + jid)
    if j["status"] in ("done", "failed"):
        break
    time.sleep(2)
print("status", j["status"], (j.get("summary") or {}).get("layers"), "层")
