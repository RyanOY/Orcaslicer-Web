"""参数元数据：让 UI 知道每个 key 是什么、怎么填。

数据来自 tools/build_param_schema.py 从 OrcaSlicer 源码 PrintConfig.cpp 抽出来的
`schema/params.json`（774 个参数，带英文名/中文名/说明/单位/范围/可选值）。
但一份 profile 里总有 schema 没覆盖的 key（新版参数、老 preset 遗留的别名、
`filament_xxx` 这种「耗材覆盖项」），所以这里还要能按规则推导：

    filament_retraction_length  → 取 retraction_length 的定义，标成「耗材覆盖」
    machine_max_speed_x         → 手写定义表（Orca 2.x 源码里已没有这些老 key）
    其余未知 key                → 按值推断类型，不装死也不乱编名字
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

META_KEYS = {
    "name", "from", "type", "inherits", "instantiation", "setting_id", "filament_id",
    "version", "is_custom_defined", "filament_vendor", "printer_settings_id",
    "print_settings_id", "filament_settings_id", "compatible_printers_condition",
    "compatible_printers", "aliases", "renamed_from", "preset_name", "inherits_group",
}

# Orca 2.x 的预设里还在用的老 key，源码里已经查不到了，只能手写
CURATED: dict[str, dict] = {
    "bed_shape": {"t": "points", "zh": "热床轮廓", "label": "Bed shape",
                  "cat": "打印尺寸", "unit": "mm",
                  "tip_zh": "热床多边形（老版参数，新版本叫 printable_area）"},
    "temperature": {"t": "floats", "zh": "喷嘴温度", "label": "Nozzle temperature",
                    "cat": "温度/热床", "unit": "°C",
                    "tip_zh": "喷头目标温度（老版参数，新版本叫 nozzle_temperature）"},
    "chamber_temperatures": {"t": "ints", "zh": "腔体温度", "label": "Chamber temperature",
                             "cat": "温度/热床", "unit": "°C"},
    "filament_z_hop_types": {"t": "strings", "zh": "Z 抬升类型", "label": "Z hop type",
                             "cat": "回抽", "vals": ["Auto Lift", "Normal Lift", "Spiral Lift"]},
}
for _axis, _ax in (("x", "X 轴"), ("y", "Y 轴"), ("z", "Z 轴"), ("e", "挤出机")):
    CURATED["machine_max_speed_%s" % _axis] = {
        "t": "floats", "zh": "最大速度 · %s" % _ax, "label": "Max speed %s" % _axis,
        "cat": "运动能力", "unit": "mm/s", "min": 0,
        "tip_zh": "机器允许的最高移动速度（Klipper 里对应 max_velocity）"}
    CURATED["machine_max_acceleration_%s" % _axis] = {
        "t": "floats", "zh": "最大加速度 · %s" % _ax, "label": "Max acceleration %s" % _axis,
        "cat": "运动能力", "unit": "mm/s²", "min": 0,
        "tip_zh": "机器允许的最大加速度（Klipper 里对应 max_accel）"}
    CURATED["machine_max_jerk_%s" % _axis] = {
        "t": "floats", "zh": "最大抖动 · %s" % _ax, "label": "Max jerk %s" % _axis,
        "cat": "运动能力", "unit": "mm/s", "min": 0,
        "tip_zh": "机器允许的最大 jerk / square corner velocity"}

# 分组顺序（按 Orca 界面习惯），没列到的排最后
CAT_ORDER = [
    "Quality", "质量", "Strength", "强度", "Speed", "速度", "Support", "支撑",
    "Multimaterial", "多材料", "Layers and Perimeters", "层与周长",
    "Precision", "精度", "Ironing", "熨烫", "Skirt and brim", "裙边",
    "Fuzzy skin", "毛绒表面", "基础信息", "打印尺寸", "挤出机", "Extruders",
    "运动能力", "Machine limits", "温度/热床", "Temperature", "Cooling", "风扇/冷却",
    "Retraction", "回抽", "Flush options", "擦料/待机", "G-code", "输出",
    "输入整形", "其他工艺", "收缩率", "耗材", "Filament", "Others", "Other", "其他",
    "Advanced", "高级", "元数据",
]

# 已有参数在 UI 上的「归组 / 可见性 / 叫法」微调。只改展示层，不动类型和值语义：
# params.json 是从 Orca 源码抽的，带 t/min/max/def，但**没有 cat**，所以所有参数
# 默认都掉进「其他」，简单视图里还因为 mode=advanced 一律不显示。收缩率就是典型：
# 参数明明存在，用户在耗材配置里却找不到。
UI_OVERRIDES: dict[str, dict] = {
    "filament_shrink": {
        "cat": "收缩率", "cat_zh": "收缩率", "mode": "simple",
        "zh": "收缩率 XY", "label": "Shrinkage (XY)", "unit": "%",
        "tip_zh": "耗材冷却后的收缩比例。实测 100mm 打印出来只有 95mm，就填 95 —— "
                  "模型会在 XY 方向放大 100/95 来补偿。100 表示不补偿。\n"
                  "补偿是在边界检查之后做的，摆盘时记得多留点间距。",
    },
    "filament_shrinkage_compensation_z": {
        "cat": "收缩率", "cat_zh": "收缩率", "mode": "simple",
        "zh": "收缩率 Z", "label": "Shrinkage (Z)", "unit": "%",
        "tip_zh": "同上，但只作用于 Z（高度）。实测 100mm 高打印出来只有 98mm，就填 98，"
                  "模型会在 Z 方向放大 100/98。100 表示不补偿。\n"
                  "注意：Z 补偿也是缩放模型本体，层高/层数会跟着变（0.2mm 层高会变成 0.196mm）。",
    },
}

class Schema:
    def __init__(self, path: Path):
        self.path = path
        self.params: dict[str, dict] = {}
        self.order: list[str] = []
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.params = doc.get("params") or {}
            self.order = doc.get("order") or list(self.params)

    # ---- 单个参数 ----
    def get(self, key: str, value: Any = None) -> dict:
        d = self.params.get(key)
        if d:
            out = dict(d)
            # 展示层微调（归组/可见性/中文叫法），类型与取值范围仍以 Orca 源码为准
            ov = UI_OVERRIDES.get(key)
            if ov:
                out.update(ov)
            if value is not None:
                out["v"] = value
            return out
        if key.startswith("filament_"):
            base = self.params.get(key[9:])
            if base:
                out = dict(base)
                out["derived"] = key[9:]
                out["zh"] = "耗材 · " + (base.get("zh") or base.get("label") or key[9:])
                out["label"] = "Filament override: " + (base.get("label") or key[9:])
                out["cat"] = "耗材"
                out["cat_zh"] = "耗材"
                out.setdefault("kinds", ["filament"])
                if value is not None:
                    out["v"] = value
                return out
        if key in CURATED:
            out = dict(CURATED[key])
            out.setdefault("label", key)
            out.setdefault("zh", key)
            if value is not None:
                out["v"] = value
            return out
        if key in META_KEYS:
            return {"t": "meta", "label": key, "zh": key, "cat": "元数据", "v": value}
        out = {"t": _infer_type(value), "label": key, "zh": key, "cat": "其他",
               "unknown": True, "v": value}
        return out

    # ---- 整份 profile ----
    def describe(self, kind: str, doc: dict) -> dict[str, Any]:
        out: dict[str, dict] = {}
        groups: dict[str, list[str]] = {}
        for key, value in doc.items():
            d = self.get(key, value)
            if d.get("t") == "meta":
                d["zh"] = d.get("zh") or key
            out[key] = d
            cat = d.get("cat_zh") or d.get("cat") or "其他"
            groups.setdefault(cat, []).append(key)
        # 有些参数很重要但老 preset 里根本没写（比如耗材收缩率），describe 只看 doc
        # 的 key，缺了就等于「界面上没有」。这里按 kind 强制补上，值用 Orca 的默认值，
        # 用户不动它就不会写进 profile。
        for key in UI_OVERRIDES:
            if key in out:
                continue
            meta = self.params.get(key) or {}
            if kind in (meta.get("kinds") or []):
                d = self.get(key)
                out[key] = d
                groups.setdefault(d.get("cat_zh") or d.get("cat") or "其他", []).append(key)
        order = {c: i for i, c in enumerate(CAT_ORDER)}
        cats = sorted(groups, key=lambda c: (order.get(c, 999), c))
        return {
            "kind": kind,
            "params": out,
            "groups": [{"cat": c, "keys": groups[c]} for c in cats],
            "coverage": {
                "total": len(out),
                "known": sum(1 for d in out.values() if not d.get("unknown")),
                "with_tip": sum(1 for d in out.values() if d.get("tip_zh") or d.get("tip")),
                "with_vals": sum(1 for d in out.values() if d.get("vals")),
            },
        }

    def for_kind(self, kind: str) -> dict[str, Any]:
        """某类配置里「可能出现」的参数全集（用来给新建配置列默认值）。"""
        keys = [k for k in self.order if kind in (self.params[k].get("kinds") or [])]
        for k, d in CURATED.items():
            if kind == "machine":
                keys.append(k)
        doc = {}
        for k in keys:
            d = self.params.get(k) or CURATED.get(k) or {}
            if "def" in d:
                doc[k] = d["def"]
        return self.describe(kind, doc)


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        if value and all(isinstance(x, list) and len(x) in (2, 3) for x in value):
            return "points"
        if value and all(isinstance(x, bool) for x in value):
            return "bools"
        if value and all(isinstance(x, str) for x in value):
            return "strings"
        if value and all(isinstance(x, (int, float)) for x in value):
            return "floats"
        return "strings"
    return "meta"
