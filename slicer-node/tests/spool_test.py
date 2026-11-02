#!/usr/bin/env python3
"""料盘账本 + gcode 用量扫描的单元测试（不需要起服务）。

    python3 tests/spool_test.py

覆盖：
1. usage.scan 的总量与 Orca 自己写在 gcode 尾部的 `filament used [mm]` 对得上；
2. 多工具头能按 T 号拆开；
3. Ledger 的 创建/装槽/一次占两槽 的互斥/扣减/记录/损坏文件恢复。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import spools, usage  # noqa: E402

FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print("  ✓", name)
    else:
        FAIL.append(name)
        print("  ✗", name, detail)


def gcode_header(path: Path) -> list[float] | None:
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 400_000))
        tail = f.read().decode("utf-8", "replace")
    m = re.search(r"^; filament used \[mm\] = (.*)$", tail, re.M)
    return [float(x) for x in m.group(1).split(",")] if m else None


def test_usage() -> None:
    print("usage.scan:")
    root = Path(__file__).resolve().parent.parent
    files = sorted(root.glob("data06/jobs/*/out/*.gcode"))
    checked = multi = 0
    for p in files:
        want = gcode_header(p)
        if not want or max(want) <= 0:
            continue
        got = usage.scan(p)
        checked += 1
        if len([v for v in want if v > 0]) > 1:
            multi += 1
        nonzero = [(i, v) for i, v in enumerate(want) if v > 0]
        if len(nonzero) == 1:
            # 单色：总量应该和 Orca 自己的统计一致（误差 <1%）
            tool, val = nonzero[0]
            mine = got["per_tool_m"].get(str(tool))
            check("总量 %s" % p.parent.parent.name,
                  mine is not None and abs(mine * 1000 - val) <= max(1.0, val * 0.01),
                  "got %s want %s" % (mine, val / 1000))
            check("层数 %s" % p.parent.parent.name, got["layers"] > 0)
        else:
            # 多色：绝对值会和 Orca 不同（换料时的回抽/挤料 Orca 计入口径不一样），
            # 但**分摊比例**必须接近 —— 记账时正是按比例去乘 Moonraker 的实际用量。
            tot_o = sum(v for _, v in nonzero)
            tot_m = sum(got["per_tool_m"].values())
            ratios_ok = True
            detail = []
            for tool, val in nonzero:
                ro = val / tot_o
                rm = got["per_tool_m"].get(str(tool), 0) / tot_m if tot_m else 0
                detail.append("T%d %.3f/%.3f" % (tool, rm, ro))
                if abs(rm - ro) > 0.12:
                    ratios_ok = False
            check("多色分摊比例 %s" % p.parent.parent.name, ratios_ok, " ".join(detail))
    check("扫过至少一个 gcode", checked > 0, "files=%d" % len(files))
    print("  （共比对 %d 个文件，其中 %d 个多色）" % (checked, multi))


def test_ledger() -> None:
    print("Ledger:")
    with tempfile.TemporaryDirectory() as td:
        led = spools.Ledger(Path(td) / "spools.json")
        a = led.create({"name": "白", "total_m": 330, "material": "PLA"})
        b = led.create({"name": "黑", "total_m": 330, "slot": 0})
        check("新建两个料盘", len(led.state()["spools"]) == 2)

        led.assign(a["id"], 0)
        st = led.state()
        check("同槽互斥：白占用 T0 后黑被摘下",
              st["slots"][0]["spool"]["id"] == a["id"],
              str(st["slots"][0]))
        check("黑不再占槽", next(s for s in st["spools"] if s["id"] == b["id"])["slot"] is None)

        led.assign(b["id"], 1)
        rec = led.record(job_id="abc123", filename="x.gcode",
                         per_tool_m={"0": 10.0, "1": 2.5}, total_m=12.5, source="test")
        st = led.state()
        rem = {s["name"]: s["remaining_m"] for s in st["spools"]}
        check("按工具头分别扣减", rem["白"] == 320.0 and rem["黑"] == 327.5, str(rem))
        check("记录里有两笔扣减", len(rec["deduct"]) == 2, str(rec["deduct"]))
        check("记录能读回来", len(st["records"]) == 1)

        led.adjust(a["id"], {"delta_m": -20})
        check("手工扣减", led.get(a["id"])["remaining_m"] == 300.0)
        led.adjust(a["id"], {"remaining_m": 999})
        check("剩余不超过总量", led.get(a["id"])["remaining_m"] == 330.0)

        # 损坏文件不应丢数据：先备份再重建
        Path(led.path).write_text("{not json", encoding="utf-8")
        led2 = spools.Ledger(Path(led.path))
        st2 = led2.state()
        check("坏账本自动重建为空", st2["spools"] == [])
        check("坏账本留下备份", any(p.name.startswith("spools.broken-")
                                    for p in Path(td).iterdir()))

        # 删除料盘要顺手清掉槽位
        led2.write({"version": 1, "slots": {"0": {"spool": "sp_x"}},
                    "spools": [{"id": "sp_x", "name": "X", "total_m": 10,
                                "remaining_m": 5, "slot": 0}], "records": [], "pending": None})
        led2.delete("sp_x")
        check("删料盘清槽位", led2.state()["slots"][0]["spool"] is None)


def main() -> int:
    test_usage()
    test_ledger()
    print()
    if FAIL:
        print("失败 %d 项：%s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
