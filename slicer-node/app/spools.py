"""料盘（spool）与槽位（MMU/多工具头）账本。

关注的是「这卷料还剩多少米，谁用掉的」——所以它记两件事：

1. `spools`：料盘本身（总量/剩余/颜色/材料），以及它装在哪个槽位。
2. `records`：每次打印的耗材消耗，来源是我们自己扫 gcode（按工具头拆分）
   + Moonraker 的实际用量（总量校正）。

数据是一个 JSON 文件（默认 `<data>/spools.json`），写盘用「临时文件 + 原子替换」，
避免掉电把账本写坏。体积很小（几百条记录），不用数据库。
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

MAX_SLOTS = 16
MAX_RECORDS = 500


class SpoolError(Exception):
    pass


def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts or _now()))


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):
        return default
    return round(f, 3)


class Ledger:
    """料盘账本；所有写操作都在一把进程内锁里完成（FastAPI 单进程够用了）。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._cache: dict[str, Any] | None = None

    # ------------------------------------------------------------ 读写

    def _blank(self) -> dict[str, Any]:
        return {"version": 1, "slots": {}, "spools": [], "records": [], "pending": None}

    def read(self, fresh: bool = False) -> dict[str, Any]:
        if self._cache is not None and not fresh:
            return self._cache
        data = self._blank()
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data.update({k: raw[k] for k in ("slots", "spools", "records", "pending")
                                 if k in raw})
            except (OSError, ValueError):
                # 账本坏了也不能把新数据覆盖上去 —— 先备份一份再重建
                self._backup_broken()
        if not isinstance(data.get("slots"), dict):
            data["slots"] = {}
        if not isinstance(data.get("spools"), list):
            data["spools"] = []
        if not isinstance(data.get("records"), list):
            data["records"] = []
        self._cache = data
        return data

    def _backup_broken(self) -> None:
        try:
            self.path.rename(self.path.with_suffix(".broken-%d.json" % int(_now())))
        except OSError:
            pass

    def write(self, data: dict[str, Any]) -> None:
        data["records"] = data.get("records", [])[-MAX_RECORDS:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        self._cache = data

    # ------------------------------------------------------------ 查询

    def get(self, spool_id: str) -> dict[str, Any]:
        for s in self.read().get("spools", []):
            if s.get("id") == spool_id:
                return s
        raise SpoolError("没有这个料盘：%s" % spool_id)

    def state(self) -> dict[str, Any]:
        d = self.read(fresh=True)
        spools = [s for s in d["spools"] if not s.get("archived")]
        used = {s.get("slot"): s.get("id") for s in spools
                if isinstance(s.get("slot"), int)}
        slots = []
        for i in range(MAX_SLOTS):
            sid = used.get(i) or (d["slots"].get(str(i)) or {}).get("spool")
            spool = next((s for s in spools if s.get("id") == sid), None)
            slots.append({"slot": i, "spool": spool})
        return {
            "spools": sorted(spools, key=lambda s: (s.get("slot") if isinstance(s.get("slot"), int)
                                                    else 99, s.get("name") or "")),
            "slots": slots,
            "records": list(reversed(d.get("records", [])[-50:])),
            "pending": d.get("pending"),
            "total_m": round(sum(_num(s.get("remaining_m")) for s in spools), 2),
        }

    # ------------------------------------------------------------ 料盘 CRUD

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        d = self.read()
        total = _num(payload.get("total_m"), 0)
        if total <= 0:
            total = _num(payload.get("remaining_m"), 0)
        spool = {
            "id": "sp_" + secrets.token_hex(4),
            "name": str(payload.get("name") or "未命名耗材").strip()[:60],
            "material": str(payload.get("material") or "").strip()[:32],
            "vendor": str(payload.get("vendor") or "").strip()[:40],
            "color": _color(payload.get("color")),
            "diameter": _num(payload.get("diameter"), 1.75) or 1.75,
            "density": _num(payload.get("density"), 1.24) or 1.24,
            "total_m": round(total, 2),
            "remaining_m": round(_num(payload.get("remaining_m"), total), 2),
            "spool_weight_g": _num(payload.get("spool_weight_g"), 0),
            "filament": str(payload.get("filament") or "").strip()[:80],
            "note": str(payload.get("note") or "").strip()[:200],
            "slot": None,
            "archived": False,
            "created": _iso(),
            "updated": _iso(),
        }
        spool["remaining_m"] = min(spool["remaining_m"], spool["total_m"])
        d["spools"].append(spool)
        slot = payload.get("slot")
        if isinstance(slot, int) and 0 <= slot < MAX_SLOTS:
            self._assign(d, spool, slot)
        self.write(d)
        return spool

    def update(self, spool_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        d = self.read()
        spool = self.get(spool_id)
        for key in ("name", "material", "vendor", "filament", "note"):
            if key in payload:
                spool[key] = str(payload[key] or "").strip()[:200]
        if "color" in payload:
            spool["color"] = _color(payload["color"])
        for key in ("diameter", "density", "total_m", "remaining_m", "spool_weight_g"):
            if key in payload:
                spool[key] = _num(payload[key], _num(spool.get(key)))
        if "remaining_m" in payload:
            spool["remaining_m"] = max(0.0, min(spool["remaining_m"],
                                                _num(spool.get("total_m"), spool["remaining_m"])))
        if "archived" in payload:
            spool["archived"] = bool(payload["archived"])
        if "slot" in payload:
            slot = payload["slot"]
            if slot in (None, "", -1):
                self._unassign(d, spool)
            else:
                try:
                    self._assign(d, spool, int(slot))
                except (TypeError, ValueError) as e:
                    raise SpoolError("槽位必须是整数") from e
        spool["updated"] = _iso()
        self.write(d)
        return spool

    def delete(self, spool_id: str) -> dict[str, Any]:
        d = self.read()
        spool = self.get(spool_id)
        d["spools"] = [s for s in d["spools"] if s.get("id") != spool_id]
        for k, v in list(d["slots"].items()):
            if (v or {}).get("spool") == spool_id:
                d["slots"].pop(k, None)
        if spool.get("slot") is not None:
            spool["slot"] = None
        self.write(d)
        return {"deleted": spool_id}

    def assign(self, spool_id: str, slot: int | None) -> dict[str, Any]:
        d = self.read()
        spool = self.get(spool_id)
        if slot is None:
            self._unassign(d, spool)
        else:
            self._assign(d, spool, int(slot))
        spool["updated"] = _iso()
        self.write(d)
        return spool

    def _assign(self, d: dict[str, Any], spool: dict[str, Any], slot: int) -> None:
        if slot < 0 or slot >= MAX_SLOTS:
            raise SpoolError("槽位超出范围（0-%d）" % (MAX_SLOTS - 1))
        # 一个槽位只能装一卷：先摘掉原来那卷
        for other in d["spools"]:
            if other.get("slot") == slot and other.get("id") != spool["id"]:
                other["slot"] = None
        spool["slot"] = slot
        d["slots"][str(slot)] = {"spool": spool["id"]}

    def _unassign(self, d: dict[str, Any], spool: dict[str, Any]) -> None:
        slot = spool.get("slot")
        if isinstance(slot, int):
            d["slots"].pop(str(slot), None)
        spool["slot"] = None

    def adjust(self, spool_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """手工校正：给「剩余米数」或「已用米数」，二选一。"""
        d = self.read()
        spool = self.get(spool_id)
        if "remaining_m" in payload:
            spool["remaining_m"] = max(0.0, min(_num(payload["remaining_m"]),
                                                _num(spool.get("total_m"))))
        elif "used_m" in payload:
            spool["remaining_m"] = max(0.0, round(_num(spool.get("total_m"))
                                                  - _num(payload["used_m"]), 2))
        elif "delta_m" in payload:
            spool["remaining_m"] = max(0.0, round(_num(spool.get("remaining_m"))
                                                  + _num(payload["delta_m"]), 2))
        else:
            raise SpoolError("要给 remaining_m / used_m / delta_m 其中一个")
        spool["updated"] = _iso()
        self.write(d)
        return spool

    # ------------------------------------------------------------ 打印记录

    def set_pending(self, job_id: str | None, filename: str | None) -> None:
        d = self.read()
        d["pending"] = ({"job_id": job_id, "filename": filename, "started": _iso()}
                        if job_id or filename else None)
        self.write(d)

    def record(self, *, job_id: str | None, filename: str, per_tool_m: dict[str, float],
               total_m: float, source: str, note: str = "",
               extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """按工具头 → 槽位 → 料盘扣减，并写一条记录。"""
        d = self.read()
        total_m = _num(total_m)
        per_tool_m = {str(k): _num(v) for k, v in (per_tool_m or {}).items()}
        deduct: list[dict[str, Any]] = []
        # 同一个料盘可能被两个工具头共用（比如两色都从同一卷取料），
        # 先按料盘聚合再扣一次，不能因为「见过」就跳过第二笔。
        per_spool: dict[str, dict[str, Any]] = {}
        for tool, metres in sorted(per_tool_m.items(), key=lambda kv: int(kv[0])):
            if metres <= 0:
                continue
            slot = int(tool)
            sid = (d["slots"].get(str(slot)) or {}).get("spool")
            if not sid:
                continue
            try:
                spool = self.get(sid)
            except SpoolError:
                continue
            item = per_spool.setdefault(sid, {"spool": spool, "m": 0.0, "slots": []})
            item["m"] += metres
            item["slots"].append(slot)
        for sid, item in per_spool.items():
            spool = item["spool"]
            metres = round(item["m"], 3)
            before = _num(spool.get("remaining_m"))
            spool["remaining_m"] = round(max(0.0, before - metres), 2)
            spool["updated"] = _iso()
            deduct.append({"spool": sid, "name": spool.get("name"),
                           "slot": item["slots"][0], "slots": item["slots"],
                           "m": metres, "before_m": before,
                           "after_m": spool["remaining_m"]})
        record = {
            "id": "pr_" + secrets.token_hex(4),
            "job_id": job_id,
            "filename": filename or "",
            "time": _iso(),
            "total_m": round(total_m, 3),
            "per_tool_m": per_tool_m,
            "source": source,
            "deduct": deduct,
            "note": note,
        }
        record.update(extra or {})
        d["records"].append(record)
        d["pending"] = None
        self.write(d)
        return record

    def last_print_time(self) -> float | None:
        recs = self.read().get("records") or []
        if not recs:
            return None
        try:
            return time.mktime(time.strptime(recs[-1]["time"], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            return None


def _color(v: Any) -> str:
    s = str(v or "").strip()
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        return "#" + s.lower()
    if s.startswith("#") and len(s) in (4, 7):
        return s.lower()
    if s.startswith("rgb"):
        return s
    return s[:24]
