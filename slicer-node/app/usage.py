"""从 gcode 里统计耗材用量，**按工具头分开**。

为什么不用 Moonraker 的 `print_stats.filament_used`：那是整卷/整次打印的总量，
多色（MMU/ACE）打印时无法拆到每个槽位的料盘上。我们手上有 gcode 文件，
自己扫一遍 E 轴增量最准，而且离线可用、可复核。

规则（够用且不易出错）：
- 只累加**正向** E 增量：回抽是负的，忽略；
- 绝对挤出（M82）按 `E - last_e` 算增量，遇到 `G92 E` 重置基准；
- 相对挤出（M83）直接取 `E`；
- T0..T31 切换工具头，按工具头分别累计；
- `G92 E0` 之后的第一次移动不会算成一大坨（基准已重置）。

已知口径差异：多色打印时 Orca 的 `; filament used [mm]` 会把换料的挤料/回抽
按它自己的算法算进去，和这里的「净挤出」绝对值可能差一截（比例仍然一致）。
所以记账时**总量以 Moonraker 的 `print_stats.filament_used` 为准**，
这里的 per_tool 只用来决定「各槽位分摊多少」。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_RE_E = re.compile(r"E(-?\d*\.?\d+)", re.I)
_RE_F = re.compile(r"F(\d*\.?\d+)", re.I)
_RE_T = re.compile(r"^T(\d+)$")


def scan(path: str | Path) -> dict[str, Any]:
    """返回 {total_mm, per_tool: {"0": mm, ...}, layers, moves}。"""
    path = Path(path)
    total = 0.0
    pos_total = 0.0
    per_tool: dict[str, float] = {}
    absolute = True          # M82 是 PrusaSlicer/Orca 的默认
    last_e = 0.0
    tool = 0
    layers = 0
    moves = 0

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line[0] == ";":
                # Orca 的层标记：;LAYER_CHANGE 或 ;LAYER:12
                if line.startswith(";LAYER"):
                    layers += 1
                continue
            # 去掉行尾注释
            cmd = line.split(";", 1)[0].strip()
            if not cmd:
                continue

            if cmd in ("M82",):
                absolute = True
                continue
            if cmd in ("M83",):
                absolute = False
                continue
            if cmd.startswith("G92"):
                gm = _RE_E.search(cmd)
                last_e = float(gm.group(1)) if gm else 0.0
                continue

            m = _RE_T.match(cmd)
            if m:
                tool = int(m.group(1))
                per_tool.setdefault(str(tool), 0.0)
                last_e = 0.0
                continue

            head = cmd.split(None, 1)[0].upper()
            if head not in ("G1", "G0", "G2", "G3"):
                continue
            if head != "G1":
                # G0 一般不挤出，但有些切片器用 G0 走挤出；保险起见还是看 E
                pass
            em = _RE_E.search(cmd)
            if not em:
                continue
            try:
                e = float(em.group(1))
            except ValueError:
                continue
            moves += 1
            if absolute:
                delta = e - last_e
                last_e = e
            else:
                delta = e
            if delta > 0:
                pos_total += delta
            # 净挤出：回抽 -0.8 / 回填 +0.8 会相互抵消，
            # 这样算出来才和 Orca 自己写的 `filament used [mm]` 对得上。
            total += delta
            per_tool[str(tool)] = per_tool.get(str(tool), 0.0) + delta

    return {
        "total_mm": round(total, 1),
        "positive_mm": round(pos_total, 1),
        "total_m": round(total / 1000.0, 3),
        "per_tool": {k: round(v, 1) for k, v in sorted(per_tool.items(), key=lambda kv: int(kv[0]))},
        "per_tool_m": {k: round(v / 1000.0, 3) for k, v in sorted(per_tool.items(), key=lambda kv: int(kv[0]))},
        "layers": layers,
        "moves": moves,
    }
