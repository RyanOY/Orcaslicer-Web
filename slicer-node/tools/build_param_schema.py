#!/usr/bin/env python3
"""从 OrcaSlicer 源码的 PrintConfig.cpp 抽参数定义，产出 app/schema/params.json。

为什么要抽：配置文件里只有 key 和值，没有名字、单位、范围、可选值。
OrcaSlicer 的 UI 之所以能画出漂亮的表单，是因为这些元数据都写死在
src/libslic3r/PrintConfig.cpp 里（def->label / tooltip / category / sidetext /
min / max / mode / enum_values）。860 多个参数，手抄不现实，所以用脚本解析。

用法（在容器里跑，因为要用它的 /opt/orca/resources）：
    python3 build_param_schema.py PrintConfig.cpp zh_CN.mo \
        /opt/orca/resources/profiles -o params.json

产物结构：
    {"version":1,"zh":true,
     "params":{"layer_height":{"t":"float","label":"Layer height","zh":"层高",
        "tip":"...","tip_zh":"...","cat":"Quality","cat_zh":"质量","unit":"mm",
        "min":0,"max":null,"mode":"simple","vals":[],"vlbl":[],"gui":"","def":0.2,
        "kinds":["process","filament"]}},
     "order":["layer_height", ...]}
"""
from __future__ import annotations

import argparse
import gettext
import json
import re
import sys
from pathlib import Path

ADD_RE = re.compile(r'\bdef\s*=\s*this->add(?:_nullable)?\(\s*"([A-Za-z0-9_]+)"\s*,\s*(co[A-Za-z]+)\s*\)')
STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def unescape(s: str) -> str:
    out = s.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    return out.replace("\\t", "\t")


def rhs_strings(expr: str) -> str | None:
    """把 `L("a\\n" "b")` / `u8"毫米"` 这类表达式还原成一段字符串。"""
    parts = STR_RE.findall(expr)
    if not parts:
        return None
    return unescape("".join(parts))


def grab(block: str, field: str) -> str | None:
    """取 `def->field = <expr>;` 的整个右侧（可能跨行）。"""
    m = re.search(r"def->%s\s*=\s*" % re.escape(field), block)
    if not m:
        return None
    i = m.end()
    depth = 0
    j = i
    while j < len(block):
        ch = block[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth <= 0:
            break
        j += 1
    return block[i:j].strip()


def grab_num(block: str, field: str) -> float | None:
    v = grab(block, field)
    if v is None:
        return None
    m = re.match(r"^(-?[0-9.]+)", v)
    if not m:
        return None
    try:
        f = float(m.group(1))
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


TYPE_MAP = {
    "coBool": "bool", "coBools": "bools",
    "coInt": "int", "coInts": "ints",
    "coFloat": "float", "coFloats": "floats",
    "coString": "string", "coStrings": "strings",
    "coPercent": "percent", "coPercents": "percents",
    "coFloatOrPercent": "float_or_percent", "coFloatsOrPercents": "floats_or_percents",
    "coPoint": "point", "coPoints": "points", "coPoint3": "point3",
    "coPoint3s": "points3", "coEnum": "enum", "coEnums": "enums",
}


def parse_cpp(text: str) -> dict[str, dict]:
    marks = [(m.start(), m.end(), m.group(1), m.group(2)) for m in ADD_RE.finditer(text)]
    params: dict[str, dict] = {}
    for idx, (_s, e, key, ctype) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(text)
        block = text[e:end]
        d: dict = {"t": TYPE_MAP.get(ctype, ctype[2:].lower())}
        label = grab(block, "label")
        if label:
            txt = rhs_strings(label)
            if txt:
                d["label"] = txt
        for f, k in (("tooltip", "tip"), ("sidetext", "unit"), ("category", "cat")):
            v = grab(block, f)
            if v:
                txt = rhs_strings(v)
                if txt:
                    d[k] = txt
        for f, k in (("min", "min"), ("max", "max")):
            n = grab_num(block, f)
            if n is not None:
                d[k] = n
        mode = grab(block, "mode")
        if mode:
            m = re.search(r"com(\w+)", mode)
            if m:
                d["mode"] = m.group(1).lower()
        gui = grab(block, "gui_type")
        if gui:
            g = re.search(r'"([a-z_]+)"', gui)
            if g:
                d["gui"] = g.group(1)
        vals = [unescape(x) for x in re.findall(r'def->enum_values\.push_back\("((?:[^"\\]|\\.)*)"\)', block)]
        if vals:
            d["vals"] = vals
            lbl = []
            for expr in re.findall(r"def->enum_labels\.push_back\((.*?)\);\s*$", block, re.M):
                s = rhs_strings(expr)
                lbl.append(s if s else "")
            if lbl and len(lbl) == len(vals):
                d["vlbl"] = lbl
        dv = re.search(r"set_default_value\(new (ConfigOption\w+)\s*[({]([^;}]*)[)}]\)?", block)
        if dv:
            raw = dv.group(2).strip()
            if re.fullmatch(r"-?[0-9.]+", raw):
                num = float(raw)
                d["def"] = int(num) if num.is_integer() else num
            elif raw in ("true", "false"):
                d["def"] = raw == "true"
            elif re.fullmatch(r'"[^"]*"', raw):
                d["def"] = raw[1:-1]
        params[key] = d
    return params


# 机器类参数在 PrintConfig.cpp 里大多没有 category（Orca 的机器设置是分页硬编码的），
# 所以按 key 前缀/关键词补一份，够 UI 分组用。
MACHINE_RULES: list[tuple[str, str]] = [
    (r"^(printer_model|printer_variant|printer_technology|printer_settings_id|printer_agent|name|inherits|version|from)$", "基础信息"),
    (r"(printable_area|printable_height|bed_shape|bed_exclude|max_print_height|printable_)", "打印尺寸"),
    (r"(nozzle_diameter|nozzle_type|nozzle_volume|nozzle_hrc|nozzle_flush|extruder_count"
     r"|extruder_type|extruder_offset|extruder_printable|extruder_clearance|extruder_colour"
     r"|extruder_nozzle|single_extruder|multi_extruder|wipe_tower_extruder|purge_in_prime"
     r"|^nozzle_)", "挤出机"),
    (r"(fan|cooling|auxiliary)", "风扇/冷却"),
    (r"(accel|max_accel|square_corner|jerk|velocity|minimum_cruise|max_speed|machine_limit|printer_limit|min_layer_time|slowdown|limit)", "运动能力"),
    (r"retract", "回抽"),
    (r"(^z_hop|z_offset|bed_temperature|^temperature|chamber_temper|nozzle_temper|hotend)", "温度/热床"),
    (r"(gcode|g_code|start_gcode|end_gcode|layer_change|pause|toolchange|before_layer|after_layer|time_lapse|custom_g)", "G-code"),
    (r"(thumbnails|silent|emit_machine|verbose)", "输出"),
    (r"(input_shaping|resonance|shaper|damping|smoothing)", "输入整形"),
    (r"(scan|scanning|laser|cutter|plotter)", "其他工艺"),
    (r"(wipe|purge|prime|ooze|park|stance|cooldown)", "擦料/待机"),
    (r"(time_cost|adaptive|resolution|arc_fitting|spiral|vase|sparse_layer|layer_gcode|support|overhang|brim|skirt|raft)", "其他工艺"),
]


def machine_cat(key: str) -> str:
    for pat, cat in MACHINE_RULES:
        if re.search(pat, key):
            return cat
    return "其他"


def keys_by_kind(profiles_dir: Path) -> dict[str, set[str]]:
    """扫一遍自带的所有厂商预设，收集每类配置里出现过的 key。"""
    out = {"machine": set(), "process": set(), "filament": set()}
    if not profiles_dir.is_dir():
        return out
    for kind in out:
        for p in profiles_dir.glob("*/%s/*.json" % kind):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict):
                out[kind].update(doc.keys())
    out["process"].update(out["filament"] and {"layer_height"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cpp")
    ap.add_argument("mo")
    ap.add_argument("profiles")
    ap.add_argument("-o", "--out", default="params.json")
    a = ap.parse_args()

    text = Path(a.cpp).read_text(encoding="utf-8", errors="replace")
    params = parse_cpp(text)

    tr = None
    try:
        with open(a.mo, "rb") as f:
            tr = gettext.GNUTranslations(f)
    except (OSError, ValueError) as e:
        print("警告：读不了 .mo，中文名会缺：%s" % e, file=sys.stderr)

    kinds = keys_by_kind(Path(a.profiles))

    for key, d in params.items():
        d["kinds"] = [k for k, ks in kinds.items() if key in ks]
        if tr is not None:
            if "label" in d:
                zh = tr.gettext(d["label"])
                if zh and zh != d["label"]:
                    d["zh"] = zh
            if "tip" in d:
                zh = tr.gettext(d["tip"])
                if zh and zh != d["tip"]:
                    d["tip_zh"] = zh
            if "cat" in d:
                zh = tr.gettext(d["cat"])
                if zh and zh != d["cat"]:
                    d["cat_zh"] = zh
            if "vlbl" in d:
                d["vlbl_zh"] = [tr.gettext(x) or x for x in d["vlbl"]]
        if "machine" in d["kinds"] and "cat" not in d:
            d["cat"] = machine_cat(key)
            if tr is not None:
                zh = tr.gettext(d["cat"])
                d["cat_zh"] = zh if zh and zh != d["cat"] else d["cat"]
        if "kinds" not in d or not d["kinds"]:
            # 没在自带预设里出现过的（新参数/冷门参数），按 PrintConfig 的 category 猜
            cat = d.get("cat", "")
            if cat in ("Quality", "Strength", "Speed", "Support material", "Multimaterial",
                       "Layers and perimeters", "Infill", "Skirt and brim", "Ironing",
                       "Fuzzy skin", "Precision", "G-code", "Post-processing", "Notes"):
                d["kinds"] = ["process"]
            else:
                d["kinds"] = []

    # 只留真正用得到的：有 label 的（避免把内部字段塞给用户）+ 出现过的 key
    useful = {k: v for k, v in params.items() if v.get("label") or v.get("kinds")}
    order = sorted(useful, key=lambda k: (useful[k].get("cat") or "zzz", k))
    out = {"version": 1, "source": "OrcaSlicer 2.4.2 PrintConfig.cpp",
           "params": {k: useful[k] for k in order}, "order": order}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")

    with_label = sum(1 for v in useful.values() if v.get("label"))
    with_zh = sum(1 for v in useful.values() if v.get("zh"))
    with_tip = sum(1 for v in useful.values() if v.get("tip"))
    with_vals = sum(1 for v in useful.values() if v.get("vals"))
    print("参数 %d 个（有名字 %d / 有中文名 %d / 有说明 %d / 有可选值 %d），%d KB"
          % (len(useful), with_label, with_zh, with_tip, with_vals,
             len(Path(a.out).read_bytes()) // 1024))
    print("各类型分布：", json.dumps({t: sum(1 for v in useful.values() if v["t"] == t)
                                 for t in sorted({v["t"] for v in useful.values()})},
                                ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
