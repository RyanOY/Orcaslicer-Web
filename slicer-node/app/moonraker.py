"""Moonraker 的「实时」一面：状态快照、温度、手动移动、发 gcode、打印动作。

`printer.py` 只管「把文件送过去并开打」；这里管交互式控制。两者共用同一份
连接配置和 `_req()`。

安全边界（故意的）：
- 这里所有动作都只在用户显式点按钮/发命令时触发，绝不轮询后自动动作；
- 挤出(E)手动移动单独走 M83 相对模式，避免把绝对坐标搞乱；
- 单次点动上限 100 mm，gcode 脚本长度上限 8000 字符。
"""
from __future__ import annotations

import re
import time
import urllib.parse
from typing import Any

from .printer import PrinterError, _req

_WANTED = ["print_stats", "virtual_sdcard", "toolhead", "gcode_move", "heater_bed",
           "extruder", "display_status", "idle_timeout", "fan", "ace", "ace_state",
           "ace_instance_0", "temperature_sensor chamber",
           "temperature_sensor acepro_temp_1", "temperature_sensor acepro_temp_2"]

_OBJ_CACHE: dict[str, Any] = {"at": 0.0, "objects": []}


def objects(cfg: dict) -> list[str]:
    """Klipper 遇到不存在的对象会让整个 query 失败，所以先问有哪些。"""
    now = time.time()
    if now - float(_OBJ_CACHE["at"]) < 30 and _OBJ_CACHE["objects"]:
        return list(_OBJ_CACHE["objects"])
    try:
        res = _req(cfg, "/printer/objects/list")
        objs = [str(o) for o in (res.get("result") or {}).get("objects") or []]
    except PrinterError:
        return list(_OBJ_CACHE["objects"])
    _OBJ_CACHE.update({"at": now, "objects": objs})
    return objs


def _query(cfg: dict, names: list[str]) -> dict[str, Any]:
    known = set(objects(cfg))
    want = [n for n in names if n in known]
    want += sorted(n for n in known if re.fullmatch(r"extruder\d+", n))
    if not want:
        return {}
    q = "&".join(urllib.parse.quote(n, safe="") for n in want)
    res = _req(cfg, "/printer/objects/query?" + q)
    return (res.get("result") or {}).get("status") or {}


def _t(obj: dict | None) -> dict[str, float]:
    obj = obj or {}
    return {"temp": round(float(obj.get("temperature") or 0), 1),
            "target": round(float(obj.get("target") or 0), 1)}


def live(cfg: dict) -> dict[str, Any]:
    """打印页用的实时快照：温度 / 进度 / 位置 / ACE 槽位 / 倍率。"""
    st = _query(cfg, _WANTED)
    ps = st.get("print_stats") or {}
    vs = st.get("virtual_sdcard") or {}
    th = st.get("toolhead") or {}
    gm = st.get("gcode_move") or {}
    info = ps.get("info") or {}

    extruders = []
    for name in ["extruder"] + sorted(n for n in st if re.fullmatch(r"extruder\d+", n)):
        if name in st:
            extruders.append({"name": name, **_t(st[name])})

    chamber = None
    for name in ("temperature_sensor chamber", "temperature_sensor acepro_temp_1"):
        if st.get(name):
            chamber = {"name": name.split()[-1], **_t(st[name])}
            break

    ace = st.get("ace_instance_0") or st.get("ace") or {}
    ace_top = st.get("ace") or st.get("ace_state") or {}
    cur_index = ace_top.get("current_index", ace.get("current_index"))
    slots = []
    for s in ace.get("slots") or []:
        rgb = s.get("color") or [0, 0, 0]
        try:
            hexcol = "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        except (TypeError, IndexError, ValueError):
            hexcol = ""
        slots.append({
            "index": s.get("index"), "tool": s.get("tool"),
            "status": s.get("status"), "color": hexcol,
            "material": s.get("material") or "", "temp": s.get("temp"),
            "rfid": bool(s.get("rfid")),
        })

    dryer = ace.get("dryer_status") or {}
    return {
        "ok": True,
        "state": ps.get("state") or "unknown",
        "state_message": ps.get("message") or "",
        "filename": ps.get("filename") or "",
        "progress": round(float(vs.get("progress") or 0) * 100, 1),
        "print_duration": int(float(ps.get("print_duration") or 0)),
        "total_duration": int(float(ps.get("total_duration") or 0)),
        "filament_used_mm": round(float(ps.get("filament_used") or 0), 1),
        "layer": {"current": info.get("current_layer"), "total": info.get("total_layer")},
        "extruders": extruders,
        "bed": _t(st.get("heater_bed")),
        "chamber": chamber,
        "position": [round(float(v), 2) for v in (th.get("position") or [0, 0, 0, 0])[:3]],
        "homed_axes": th.get("homed_axes") or "",
        "speed_factor": round(float(gm.get("speed_factor") or 1) * 100, 1),
        "extrude_factor": round(float(gm.get("extrude_factor") or 1) * 100, 1),
        "fan": round(float((st.get("fan") or {}).get("speed") or 0) * 100, 1),
        "ace": {
            "enabled": bool(ace),
            "current": cur_index,
            "temp": ace.get("temp"),
            "humidity": ace.get("humidity"),
            "dryer": {"status": dryer.get("status"),
                      "target": dryer.get("target_temp"),
                      "remain_s": dryer.get("remain_time")} if dryer else None,
            "slots": slots,
        },
    }


def gcode(cfg: dict, script: str, timeout: float = 20.0) -> dict[str, Any]:
    """发一段 gcode（/printer/gcode/script）。"""
    if not script or not script.strip():
        raise PrinterError("gcode 是空的")
    if len(script) > 8000:
        raise PrinterError("gcode 太长（>8000 字符）")
    return _req(cfg, "/printer/gcode/script?script=" + urllib.parse.quote(script),
                method="POST", timeout=timeout)


def set_temps(cfg: dict, bed: float | None = None,
              extruders: list[float | None] | None = None) -> dict[str, Any]:
    lines = []
    if bed is not None:
        lines.append("M140 S%g" % float(bed))
    for i, t in enumerate(extruders or []):
        if t is None:
            continue
        lines.append("M104 T%d S%g" % (i, float(t)))
    if not lines:
        raise PrinterError("没有要给的目标温度")
    return gcode(cfg, "\n".join(lines))


def jog(cfg: dict, axis: str, delta: float, feedrate: float = 3000) -> dict[str, Any]:
    axis = (axis or "").upper()
    if axis not in ("X", "Y", "Z", "E"):
        raise PrinterError("轴只能是 X/Y/Z/E")
    delta = float(delta)
    if abs(delta) > 100:
        raise PrinterError("单次移动最多 100 mm")
    if axis == "E":
        script = "M83\nG1 E%.3f F%g\nM82" % (delta, feedrate)
    else:
        script = "G91\nG1 %s%.3f F%g\nG90" % (axis, delta, feedrate)
    return gcode(cfg, script)


def action(cfg: dict, name: str) -> dict[str, Any]:
    name = (name or "").lower()
    if name in ("pause", "resume", "cancel"):
        return _req(cfg, "/printer/print/" + name, method="POST")
    if name == "emergency_stop":
        return _req(cfg, "/printer/emergency_stop", method="POST")
    if name == "firmware_restart":
        return _req(cfg, "/printer/firmware_restart", method="POST")
    if name == "home":
        return gcode(cfg, "G28", timeout=180)
    if name == "home_xy":
        return gcode(cfg, "G28 X Y", timeout=120)
    raise PrinterError("不认识的动作：%s" % name)


def history(cfg: dict, limit: int = 10) -> list[dict[str, Any]]:
    """Moonraker 的打印历史，用来和我们的料盘记录对账。"""
    res = _req(cfg, "/server/history/list?limit=%d&order=desc" % int(limit))
    jobs = (res.get("result") or {}).get("jobs") or []
    return [{
        "job_id": j.get("job_id"),
        "filename": j.get("filename"),
        "status": j.get("status"),
        "start_time": j.get("start_time"),
        "end_time": j.get("end_time"),
        "filament_used_mm": round(float(j.get("filament_used") or 0), 1),
        "print_duration": int(float(j.get("print_duration") or 0)),
    } for j in jobs]
