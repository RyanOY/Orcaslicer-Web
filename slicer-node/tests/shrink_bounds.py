"""收缩率补偿要算进摆盘检查 / 自动缩放。

拿一个模型缩放到「刚好装进 350 热床」，然后：
  - 耗材 100%：检查无「超出热床」、fit 说放得下（factor=1）
  - 耗材 90%：模型被放大 11% → 超出热床；fit 给出 factor<1
  - 切片页覆盖 filament_shrink=90% 效果同 90% 耗材
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8039"


def call(path, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


models = call("/models")["models"]
m = max(models, key=lambda x: (x.get("w") or 0) * (x.get("d") or 0))
print("模型:", m["file"], "%.1f×%.1f×%.1f" % (m["w"], m["d"], m["h"]))

bed = {"w": 350, "d": 350, "margin": 3, "h": 300}
scale = round(330.0 / max(m["w"], m["d"]), 4)
obj = {"file": m["file"], "part": "", "name": m["file"], "x": 175, "y": 175,
       "rot": 0, "scale": scale, "extruder": 1}
print("scale=%.4f → 占地 %.1f×%.1f" % (scale, m["w"] * scale, m["d"] * scale))

base = call("/plate/check", {"bed": bed, "objects": [obj], "filaments": ["generic-pla"]})
print("100% 检查:", base["warnings"] or "无")

tmp = "zz-shrink-test"
src = dict(call("/profiles/filament/generic-pla")["doc"])
src["filament_shrink"] = "90%"
src["filament_shrinkage_compensation_z"] = "90%"
try:
    call("/profiles/filament/" + tmp, {"doc": src}, method="PUT")
    shr = call("/plate/check", {"bed": bed, "objects": [obj], "filaments": [tmp]})
    print("90%%  检查:", shr["warnings"] or "无")

    ov = call("/plate/check", {"bed": bed, "objects": [obj],
                               "filaments": ["generic-pla"],
                               "overrides": {"filament_shrink": "90%"}})
    print("覆盖 90% 检查:", ov["warnings"] or "无")

    f1 = call("/plate/fit", {"bed": bed, "objects": [obj], "filaments": ["generic-pla"]})
    f2 = call("/plate/fit", {"bed": bed, "objects": [obj], "filaments": [tmp]})
    print("fit 100%% factor=%.3f all_fit=%s" % (f1["factor"], f1["all_fit"]))
    print("fit  90%% factor=%.3f all_fit=%s" % (f2["factor"], f2["all_fit"]))

    out_ok = not base["warnings"] and any("超出热床" in w for w in shr["warnings"])
    ov_ok = any("超出热床" in w for w in ov["warnings"])
    fit_ok = f2["factor"] < f1["factor"] - 1e-6
    print("检查按放大后尺寸算:", "OK" if out_ok else "FAIL")
    print("覆盖也参与检查:", "OK" if ov_ok else "FAIL")
    print("fit 按放大后占地算:", "OK" if fit_ok else "FAIL")
finally:
    call("/profiles/filament/" + tmp, method="DELETE")
    print("临时耗材已删除")
