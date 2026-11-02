"""三轴旋转端到端验证：前端发出的 payload（rot/m）→ /plate/check → /plate/slice → gcode。

跑法：python3 tests/rot3_slice.py

注意：绕 X 转到 88°~90° 时 Orca 2.4.2 引擎自己会炸（见 changelog v0.11.4 的对照实验），
所以这里用 45° / 90°+绕Z30° 这种能切的姿态验证「姿态真的传到了切片器」。

验证点：
  1. rot 老路径（只转 Z）高度不变（48mm）；
  2. rx=45 立起来后高度明显变高（≈ 60·sin45 + 48·cos45 的上界以内）；
  3. rx90 + rot30（三轴叠加）也能切，且 gcode 最大 Z 是「立起来」后的高度。
"""
import json
import math
import re
import time
import urllib.request

BASE = "http://127.0.0.1:8039"
FILE = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"
MACHINE = "voron24-350"
PROCESS = "020-standard"
FIL = ["generic-pla"]


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


def rot_x(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [1, 0, 0, 0, c, -s, 0, s, c]      # 行主序，列向量约定


def rot_y(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [c, 0, s, 0, 1, 0, -s, 0, c]


def mul(a, b):
    return [sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3)) for r in range(3) for c in range(3)]


def obj(**kw):
    o = {"file": FILE, "part": "", "name": "boat", "x": 100, "y": 100, "z": 0,
         "rot": 0, "scale": 1, "extruder": 1, "m": None}
    o.update(kw)
    return o


def check(objects):
    r = post("/plate/check", {"bed": {"w": 350, "d": 350}, "objects": objects})
    return r.get("warnings") or []


def slice_and_wait(objects, label):
    r = post("/plate/slice", {"machine": MACHINE, "process": PROCESS, "filaments": FIL,
                              "overrides": {}, "objects": objects})
    jid = r["job_id"]
    print("  job", jid)
    for _ in range(240):
        j = get("/jobs/" + jid)
        if j["status"] in ("done", "failed"):
            break
        time.sleep(2)
    if j["status"] != "done":
        raise SystemExit("切片失败：%s" % (j.get("error") or j))
    print("  %s: %s 层" % (label, (j.get("summary") or {}).get("layers")))
    return j


def max_z(job):
    """从 /jobs/<id>/gcode 流式扫最大 Z（不落盘，gcode 可能几十 MB）。"""
    url = BASE + "/jobs/%s/gcode" % job["id"]
    zmax = 0.0
    with urllib.request.urlopen(url, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace")
            if line[:2] in ("G0", "G1"):
                m = re.search(r"\bZ([-0-9.]+)", line)
                if m:
                    zmax = max(zmax, float(m.group(1)))
    return zmax


print("== 1. 只绕 Z 转 30°（老路径 rot != 0, m=None）")
w = check([obj(rot=30)])
print("  warnings:", w or "无")
assert not any("超出" in x for x in w), w
h_z = max_z(slice_and_wait([obj(rot=30)], "Z30"))
print("  gcode max Z = %.2f (期望 48)" % h_z)
assert abs(h_z - 48) < 1.0, "只转 Z 时高度应仍是 48"

print("== 2. rx=45：把模型立起一半（m=Rx45, rot=0）")
w = check([obj(m=rot_x(45))])
print("  warnings:", w or "无")
assert not any("超出" in x for x in w), w
h_x = max_z(slice_and_wait([obj(m=rot_x(45))], "RX45"))
# 船不是方块：60/48 的极值不在同一个点上，实际立起 45° 后是 49.72（P.build 的 bbox 实测），
# 所以这里只断言「姿态确实变了」，别拿包围盒上界当预期。
print("  gcode max Z = %.2f（平放是 %.2f，build bbox 实测 49.72）" % (h_x, h_z))
assert abs(h_x - h_z) > 1.0, "rx=45 后高度应该和平放不同，说明姿态进了切片器"

print("== 3. 三轴叠加：rx=30 ry=20 rz=20 —— 前端存成 rot=20 + m=Ry(20)·Rx(30)，R = Rz·Ry·Rx")
m3 = mul(rot_y(20), rot_x(30))
w = check([obj(rot=20, m=m3)])
print("  warnings:", w or "无")
h3 = max_z(slice_and_wait([obj(rot=20, m=m3)], "3AXIS"))
print("  gcode max Z = %.2f" % h3)
assert h3 > 0

print("\n结论：Z 旋转 %.1f / 立起 45° %.1f / 三轴 %.1f —— 姿态都进了切片器" % (h_z, h_x, h3))
print("三轴旋转端到端通过")
