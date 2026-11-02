"""任务队列：内存里排队，磁盘上留产物。

完成的/失败的任务在启动时从 `$DATA/jobs/*/job.json` 读回来（预览、gcode 都还在），
所以重启容器不会丢掉历史，Web UI 的「最近任务」才有意义。
"""
import asyncio
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .config import Settings
from . import gcode as gcode_mod
from .slicer import SliceError, run_slice

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


@dataclass
class Job:
    id: str
    model_name: str
    machine: str
    process: str
    filament: str | None
    overrides: dict[str, Any] = field(default_factory=dict)
    status: str = QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    work_dir: str = ""
    gcode_path: str | None = None
    preview_index: str | None = None
    preview_bin: str | None = None
    preview_status: str = "off"      # off | pending | ready | failed

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("work_dir", None)
        d.pop("preview_index", None)
        d.pop("preview_bin", None)
        d["gcode_url"] = "/jobs/%s/gcode" % self.id if self.gcode_path else None
        d["preview_url"] = "/jobs/%s/preview" % self.id if self.preview_index else None
        if self.started_at and self.finished_at:
            d["duration"] = round(self.finished_at - self.started_at, 2)
        return d


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._running = 0
        # 提交时校验过的 profile 文档，避免 worker 再去读盘
        self._docs: dict[str, dict[str, dict]] = {}

    # ---- 生命周期 ----
    async def start(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings.home.mkdir(parents=True, exist_ok=True)
        self._restore()
        for i in range(self.settings.max_concurrency):
            self._tasks.append(asyncio.create_task(self._worker(i), name="slicer-worker-%d" % i))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    def _restore(self) -> None:
        """把磁盘上跑完的任务读回内存（只读 job.json，不重新解析任何东西）。"""
        root = self.settings.data_dir / "jobs"
        if not root.is_dir():
            return
        for meta in root.glob("*/job.json"):
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if d.get("status") not in (DONE, FAILED):
                continue
            work_dir = meta.parent
            gcode = work_dir / "out" / "plate_1.gcode"
            idx = work_dir / "preview" / "index.json"
            seg = work_dir / "preview" / "segments.bin"
            job = Job(
                id=d.get("id") or work_dir.name,
                model_name=d.get("model_name") or "model",
                machine=d.get("machine") or "",
                process=d.get("process") or "",
                filament=d.get("filament"),
                overrides=d.get("overrides") or {},
                status=d["status"],
                created_at=float(d.get("created_at") or 0),
                started_at=d.get("started_at"),
                finished_at=d.get("finished_at"),
                error=d.get("error"),
                summary=d.get("summary") or {},
                work_dir=str(work_dir),
                gcode_path=str(gcode) if gcode.is_file() else None,
                preview_index=str(idx) if idx.is_file() else None,
                preview_bin=str(seg) if seg.is_file() else None,
                preview_status="ready" if idx.is_file() and seg.is_file() else "off",
            )
            self.jobs[job.id] = job

    # ---- 对外 ----
    def submit(self, model_tmp: Path, model_name: str, machine: str, process: str,
               filament: str | None, docs: dict[str, dict[str, dict]],
               overrides: dict[str, Any]) -> Job:
        job_id = uuid.uuid4().hex[:12]
        work_dir = self.settings.data_dir / "jobs" / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(model_name).suffix.lower() or ".stl"
        model_path = work_dir / ("model" + suffix)
        shutil.move(str(model_tmp), model_path)

        job = Job(id=job_id, model_name=model_name, machine=machine, process=process,
                  filament=filament, overrides=overrides, work_dir=str(work_dir))
        self.jobs[job_id] = job
        self._docs[job_id] = docs
        self._queue.put_nowait(job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def stats(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for j in self.jobs.values():
            counts[j.status] = counts.get(j.status, 0) + 1
        return {"jobs": counts, "queued": self._queue.qsize(),
                "running": self._running, "concurrency": self.settings.max_concurrency}

    # ---- worker ----
    async def _worker(self, idx: int) -> None:
        while True:
            job_id = await self._queue.get()
            job = self.jobs.get(job_id)
            if job is None:
                continue
            self._running += 1
            job.status = RUNNING
            job.started_at = time.time()
            docs = self._docs.pop(job_id, None) or {}
            work_dir = Path(job.work_dir)
            model = next(iter(sorted(work_dir.glob("model.*"))), None)
            try:
                if model is None:
                    raise SliceError("模型文件丢了")
                summary, gcode, _log = await run_slice(
                    self.settings, work_dir, model, docs, job.overrides,
                    self.settings.job_timeout,
                )
                job.summary = summary
                job.gcode_path = str(gcode)
                job.status = DONE
                if self.settings.preview:
                    job.preview_status = "pending"
                    await self.build_preview(job, gcode)
            except SliceError as e:
                job.status = FAILED
                job.error = str(e)
            except Exception as e:  # noqa: BLE001
                job.status = FAILED
                job.error = "%s: %s" % (type(e).__name__, e)
            finally:
                job.finished_at = time.time()
                self._running -= 1
                (work_dir / "job.json").write_text(
                    json.dumps(job.public(), ensure_ascii=False, indent=1), encoding="utf-8")
                self._queue.task_done()

    async def build_preview(self, job: Job, gcode: Path) -> None:
        """切完顺手把 gcode 拆成逐层线段（CPU 活，扔线程里跑，别堵事件循环）。"""
        pdir = Path(job.work_dir) / "preview"
        bin_path = pdir / "segments.bin"
        idx_path = pdir / "index.json"
        try:
            index = await asyncio.to_thread(gcode_mod.parse, gcode, bin_path)
            gcode_mod.write_index(index, idx_path)
        except Exception as e:  # noqa: BLE001 — 预览失败不该让切片算失败
            job.preview_status = "failed"
            job.summary["preview_error"] = "%s: %s" % (type(e).__name__, e)
            return
        job.summary.pop("preview_error", None)
        job.preview_index = str(idx_path)
        job.preview_bin = str(bin_path)
        job.preview_status = "ready"
        job.summary["preview"] = {
            "layers": index["layer_count"],
            "segments": index["segment_count"],
            "bin_bytes": index["bin_bytes"],
            "parse_ms": index["parse_ms"],
        }
