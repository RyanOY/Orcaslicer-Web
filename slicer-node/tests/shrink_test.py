"""耗材收缩率（XY / Z）端到端验证：profile 参数 → /plate/slice → gcode 实际尺寸。

Orca 的收缩率是「补偿」语义：填 90 表示打印出来只剩 90%，切片时把模型放大 100/90
（≈1.1111）。所以：
  - 只开 XY：X/Y 尺寸 ×1.111，Z 和层高不变；
  - 只开 Z ：Z 高度 ×1.111，层数变多（0.2mm 层 → 0.18mm 层），X/Y 不变。

跑法：python3 tests/shrink_test.py
"""
import json
import re
import time
import urllib.request

BASE = "http://127.0.0.1:8039"
FILE = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"
MACHINE = "voron24-350"
PROCESS = "020-standard"


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit("HTTP %s %s: %s" % (e.code, path, e.read().decode()[:400]))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.loads(r.read())


def slice_wait(filament, overrides, label):
    payload = {
        "machine": MACHINE, "process": PROCESS, "filaments": [filament],
        "overrides": overrides,
        "objects": [{"file": FILE, "part": "", "name": "boat", "x": 100, "y": 100, "z": 0,
                     "rot": 0, "scale": 1, "extruder": 1, "m": None}],
    }
    r = post("/plate/slice", payload)
    jid = r["job_id"]
    print("  %-16s job %s" % (label, jid))
    for _ in range(240):
        j = get("/jobs/" + jid)
        if j["status"] in ("done", "failed"):
            break
        time.sleep(2)
    if j["status"] != "done":
        raise SystemExit("切片失败：%s" % (j.get("error") or j))
    return j


def bbox(job):
    """扫 gcode 求 XYZ 范围（只有带 X/Y/Z 的 G0/G1 才算）。"""
    xmin = ymin = zmin = 1e9
    xmax = ymax = zmax = -1e9
    url = BASE + "/jobs/%s/gcode" % job["id"]
    with urllib.request.urlopen(url, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace")
            if line[:2] not in ("G0", "G1", "G2", "G3"):
                continue
            for axis, val in re.findall(r"\b([XYZ])([-0-9.]+)", line):
                v = float(val)
                if axis == "X":
                    xmin, xmax = min(xmin, v), max(xmax, v)
                elif axis == "Y":
                    ymin, ymax = min(ymin, v), max(ymax, v)
                else:
                    zmin, zmax = min(zmin, v), max(zmax, v)
    return {"dx": xmax - xmin, "dy": ymax - ymin, "dz": zmax - zmin,
            "layers": (job.get("summary") or {}).get("layers")}


FACTOR = 100.0 / 90.0

print("== 基线：generic-pla，不补偿")
base = bbox(slice_wait("generic-pla", {}, "baseline"))
print("   X %.2f  Y %.2f  Z %.2f  (%s 层)" % (base["dx"], base["dy"], base["dz"], base["layers"]))

print("== XY 收缩 90%")
xy = bbox(slice_wait("generic-pla", {"filament_shrink": 90}, "xy90"))
print("   X %.2f  Y %.2f  Z %.2f  (%s 层)" % (xy["dx"], xy["dy"], xy["dz"], xy["layers"]))
print("   比值 X %.4f  Y %.4f  Z %.4f（期望 X/Y≈%.4f，Z≈1）"
      % (xy["dx"] / base["dx"], xy["dy"] / base["dy"], xy["dz"] / base["dz"], FACTOR))
assert abs(xy["dx"] / base["dx"] - FACTOR) < 0.02, "XY 补偿没生效（X）"
assert abs(xy["dy"] / base["dy"] - FACTOR) < 0.02, "XY 补偿没生效（Y）"
assert abs(xy["dz"] / base["dz"] - 1.0) < 0.01, "开了 XY 补偿不该改 Z"

print("== Z 收缩 90%")
z = bbox(slice_wait("generic-pla", {"filament_shrinkage_compensation_z": 90}, "z90"))
print("   X %.2f  Y %.2f  Z %.2f  (%s 层)" % (z["dx"], z["dy"], z["dz"], z["layers"]))
print("   比值 X %.4f  Y %.4f  Z %.4f（期望 Z≈%.4f，X/Y≈1）"
      % (z["dx"] / base["dx"], z["dy"] / base["dy"], z["dz"] / base["dz"], FACTOR))
assert abs(z["dz"] / base["dz"] - FACTOR) < 0.02, "Z 补偿没生效"
assert abs(z["dx"] / base["dx"] - 1.0) < 0.01, "开了 Z 补偿不该改 XY"

print("\n结论：XY %.2f→%.2f (×%.4f)，Z %.2f→%.2f (×%.4f)，层数 %s→%s"
      % (base["dx"], xy["dx"], xy["dx"] / base["dx"], base["dz"], z["dz"],
         z["dz"] / base["dz"], base["layers"], z["layers"]))
print("耗材收缩率端到端通过")
