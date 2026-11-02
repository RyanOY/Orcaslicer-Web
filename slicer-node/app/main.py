"""slicer-node HTTP 接口。

分层：这一层只做「收模型 + 拼装参数 + 排队切片 + 交还产物」，
再加上 Web UI 需要的配置编辑、摆盘、送机；打印机的安全动作（开始打印）
必须显式 start=true，绝不会自动触发。
"""
from __future__ import annotations

import asyncio
import gzip
import json
import math
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import moonraker
from . import plate as plate_mod
from . import models as model_mod
from . import printer as printer_mod
from . import profiles as prof
from . import spools as spools_mod
from . import usage as usage_mod
from .config import load_settings
from .gcode import TYPE_NAMES
from .jobs import JobManager
from .schema import Schema
from .slicer import SliceError, orca_version

MAX_UPLOAD = 300 * 1024 * 1024  # 300 MB，够 3MF 项目文件了
ALLOWED_SUFFIX = {".stl", ".3mf", ".step", ".stp", ".obj"}

settings = load_settings()
manager = JobManager(settings)
schema = Schema(Path(__file__).parent / "schema" / "params.json")
ledger = spools_mod.Ledger(Path(settings.data_dir) / "spools.json")
VERSION = {"orca": None}
APP_VERSION = "0.11.6"   # 与 FastAPI 的 version、/health 的 version 保持一致
_preview_cache: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.start()
    VERSION["orca"] = orca_version(settings)
    watcher = asyncio.create_task(_spool_watch())
    try:
        yield
    finally:
        watcher.cancel()
        await manager.stop()


async def _spool_watch() -> None:
    """盯着打印状态：一结束就自动把用量记到料盘上（不依赖前端页面开着）。

    只在「账本里真的有料盘」时才轮询，没配料盘的人完全无感。
    每 20 秒一次，比 UI 的 2 秒轮询省得多；状态迁移的判定和前端一致。
    """
    prev: str | None = None
    while True:
        await asyncio.sleep(20)
        try:
            if not ledger.state()["spools"]:
                prev = None
                continue
            live = await asyncio.to_thread(moonraker.live, load_printer())
            cur = live.get("state") or ""
            if prev in ("printing", "paused") and cur in (
                    "complete", "cancelled", "error", "shutdown", "standby", "ready"):
                await asyncio.to_thread(sync_spools_now, 20)
            prev = cur
        except asyncio.CancelledError:
            raise
        except Exception:
            # 打印机离线、网络抖动都正常，别让后台任务挂掉
            continue


app = FastAPI(title="orca slicer node", version=APP_VERSION, lifespan=lifespan)


# ---------------------------------------------------------------- 基础

@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": APP_VERSION,
        "orca": VERSION["orca"] or "unknown",
        "orca_bin": settings.orca_bin,
        "profiles": prof.catalog(settings=settings),
        "queue": manager.stats(),
        "schema_params": len(schema.params),
        "models": len(model_mod.list_models(settings)),
    }


# ---------------------------------------------------------------- 参数schema

@app.get("/schema")
async def get_schema(kind: str | None = None) -> dict[str, Any]:
    if kind is None:
        return {"params": schema.params, "order": schema.order,
                "count": len(schema.params)}
    if kind not in prof.KINDS:
        raise HTTPException(400, "kind 只能是 %s" % "/".join(prof.KINDS))
    return schema.for_kind(kind)


# ---------------------------------------------------------------- profile

@app.get("/profiles")
async def profiles() -> dict[str, Any]:
    return {k: prof.entries(settings, k) for k in prof.KINDS}


@app.get("/profiles/{kind}/{name}")
async def profile_detail(kind: str, name: str) -> dict[str, Any]:
    try:
        doc = prof.load(settings, kind, name)
        _path, source = prof.resolve(settings, kind, name)
    except prof.ProfileError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    described = schema.describe(kind, doc)
    return {"name": name, "kind": kind, "source": source, "doc": doc, **described}


@app.put("/profiles/{kind}/{name}")
async def profile_save(kind: str, name: str, payload: dict = Body(...)) -> dict[str, Any]:
    doc = payload.get("doc") if isinstance(payload.get("doc"), dict) else payload
    try:
        return prof.save(settings, kind, name, doc)
    except prof.ProfileError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/profiles/{kind}/{name}/clone")
async def profile_clone(kind: str, name: str, payload: dict = Body(...)) -> dict[str, Any]:
    to = (payload.get("to") or "").strip()
    if not to:
        raise HTTPException(400, "要给出新名字 to")
    try:
        doc = prof.load(settings, kind, name)
    except prof.ProfileError as e:
        raise HTTPException(404, str(e)) from e
    doc["name"] = to
    doc["from"] = "User"
    try:
        return prof.save(settings, kind, to, doc)
    except prof.ProfileError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/profiles/{kind}/{name}")
async def profile_delete(kind: str, name: str) -> dict[str, Any]:
    try:
        return prof.delete(settings, kind, name)
    except prof.ProfileError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/profiles/{kind}/{name}/export")
async def profile_export(kind: str, name: str):
    try:
        filename, doc = prof.export(settings, kind, name)
    except prof.ProfileError as e:
        raise HTTPException(404, str(e)) from e
    return Response(
        content=json.dumps(doc, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )


@app.post("/profiles/{kind}/import")
async def profile_import(kind: str, file: UploadFile = File(...),
                         name: str | None = Form(None)) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "配置文件太大了")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise HTTPException(400, "不是合法 JSON：%s" % e) from e
    try:
        target, body = prof.import_doc(kind, doc, name or Path(file.filename or "").stem)
        saved = prof.save(settings, kind, target, body)
    except prof.ProfileError as e:
        raise HTTPException(400, str(e)) from e
    described = schema.describe(kind, dict(body, name=target))
    return {"name": target, "saved": saved, **described}


@app.get("/profiles/{kind}/{name}/bed")
async def profile_bed(kind: str, name: str) -> dict[str, Any]:
    try:
        doc = prof.load(settings, kind, name)
    except prof.ProfileError as e:
        raise HTTPException(404, str(e)) from e
    return bed_of(doc)


def bed_of(machine: dict) -> dict[str, Any]:
    """从机器配置里抠出热床尺寸/形状，给摆盘画布用。"""
    area = machine.get("printable_area") or machine.get("bed_shape") or []
    pts: list[tuple[float, float]] = []
    for item in area:
        if isinstance(item, str) and "x" in item.lower():
            a, b = item.lower().split("x")
            try:
                pts.append((float(a), float(b)))
            except ValueError:
                continue
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            pts.append((float(item[0]), float(item[1])))
    if not pts:
        pts = [(0, 0), (200, 0), (200, 200), (0, 200)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w = max(xs) - min(xs)
    d = max(ys) - min(ys)
    circular = False
    if len(pts) >= 8:
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        rs = [math.hypot(x - cx, y - cy) for x, y in pts]
        circular = max(rs) - min(rs) < max(1.0, 0.02 * max(rs))
    max_h = machine.get("printable_height") or machine.get("max_print_height") or 0
    if isinstance(max_h, list):
        max_h = max_h[0] if max_h else 0
    nozzle = machine.get("nozzle_diameter") or [0.4]
    if not isinstance(nozzle, list):
        nozzle = [nozzle]
    return {
        "w": round(w, 2), "d": round(d, 2),
        "x0": round(min(xs), 2), "y0": round(min(ys), 2),
        "circular": circular,
        "h": round(float(max_h), 1),
        "nozzles": nozzle,
        "extruders": int(machine.get("extruder_count") or len(nozzle) or 1),
        "mmu": bool(machine.get("single_extruder_multi_material")),
        "points": [[round(x, 2), round(y, 2)] for x, y in pts],
    }


# ---------------------------------------------------------------- 模型库

@app.get("/models")
async def models_list() -> dict[str, Any]:
    return {"models": model_mod.list_models(settings),
            "dir": str(model_mod.models_dir(settings)),
            "extra_dirs": [str(p) for p in model_mod.extra_dirs(settings) if p.is_dir()]}


@app.post("/models", status_code=201)
async def models_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "文件超过 %d MB" % (MAX_UPLOAD // 1024 // 1024))
    try:
        return model_mod.save_upload(settings, file.filename or "model.stl", raw)
    except model_mod.ModelError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "解析网格失败：%s" % e) from e


@app.get("/models/{name}/info")
async def model_info(name: str, refresh: bool = False) -> dict[str, Any]:
    try:
        return model_mod.info(settings, name, refresh=refresh)
    except model_mod.ModelError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/models/{name}/mesh")
async def model_mesh(name: str, request: Request, part: str | None = None):
    """焊接后的三角形网格（二进制），给前端 3D 摆盘渲染 + 面拾取。

    `?part=<id>` 取 3MF 里某个子对象的网格（局部坐标），不带则整文件合并网格。
    布局：uint32 顶点数 · uint32 面数 · float32×3×顶点 · uint32×3×面（小端）。
    """
    try:
        body = model_mod.mesh_bytes(settings, name, part or None)
    except model_mod.ModelError as e:
        raise HTTPException(404, str(e)) from e
    headers = {"Vary": "Accept-Encoding", "Cache-Control": "public, max-age=86400"}
    if "gzip" in request.headers.get("accept-encoding", "") and len(body) > 2048:
        body = gzip.compress(body, 6)
        headers["Content-Encoding"] = "gzip"
    return Response(content=body, media_type="application/octet-stream", headers=headers)


@app.delete("/models/{name}")
async def model_delete(name: str) -> dict[str, str]:
    try:
        model_mod.delete(settings, name)
    except model_mod.ModelError as e:
        raise HTTPException(400, str(e)) from e
    return {"deleted": name}


# ---------------------------------------------------------------- 摆盘

@app.post("/plate/arrange")
async def plate_arrange(payload: dict = Body(...)) -> dict[str, Any]:
    objects = _with_shrink(payload.get("objects") or [], _shrinks_of(payload))
    bed = payload.get("bed") or {}
    shapes = _shapes(objects)
    meshes = _meshes(objects)
    return {"objects": plate_mod.arrange(objects, shapes, bed, gap=float(payload.get("gap") or 5),
                                         meshes=meshes)}


def _shrinks_of(payload: dict) -> list[tuple[float, float]]:
    """本次切片会让每个槽放大多少（收缩率补偿；100% → 1.0）。"""
    names = payload.get("filaments") or []
    if not names:
        return []
    docs: list[dict] = []
    for n in list(names)[:8]:
        try:
            docs.append(prof.load(settings, "filament", n) if n else {})
        except prof.ProfileError:
            docs.append({})
    return plate_mod.shrink_of(docs, payload.get("overrides") or {})


def _with_shrink(objects: list, shrinks: list[tuple[float, float]]) -> list:
    """把收缩补偿折进 scale（XY），另存 __s / __sz 给高度检查用。

    摆盘检查/自动缩放必须按**切片后的真实尺寸**算，否则填 90% 时检查说放得下、
    切出来却出界。放大是切片时乘到网格上的，所以这里只借 scale 字段做几何。
    """
    if not shrinks:
        return objects
    out = []
    for o in objects:
        if not isinstance(o, dict):
            out.append(o)
            continue
        c = dict(o)
        t = int(o.get("extruder") or 1) - 1
        sx, sz = shrinks[min(max(t, 0), len(shrinks) - 1)]
        s = float(o.get("scale") or 1)
        c["__s"] = s
        c["__sz"] = sz
        c["scale"] = s * sx
        out.append(c)
    return out


@app.post("/plate/fit")
async def plate_fit(payload: dict = Body(...)) -> dict[str, Any]:
    """求最大统一缩放，让所有对象都放进热床（放不下时给用户一个出口）。"""
    objects = _with_shrink(payload.get("objects") or [], _shrinks_of(payload))
    bed = payload.get("bed") or {}
    shapes = _shapes(objects)
    meshes = _meshes(objects)
    return plate_mod.compute_fit(
        objects, shapes, bed,
        gap=float(payload.get("gap") or 5), meshes=meshes)


@app.post("/plate/check")
async def plate_check(payload: dict = Body(...)) -> dict[str, Any]:
    shrinks = _shrinks_of(payload)
    objects = _with_shrink(payload.get("objects") or [], shrinks)
    bed = payload.get("bed") or {}
    shapes = _shapes(objects)
    meshes = _meshes(objects)
    warnings = plate_mod.check_bounds(objects, shapes, bed, meshes=meshes)
    for o in objects:
        if not isinstance(o, dict):
            continue
        sh = shapes.get(_shape_key(o))
        # scale 已被 _with_shrink 乘上 XY 系数，高度得用原始 scale × Z 系数
        if sh and bed.get("h"):
            h = ((sh["bbox"][5] - sh["bbox"][2])
                 * float(o.get("__s") or o.get("scale") or 1)
                 * float(o.get("__sz") or 1))
            if h > bed["h"]:
                warnings.append("%s 高度超过热床可打印高度" % _obj_label(o))
    return {"warnings": warnings}


def _shape_key(o: dict) -> str:
    f = o.get("file") or ""
    p = o.get("part") or ""
    return f if not p else "%s#%s" % (f, p)


def _obj_label(o: dict) -> str:
    return str(o.get("name") or o.get("file") or "")


def _shapes(objects: list[dict]) -> dict[str, dict]:
    """对象 → 几何摘要：整文件 = file，3MF 子对象 = file#part（取该部件的 bbox/hull）。"""
    out: dict[str, dict] = {}
    for o in objects:
        if not isinstance(o, dict):
            continue
        key = _shape_key(o)
        if key in out:
            continue
        f = o.get("file") or ""
        part = o.get("part") or ""
        try:
            info = model_mod.info(settings, f)
            if part:
                sh = next((p for p in info.get("parts", []) if str(p["id"]) == str(part)), None)
                if sh:
                    out[key] = sh
            else:
                out[key] = info
        except model_mod.ModelError:
            continue
    return out


def _identity_m(m) -> bool:
    if not isinstance(m, (list, tuple)) or len(m) != 9:
        return True
    I = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    return all(abs(float(v) - I[i]) < 1e-6 for i, v in enumerate(m))


def _meshes(objects: list[dict]) -> dict[str, Any]:
    """只给带任意朝向（m）的对象加载完整网格；绕 Z 旋转走缓存的 2D 凸包，不碰网格。"""
    out: dict[str, Any] = {}
    for o in objects:
        if not isinstance(o, dict):
            continue
        key = _shape_key(o)
        if key in out or _identity_m(o.get("m")):
            continue
        try:
            out[key] = model_mod.load_mesh(settings, o.get("file") or "",
                                           o.get("part") or None)
        except model_mod.ModelError:
            continue
    return out


@app.post("/plate/slice", status_code=202)
async def plate_slice(payload: dict = Body(...)) -> JSONResponse:
    machine = payload.get("machine") or ""
    process = payload.get("process") or ""
    filaments = payload.get("filaments") or ([payload["filament"]] if payload.get("filament") else [])
    objects = payload.get("objects") or []
    overrides = payload.get("overrides") or {}
    if not machine or not process:
        raise HTTPException(400, "要选机器和工艺配置")
    if not isinstance(overrides, dict):
        raise HTTPException(400, "overrides 必须是对象")

    try:
        docs = {
            "machine": prof.load(settings, "machine", machine),
            "process": prof.load(settings, "process", process),
            "filaments": [prof.load(settings, "filament", f) for f in filaments if f],
        }
    except prof.ProfileError as e:
        raise HTTPException(400, str(e)) from e

    work = Path(tempfile.mkdtemp(prefix="plate-", dir=str(_tmp_dir())))
    try:
        built = plate_mod.build(settings, model_mod, objects, work / "plate.3mf",
                                filaments=docs["filaments"], overrides=overrides)
    except plate_mod.PlateError as e:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(400, "拼装 3MF 失败：%s" % e) from e

    label = "plate(%d)" % len(objects)
    job = manager.submit(work / "plate.3mf", label + ".3mf", machine, process,
                         ",".join(filaments) or None, docs, overrides)
    job.summary["plate"] = {"objects": len(objects), "bytes": built["bytes"],
                            "extruders": built["extruders_used"]}
    shutil.rmtree(work, ignore_errors=True)
    return JSONResponse(status_code=202,
                        content={"job_id": job.id, "status": job.status,
                                 "poll": "/jobs/%s" % job.id, "plate": built})


def _tmp_dir() -> Path:
    d = Path(settings.data_dir) / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 切片（单文件，兼容旧接口）

@app.post("/slice", status_code=202)
async def slice_model(
    file: UploadFile = File(...),
    machine: str = Form(...),
    process: str = Form(...),
    filament: str | None = Form(None),
    overrides: str | None = Form(None),
) -> JSONResponse:
    suffix = Path(file.filename or "model.stl").suffix.lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(400, "不支持的模型格式 %r，只收 %s" %
                            (suffix, ", ".join(sorted(ALLOWED_SUFFIX))))

    try:
        docs = {
            "machine": prof.load(settings, "machine", machine),
            "process": prof.load(settings, "process", process),
            "filament": prof.load(settings, "filament", filament) if filament else None,
        }
    except prof.ProfileError as e:
        raise HTTPException(400, str(e)) from e

    ov: dict[str, Any] = {}
    if overrides:
        try:
            parsed = json.loads(overrides)
        except ValueError as e:
            raise HTTPException(400, "overrides 不是合法 JSON: %s" % e) from e
        if not isinstance(parsed, dict):
            raise HTTPException(400, "overrides 必须是对象")
        ov = parsed

    work = Path(tempfile.mkdtemp(prefix="up-", dir=str(_tmp_dir())))
    model_path = work / ("model" + suffix)
    written = 0
    try:
        with open(model_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD:
                    raise HTTPException(413, "文件超过 %d MB" % (MAX_UPLOAD // 1024 // 1024))
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(work, ignore_errors=True)
        raise

    name = file.filename or ("model" + suffix)
    job = manager.submit(model_path, name, machine, process, filament, docs, ov)
    shutil.rmtree(work, ignore_errors=True)
    return JSONResponse(status_code=202, content={"job_id": job.id, "status": job.status,
                                                  "poll": "/jobs/%s" % job.id})


# ---------------------------------------------------------------- 任务

@app.get("/jobs")
async def jobs_list(limit: int = 30) -> dict[str, Any]:
    items = sorted(manager.jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]
    return {"jobs": [j.public() for j in items], "stats": manager.stats()}


@app.get("/jobs/{job_id}")
async def job_detail(job_id: str) -> dict[str, Any]:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    return job.public()


@app.get("/jobs/{job_id}/gcode")
async def job_gcode(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    if not job.gcode_path or not Path(job.gcode_path).is_file():
        raise HTTPException(409, "任务状态=%s，还没有 gcode" % job.status)
    return FileResponse(job.gcode_path, media_type="text/plain",
                        filename="%s.gcode" % Path(job.model_name).stem)


@app.get("/jobs/{job_id}/log")
async def job_log(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    p = Path(job.work_dir) / "orca.log"
    if not p.is_file():
        return {"log": ""}
    text = p.read_text(encoding="utf-8", errors="replace")
    return {"log": text[-20000:]}


@app.get("/jobs/{job_id}/preview")
async def job_preview(job_id: str, request: Request):
    """层表（层高、段数、字节偏移、每类区间、包围盒）。>1KB 时按需 gzip。"""
    index = _preview_index(job_id)
    body = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {"Vary": "Accept-Encoding", "Cache-Control": "public, max-age=3600"}
    if "gzip" in request.headers.get("accept-encoding", "") and len(body) > 1024:
        body = gzip.compress(body, 6)
        headers["Content-Encoding"] = "gzip"
    return Response(content=body, media_type="application/json", headers=headers)


@app.get("/jobs/{job_id}/preview/bin")
async def job_preview_bin(job_id: str, request: Request):
    """整个二进制流，支持 Range —— 大模型可以边下边画，也可以只取某几层。

    每层在文件里是连续的，所以按层取字节 = 用 index 里的 offset 发 Range 请求，
    不需要为「取 30 层」单独造一个批量接口。
    """
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    if not job.preview_bin or not Path(job.preview_bin).is_file():
        raise HTTPException(409, "任务状态=%s，还没有预览" % job.status)
    path = Path(job.preview_bin)
    size = path.stat().st_size
    rng = request.headers.get("range")
    if rng and rng.startswith("bytes="):
        spec = rng[6:].split(",")[0]
        first, _, last = spec.partition("-")
        try:
            start = int(first) if first else 0
            end = int(last) if last else size - 1
        except ValueError:
            raise HTTPException(416, "Range 解析失败: %r" % rng) from None
        start = max(0, min(start, size - 1))
        end = max(start, min(end, size - 1))
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        return Response(
            content=data, status_code=206, media_type="application/octet-stream",
            headers={
                "Content-Range": "bytes %d-%d/%d" % (start, end, size),
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(data)),
                "Cache-Control": "public, max-age=86400",
            },
        )
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Accept-Ranges": "bytes",
                                 "Cache-Control": "public, max-age=86400"})


@app.get("/jobs/{job_id}/preview/{layer:int}")
async def job_preview_layer(job_id: str, layer: int, types: str | None = None):
    """单层二进制：[Float32 x4 x n] 位置 + [Uint8 x n] 类型 + [Uint8 x n] 挤出截面
    + [Uint16 x n] 属性下标（v5 起，每段 20 字节）。

    `?types=0,1,3` 只取这些类型。因为构建时已按类型排序，
    服务端只需几次 seek，前端拿到的仍是标准布局。
    """
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    index = _preview_index(job_id)
    layers = index["layers"]
    if layer < 0 or layer >= len(layers):
        raise HTTPException(404, "层号越界，共 %d 层" % len(layers))
    ly = layers[layer]
    if not ly["count"]:
        return Response(content=b"", media_type="application/octet-stream",
                        headers={"X-Segments": "0", "X-Z": str(ly["z"])})

    wanted = _parse_types(types)
    data, count = _read_layer(Path(job.preview_bin), ly, wanted,
                              int(index.get("version") or 0))
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-Segments": str(count),
            "X-Pos-Bytes": str(count * 16),
            "X-Z": str(ly["z"]),
            "X-Types": ",".join(str(t) for t in sorted(wanted)) if wanted else "all",
            "Cache-Control": "public, max-age=86400",
        },
    )


@app.post("/jobs/{job_id}/preview")
async def job_preview_rebuild(job_id: str):
    """手动（重）建预览：预览关闭、构建失败、或想换参数时用。"""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    if not job.gcode_path or not Path(job.gcode_path).is_file():
        raise HTTPException(409, "任务状态=%s，还没有 gcode" % job.status)
    job.preview_status = "pending"
    await manager.build_preview(job, Path(job.gcode_path))
    if job.summary.get("preview_error"):
        raise HTTPException(500, "预览构建失败: %s" % job.summary["preview_error"])
    _preview_cache.pop(job_id, None)
    return _preview_index(job_id)


@app.delete("/jobs/{job_id}")
async def job_delete(job_id: str) -> dict[str, str]:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    if job.status == "running":
        raise HTTPException(409, "正在跑，先别删")
    shutil.rmtree(job.work_dir, ignore_errors=True)
    manager.jobs.pop(job_id, None)
    _preview_cache.pop(job_id, None)
    return {"deleted": job_id}


# ---------------------------------------------------------------- 打印机

def _printer_cfg_path() -> Path:
    return Path(settings.data_dir) / "printers.json"


def load_printer() -> dict[str, Any]:
    p = _printer_cfg_path()
    if p.is_file():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                return {**printer_mod.DEFAULT, **cfg}
        except (OSError, ValueError):
            pass
    return dict(printer_mod.DEFAULT)


@app.get("/printer")
async def printer_get() -> dict[str, Any]:
    cfg = load_printer()
    has_key = bool(cfg.get("api_key"))
    return {**cfg, "api_key": "***" if has_key else "", "configured": has_key or bool(cfg.get("url"))}


@app.put("/printer")
async def printer_put(payload: dict = Body(...)) -> dict[str, Any]:
    cfg = load_printer()
    for key in ("url", "name", "upload_dir"):
        if key in payload:
            cfg[key] = str(payload[key]).strip()
    if payload.get("api_key") not in (None, "", "***"):
        cfg["api_key"] = str(payload["api_key"]).strip()
    _printer_cfg_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return {**cfg, "api_key": "***" if cfg.get("api_key") else ""}


@app.post("/printer/test")
async def printer_test() -> dict[str, Any]:
    try:
        return printer_mod.ping(load_printer())
    except printer_mod.PrinterError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/jobs/{job_id}/print")
async def job_print(job_id: str, payload: dict = Body(default={})) -> dict[str, Any]:
    """上传 gcode 到打印机。start=True 时才会真的开始打印（需要二次确认）。"""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    if job.status != "done" or not job.gcode_path or not Path(job.gcode_path).is_file():
        raise HTTPException(409, "任务还没有可用的 gcode（状态=%s）" % job.status)
    cfg = load_printer()
    remote_name = payload.get("filename") or (
        "%s-%s.gcode" % (Path(job.model_name).stem, job.id))
    try:
        up = printer_mod.upload(cfg, Path(job.gcode_path), remote_name)
        result: dict[str, Any] = {"uploaded": up, "started": False}
        if payload.get("start"):
            result["start"] = printer_mod.start(cfg, up["path"])
            result["started"] = True
            # 记下「正在打印哪个任务」，打印结束后对账用（见 /spools/sync）
            ledger.set_pending(job.id, up["path"])
        return result
    except printer_mod.PrinterError as e:
        raise HTTPException(502, str(e)) from e


# ---------------------------------------------------------------- 打印机实时控制

@app.get("/printer/live")
async def printer_live() -> dict[str, Any]:
    try:
        return moonraker.live(load_printer())
    except printer_mod.PrinterError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/printer/gcode")
async def printer_gcode(payload: dict = Body(...)) -> dict[str, Any]:
    script = str(payload.get("script") or "")
    try:
        res = moonraker.gcode(load_printer(), script)
        return {"ok": True, "result": res}
    except printer_mod.PrinterError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/printer/temps")
async def printer_temps(payload: dict = Body(...)) -> dict[str, Any]:
    bed = payload.get("bed")
    extruders = payload.get("extruders")
    if extruders is not None and not isinstance(extruders, list):
        raise HTTPException(400, "extruders 必须是数组")
    try:
        res = moonraker.set_temps(
            load_printer(),
            bed=None if bed is None else float(bed),
            extruders=[None if v is None else float(v) for v in (extruders or [])],
        )
        return {"ok": True, "result": res}
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "温度必须是数字") from e
    except printer_mod.PrinterError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/printer/jog")
async def printer_jog(payload: dict = Body(...)) -> dict[str, Any]:
    try:
        res = moonraker.jog(load_printer(), str(payload.get("axis") or ""),
                            float(payload.get("delta") or 0),
                            float(payload.get("feedrate") or 3000))
        return {"ok": True, "result": res}
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "移动距离/速度必须是数字") from e
    except printer_mod.PrinterError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/printer/action")
async def printer_action(payload: dict = Body(...)) -> dict[str, Any]:
    name = str(payload.get("action") or "")
    try:
        return {"ok": True, "result": moonraker.action(load_printer(), name)}
    except printer_mod.PrinterError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/printer/history")
async def printer_history(limit: int = 10) -> dict[str, Any]:
    try:
        return {"jobs": moonraker.history(load_printer(), max(1, min(limit, 100)))}
    except printer_mod.PrinterError as e:
        raise HTTPException(502, str(e)) from e


# ---------------------------------------------------------------- 料盘 / 槽位

@app.get("/spools")
async def spools_state() -> dict[str, Any]:
    return ledger.state()


@app.post("/spools")
async def spool_create(payload: dict = Body(...)) -> dict[str, Any]:
    try:
        return ledger.create(payload)
    except spools_mod.SpoolError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/spools/{spool_id}")
async def spool_update(spool_id: str, payload: dict = Body(...)) -> dict[str, Any]:
    try:
        return ledger.update(spool_id, payload)
    except spools_mod.SpoolError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/spools/{spool_id}")
async def spool_delete(spool_id: str) -> dict[str, Any]:
    try:
        return ledger.delete(spool_id)
    except spools_mod.SpoolError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/spools/{spool_id}/assign")
async def spool_assign(spool_id: str, payload: dict = Body(...)) -> dict[str, Any]:
    slot = payload.get("slot")
    try:
        slot_i = None if slot in (None, "", -1) else int(slot)
    except (TypeError, ValueError):
        raise HTTPException(400, "槽位必须是整数") from None
    try:
        return ledger.assign(spool_id, slot_i)
    except spools_mod.SpoolError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/spools/{spool_id}/adjust")
async def spool_adjust(spool_id: str, payload: dict = Body(...)) -> dict[str, Any]:
    try:
        return ledger.adjust(spool_id, payload)
    except spools_mod.SpoolError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/jobs/{job_id}/usage")
async def job_usage(job_id: str) -> dict[str, Any]:
    """这个任务的 gcode 用量（按工具头），以及会扣到哪些料盘上。"""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    if not job.gcode_path or not Path(job.gcode_path).is_file():
        raise HTTPException(409, "任务还没有 gcode（状态=%s）" % job.status)
    data = usage_mod.scan(job.gcode_path)
    state = ledger.state()
    mapping = {}
    for tool, metres in data["per_tool_m"].items():
        slot = state["slots"][int(tool)] if int(tool) < len(state["slots"]) else None
        spool = (slot or {}).get("spool")
        mapping[tool] = {"slot": int(tool), "m": metres,
                         "spool": (spool or {}).get("name"),
                         "remaining_m": (spool or {}).get("remaining_m")}
    data["slots"] = mapping
    return data


@app.post("/spools/record")
async def spool_record(payload: dict = Body(...)) -> dict[str, Any]:
    """手工把某个任务的用量记进账本（不依赖 Moonraker）。"""
    job_id = str(payload.get("job_id") or "")
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    if not job.gcode_path or not Path(job.gcode_path).is_file():
        raise HTTPException(409, "任务没有 gcode")
    data = usage_mod.scan(job.gcode_path)
    rec = ledger.record(job_id=job_id, filename=Path(job.gcode_path).name,
                        per_tool_m=data["per_tool_m"], total_m=data["total_m"],
                        source="gcode", note=str(payload.get("note") or "手动记账"))
    return {"record": rec}


@app.post("/spools/sync")
async def spools_sync(payload: dict = Body(default=None)) -> dict[str, Any]:
    """拉 Moonraker 打印历史，把还没记账的打印补进账本。

    有本地 gcode 时用我们自己扫的「按工具头」比例去分摊 Moonraker 的实际用量；
    没有本地文件时整包记到当前槽位的料盘上（total 从 history 来）。
    """
    limit = int((payload or {}).get("limit") or 20)
    try:
        return await asyncio.to_thread(sync_spools_now, limit)
    except printer_mod.PrinterError as e:
        raise HTTPException(502, str(e)) from e

def sync_spools_now(limit: int = 20) -> dict[str, Any]:
    """同步实现（刻意写成同步函数：既能丢线程里跑，也能被后台任务直接调）。"""
    cfg = load_printer()
    jobs = moonraker.history(cfg, max(1, min(int(limit), 100)))
    done = {r.get("mr_job") for r in ledger.read().get("records", []) if r.get("mr_job")}
    added: list[dict[str, Any]] = []
    for j in reversed(jobs):                      # 老的先记，记录顺序才自然
        mr_id = j.get("job_id")
        if not mr_id or mr_id in done:
            continue
        filename = j.get("filename") or ""
        used_mm = float(j.get("filament_used_mm") or 0)
        job_id = _job_id_from_filename(filename)
        local = manager.get(job_id) if job_id else None
        per_tool: dict[str, float] = {}
        used_m = used_mm / 1000.0
        source = "moonraker"
        if local is not None and local.gcode_path and Path(local.gcode_path).is_file():
            scan = usage_mod.scan(local.gcode_path)
            scan_total = scan["total_m"]
            if scan_total > 0:
                share = (used_m / scan_total) if used_m > 0 else 1.0
                per_tool = {k: round(v * share, 3) for k, v in scan["per_tool_m"].items()}
                source = "moonraker+gcode"
            if used_m <= 0:
                used_m = scan_total
        if used_m <= 0:
            continue
        if not per_tool:
            state = ledger.state()
            cur = moonraker.live(cfg).get("ace", {}).get("current") or 0
            cur = cur if isinstance(cur, int) else 0
            spool = (state["slots"][cur] or {}).get("spool") if cur < len(state["slots"]) else None
            if not spool:
                continue
            per_tool = {str(cur): round(used_m, 3)}
        rec = ledger.record(job_id=job_id, filename=filename, per_tool_m=per_tool,
                            total_m=used_m, source=source,
                            note="Moonraker %s" % (j.get("status") or ""),
                            extra={"mr_job": mr_id, "mr_status": j.get("status"),
                                   "start_time": j.get("start_time")})
        added.append(rec)
        done.add(mr_id)
    return {"added": added, "count": len(added), "checked": len(jobs)}


def _job_id_from_filename(filename: str) -> str | None:
    """我们上传的名字是 `<model>-<jobid>.gcode`，靠它把打印记录接回本地任务。"""
    m = re.search(r"-([0-9a-f]{12})\.gcode$", filename or "")
    return m.group(1) if m else None



# ---------------------------------------------------------------- 辅助

def _parse_types(types: str | None) -> set[int] | None:
    if not types or types == "all":
        return None
    out: set[int] = set()
    for part in types.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            t = int(part)
        except ValueError:
            raise HTTPException(400, "types 只能是逗号分隔的整数: %r" % types) from None
        if t < 0 or t >= len(TYPE_NAMES):
            raise HTTPException(400, "类型码越界: %d" % t)
        out.add(t)
    return out or None


def _pack_interleaved(pos_b: bytes, type_b: bytes, area_b: bytes,
                      attr_b: bytes, n: int) -> bytes:
    """把「平面分组」的记录重排成每段 20 字节交错（v6 客户端只认这个布局）。

    v6 之前每层在文件里是 [16n 位置][n 类型][n 截面][2n 属性] 四段分开存的，
    所以不能直接按 20 字节跨段读。
    """
    if n <= 0:
        return b""
    rec = bytearray(20 * n)
    view = memoryview(rec).cast("f")
    pv = memoryview(pos_b).cast("f")
    for k in range(4):
        view[k::5] = pv[k::4]
    rec[16::20] = type_b
    rec[17::20] = area_b
    rec[18::20] = attr_b[0::2]
    rec[19::20] = attr_b[1::2]
    return bytes(rec)


def _read_layer(bin_path: Path, ly: dict, wanted: set[int] | None,
                version: int = 4) -> tuple[bytes, int]:
    """返回 (按段交错的记录流, 段数)。

    v7 起每段 24 字节：16 位置 + 1 类型 + 1 挤出截面 + 2 属性下标
    + 2 真实速度 + 2 真实加速度；v6 是 20 字节（没有后两列）。
    v5 及更早是平面分组（位置/类型/截面/属性各自连续），这里统一转成
    交错布局再返回，客户端只有一条读取路径。
    version 4 → 18 字节（无属性），version < 4 → 17 字节（无截面）。
    """
    n = ly["count"]
    if version >= 6:
        stride = 24 if version >= 7 else 20
        if wanted is None:
            with open(bin_path, "rb") as f:
                f.seek(ly["offset"])
                return f.read(n * stride), n
        ranges = ly.get("ranges") or {}
        out = bytearray()
        count = 0
        with open(bin_path, "rb") as f:
            for t in sorted(wanted):
                r = ranges.get(str(t))
                if not r:
                    continue
                start, cnt = r[0], r[1]
                f.seek(ly["offset"] + start * stride)
                out += f.read(cnt * stride)
                count += cnt
        return bytes(out), count

    stride = 20 if version >= 5 else (18 if version >= 4 else 17)
    ranges = ly.get("ranges") or {}
    with open(bin_path, "rb") as f:
        if wanted is None:
            f.seek(ly["offset"])
            raw = f.read(n * stride)
            pos_b = raw[:n * 16]
            type_b = raw[n * 16:n * 17]
            area_b = raw[n * 17:n * 18] if version >= 4 else bytes(n)
            attr_b = raw[n * 18:n * 20] if version >= 5 else bytes(2 * n)
            return _pack_interleaved(pos_b, type_b, area_b, attr_b, n), n

    pos_parts: list[bytes] = []
    type_parts: list[bytes] = []
    area_parts: list[bytes] = []
    attr_parts: list[bytes] = []
    count = 0
    with open(bin_path, "rb") as f:
        for t in sorted(wanted):
            r = ranges.get(str(t))
            if not r:
                continue
            start, cnt = r[0], r[1]
            if cnt <= 0:
                continue
            f.seek(ly["offset"] + start * 16)
            pos_parts.append(f.read(cnt * 16))
            f.seek(ly["offset"] + n * 16 + start)
            type_parts.append(f.read(cnt))
            if version >= 4:
                f.seek(ly["offset"] + n * 17 + start)
                area_parts.append(f.read(cnt))
            else:
                area_parts.append(bytes(cnt))
            if version >= 5:
                f.seek(ly["offset"] + n * 18 + start * 2)
                attr_parts.append(f.read(cnt * 2))
            else:
                attr_parts.append(bytes(cnt * 2))
            count += cnt
    if count == 0:
        return b"", 0
    return _pack_interleaved(b"".join(pos_parts), b"".join(type_parts),
                             b"".join(area_parts), b"".join(attr_parts), count), count


def _preview_index(job_id: str) -> dict[str, Any]:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个任务")
    if not job.preview_index or not Path(job.preview_index).is_file():
        raise HTTPException(409, "任务状态=%s，还没有预览（预览可能被关掉了）" % job.status)
    cached = _preview_cache.get(job_id)
    if cached is None:
        cached = json.loads(Path(job.preview_index).read_text(encoding="utf-8"))
        if len(_preview_cache) > 32:
            _preview_cache.clear()
        _preview_cache[job_id] = cached
    return cached


@app.exception_handler(SliceError)
async def slice_error_handler(_request, exc: SliceError):  # pragma: no cover
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# 静态页面：/ 是 Web UI，/viewer/ 是切片结果预览（可单独打开）
_web_dir = Path("/app/web")
_viewer_dir = Path("/app/viewer")
if _viewer_dir.is_dir():
    app.mount("/viewer", StaticFiles(directory=str(_viewer_dir), html=True), name="viewer")
if _web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="web")
