#!/usr/bin/env python3
"""v0.6 全流程接口测试：配置编辑 → 摆盘 → 切片 → 预览 → 送机（不真打印）。

用法： python3 tests/flow_test.py http://127.0.0.1:8038
只读检查 + 一次真实切片（约 10 秒）。不会触发打印机动作。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8038").rstrip("/")
FAIL: list[str] = []


def req(path: str, method: str = "GET", body=None, raw: bytes | None = None,
        ctype: str = "application/json") -> tuple[int, object]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(BASE + path, data=data, method=method,
                              headers={"Content-Type": ctype} if data else {})
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            txt = resp.read()
            try:
                return resp.status, json.loads(txt or b"null")
            except ValueError:
                return resp.status, txt
    except urllib.error.HTTPError as e:
        txt = e.read()
        try:
            return e.code, json.loads(txt or b"null")
        except ValueError:
            return e.code, txt.decode("utf-8", "replace")


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ✓ " if cond else "  ✗ ") + name + (("  " + extra) if extra else ""))
    if not cond:
        FAIL.append(name)


print("== 健康检查")
st, h = req("/health")
check("health 200", st == 200)
check("schema 参数 > 700", h.get("schema_params", 0) > 700, str(h.get("schema_params")))
check("orca 版本", bool(h.get("orca")), str(h.get("orca")))

print("== 配置列表")
st, prof = req("/profiles")
check("profiles 200", st == 200)
check("有机器配置", len(prof.get("machine") or []) >= 1, str([p["name"] for p in prof.get("machine") or []]))
check("列表带来源标记", all("source" in p for p in (prof.get("machine") or [])))

print("== 配置详情（参数定义）")
mname = (prof.get("machine") or [{"name": ""}])[0]["name"]
st, m = req("/profiles/machine/%s" % urllib.parse.quote(mname))
check("machine 详情 200", st == 200)
check("每个参数都有定义", set(m["params"]) == set(m["doc"]),
      "%d 参数" % len(m.get("params") or {}))
nozh = [k for k, d in (m.get("params") or {}).items() if not d.get("zh")]
check("中文名覆盖 ≥ 90%", len(nozh) <= max(1, len(m["params"]) // 10), "缺中文 %d" % len(nozh))
notip = [k for k, d in (m.get("params") or {}).items() if not (d.get("tip_zh") or d.get("tip"))]
check("说明覆盖 ≥ 80%", len(notip) <= len(m["params"]) * 0.2, "缺说明 %d" % len(notip))
check("分组非空", len(m.get("groups") or []) > 2, str([g["cat"] for g in m.get("groups") or []]))

st, bed = req("/profiles/machine/%s/bed" % urllib.parse.quote(mname))
check("bed 200", st == 200 and bed.get("w"))
print("     热床", bed.get("w"), "x", bed.get("d"), "圆形" if bed.get("circular") else "矩形")

print("== 工艺配置参数覆盖")
pname = (prof.get("process") or [{"name": ""}])[0]["name"]
st, p = req("/profiles/process/%s" % urllib.parse.quote(pname))
check("process 详情 200", st == 200)
check("工艺参数有定义", set(p["params"]) == set(p["doc"]), "%d 参数" % len(p.get("doc") or {}))
check("覆盖统计", (p.get("coverage") or {}).get("total", 0) > 50, json.dumps(p.get("coverage")))

print("== 保存配置（写用户目录）")
doc = dict(m["doc"])
doc["printable_height"] = 200
st, saved = req("/profiles/machine/%s" % urllib.parse.quote("test-voron"), "PUT", {"doc": doc})
check("保存 200", st == 200, json.dumps(saved)[:120])
st, again = req("/profiles/machine/test-voron")
check("读回来一致", st == 200 and again["doc"].get("printable_height") == 200)
check("来源=user", again.get("source") == "user")
st, prof2 = req("/profiles")
check("列表里出现", any(x["name"] == "test-voron" for x in prof2["machine"]))
st, _ = req("/profiles/machine/test-voron", "DELETE")
check("删除用户配置", st == 200)

print("== 非法保存被拒")
st, err = req("/profiles/machine/bad", "PUT", {"doc": {"name": "bad", "inherits": "Voron 2.4"}})
check("inherits 被拒 400", st == 400, str(err)[:80])

print("== 模型库")
st, md = req("/models")
check("models 200", st == 200)
files = [x["file"] for x in md.get("models") or []]
check("有模型", bool(files), str(files))
target = files[0] if files else ""
st, info = req("/models/%s/info" % urllib.parse.quote(target))
check("模型摘要", st == 200 and info.get("hull"), "tris=%s" % info.get("tris"))

print("== 摆盘")
objs = [{"file": target, "x": 120, "y": 120, "rot": 0, "scale": 1, "extruder": 1},
        {"file": target, "x": 120, "y": 120, "rot": 45, "scale": 0.6, "extruder": 1}]
st, arr = req("/plate/arrange", "POST",
              {"bed": {"w": bed["w"], "d": bed["d"], "margin": 3}, "objects": objs})
check("自动摆盘", st == 200 and len(arr.get("objects") or []) == 2,
      json.dumps(arr.get("objects"))[:120])
placed = arr["objects"]
check("摆盘结果不重叠", abs(placed[0]["x"] - placed[1]["x"]) > 1 or abs(placed[0]["y"] - placed[1]["y"]) > 1)
st, chk = req("/plate/check", "POST", {"bed": bed, "objects": placed})
check("检查接口", st == 200)
print("     警告:", chk.get("warnings"))
st, chk2 = req("/plate/check", "POST",
               {"bed": bed, "objects": [dict(placed[0], x=bed["w"] + 50)]})
check("出床能查出来", any("超出" in w for w in chk2.get("warnings") or []), str(chk2.get("warnings")))

print("== 切片（2 个对象 → 1 个 3MF → Orca）")
fname = (prof.get("filament") or [{"name": ""}])[0]["name"]
t0 = time.time()
st, sub = req("/plate/slice", "POST", {
    "machine": mname, "process": pname, "filaments": [fname],
    "objects": [{"file": o["file"], "x": o["x"], "y": o["y"], "z": 0,
                 "rot": o["rot"], "scale": o["scale"], "extruder": 1} for o in placed],
    "overrides": {"layer_height": 0.24},
})
check("提交 202", st == 202, str(sub)[:160])
job_id = (sub or {}).get("job_id")
if not job_id:
    print("!! 没法继续")
    sys.exit(1)
plate_info = (sub or {}).get("plate") or {}
check("3MF 里有 2 个对象", len(plate_info.get("objects") or []) == 2, str(plate_info.get("bytes")) + " bytes")
for _ in range(180):
    st, job = req("/jobs/%s" % job_id)
    if job.get("status") in ("done", "failed"):
        break
    time.sleep(1.5)
check("切片完成", job.get("status") == "done", job.get("error") or "")
s = job.get("summary") or {}
check("层数 > 0", (s.get("layers") or 0) > 0, "%s 层 / %s" % (s.get("layers"), s.get("sliced_time")))
check("预览 ready", job.get("preview_status") == "ready", str(job.get("preview_status")))
check("层高覆盖生效", abs((s.get("layer_height") or 0) - 0.24) < 1e-6, str(s.get("layer_height")))
check("gcode 可下载", req("/jobs/%s/gcode" % job_id)[0] == 200)
st, idx = req("/jobs/%s/preview" % job_id)
check("预览层表", st == 200 and idx.get("layer_count", 0) > 0, str((idx or {}).get("segment_count")))
print("     耗时 %.1fs，job=%s" % (time.time() - t0, job_id))

print("== 打印机配置（不碰真机）")
st, pr = req("/printer")
check("读打印机配置", st == 200 and pr.get("url"), str(pr.get("url")))

# 把地址临时换成不通的端口，验证错误被正确捕获（不会真的往打印机写文件）
st, old = req("/printer")
st, _ = req("/printer", "PUT", {"url": "http://127.0.0.1:9", "name": old.get("name")})
check("改打印机地址", st == 200)
st, err = req("/jobs/job_not_exist/print", "POST", {"start": False})
check("不存在的任务 404", st == 404, str(err)[:60])
st, err = req("/printer/test", "POST")
check("连不上时返回 502", st == 502, str(err)[:80])
st, _ = req("/printer", "PUT", {"url": old.get("url"), "name": old.get("name")})
check("地址已还原", st == 200 and req("/printer")[1].get("url") == old.get("url"))

print()
if FAIL:
    print("失败 %d 项：%s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("全部通过")
