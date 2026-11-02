"""把 gcode 送到打印机：Moonraker（Klipper）HTTP API。

只做三件事：探活、传文件、开始打印。**不会**自动开始打印 —— 必须前端用户
点了「开始打印」并确认，接口才调 /printer/print/start。
当前打印机是 Voron 2.4 + Klipper（Moonraker 在 192.168.71.33:7125）。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DEFAULT = {"url": "http://192.168.71.33:7125", "api_key": "", "name": "Voron 2.4",
           "upload_dir": "gcodes"}


class PrinterError(Exception):
    pass


def _req(cfg: dict, path: str, method: str = "GET", body: dict | None = None,
         timeout: float = 8.0) -> Any:
    base = (cfg.get("url") or "").rstrip("/")
    if not base:
        raise PrinterError("还没配置打印机地址")
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if cfg.get("api_key"):
        headers["X-Api-Key"] = cfg["api_key"]
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise PrinterError("打印机返回 %s：%s" % (e.code, detail)) from e
    except urllib.error.URLError as e:
        raise PrinterError("连不上打印机 %s：%s" % (base, e.reason)) from e
    except TimeoutError as e:
        raise PrinterError("连打印机超时（%ss）" % timeout) from e
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw[:400].decode("utf-8", "replace")}


def ping(cfg: dict) -> dict[str, Any]:
    info = _req(cfg, "/printer/info")
    state = info.get("result", {}).get("state") or info.get("state")
    version = (info.get("result") or {}).get("software_version")
    out: dict[str, Any] = {"ok": True, "state": state, "software": version}
    try:
        st = _req(cfg, "/printer/objects/query?print_stats&toolhead&heater_bed&extruder")
        res = (st.get("result") or {}).get("status") or {}
        ps = res.get("print_stats") or {}
        out.update({
            "print_state": ps.get("state"),
            "filename": ps.get("filename"),
            "progress": round(float(ps.get("progress") or 0) * 100, 1),
            "nozzle": round(float((res.get("extruder") or {}).get("temperature") or 0), 1),
            "bed": round(float((res.get("heater_bed") or {}).get("temperature") or 0), 1),
            "homed": bool((res.get("toolhead") or {}).get("homed_axes")),
        })
    except PrinterError:
        pass
    return out


def upload(cfg: dict, gcode: Path, filename: str | None = None) -> dict[str, Any]:
    """multipart/form-data 上传到 /server/files/upload。"""
    name = filename or gcode.name
    boundary = "----orcaweb" + uuid.uuid4().hex
    head = (
        "--%s\r\n"
        'Content-Disposition: form-data; name="root"\r\n\r\n'
        "%s\r\n"
        "--%s\r\n"
        'Content-Disposition: form-data; name="file"; filename="%s"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
        % (boundary, cfg.get("upload_dir") or "gcodes", boundary, name)
    ).encode()
    tail = ("\r\n--%s--\r\n" % boundary).encode()
    base = (cfg.get("url") or "").rstrip("/")
    headers = {"Content-Type": "multipart/form-data; boundary=%s" % boundary}
    if cfg.get("api_key"):
        headers["X-Api-Key"] = cfg["api_key"]

    with open(gcode, "rb") as f:
        payload = head + f.read() + tail
    req = urllib.request.Request(base + "/server/files/upload", data=payload,
                                headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise PrinterError("上传失败 %s：%s" % (e.code, e.read()[:200].decode("utf-8", "replace"))) from e
    except urllib.error.URLError as e:
        raise PrinterError("连不上打印机：%s" % e.reason) from e
    item = body.get("result") or body
    return {"ok": True, "path": item.get("path") or name,
            "size": item.get("size"), "modified": item.get("modified")}


def start(cfg: dict, remote_path: str) -> dict[str, Any]:
    return _req(cfg, "/printer/print/start?filename=" + urllib.parse.quote(remote_path),
                method="POST")


def status(cfg: dict) -> dict[str, Any]:
    return _req(cfg, "/printer/objects/query?print_stats&virtual_sdcard")
