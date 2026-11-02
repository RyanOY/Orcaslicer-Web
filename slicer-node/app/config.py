"""slicer-node 运行时配置（全部走环境变量，容器里好覆盖）。"""
import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    orca_bin: str          # orca-slicer 可执行文件
    data_dir: Path         # 任务工作目录的根
    profile_dir: Path      # machine/ process/ filament/ 三个子目录
    max_concurrency: int   # 同时切几个（RAM 允许的话 2 也行）
    job_timeout: int       # 单个任务墙钟超时（秒）
    job_ttl: int           # 完成的任务保留多久（秒），0=永不清
    home: Path             # orca 的 HOME，避免它往 /root 写
    preview: bool          # 切完顺手生成逐层线段（默认开）
    arrange: str           # --arrange 的取值；"" = 不传该参数（见下方说明）


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("SLICER_DATA_DIR", "/data"))
    return Settings(
        orca_bin=os.environ.get("ORCA_BIN", "/opt/orca/bin/orca-slicer"),
        data_dir=data_dir,
        profile_dir=Path(os.environ.get("SLICER_PROFILE_DIR", "/app/profiles")),
        max_concurrency=max(1, _env_int("SLICER_MAX_CONCURRENCY", 1)),
        job_timeout=_env_int("SLICER_JOB_TIMEOUT", 900),
        job_ttl=_env_int("SLICER_JOB_TTL", 0),
        home=Path(os.environ.get("SLICER_HOME", str(data_dir / "home"))),
        preview=os.environ.get("SLICER_PREVIEW", "1") not in ("0", "false", "False", ""),
        # --arrange 取值：
        #   ""   = 不传该参数，Orca 自己决定（2.4.2 实测唯一能切出 gcode 的模式）
        #   "0"  = 禁用自动摆盘。**实测会失败**：Orca 2.4.2 + 展平 profile 下返回
        #          rc=-50 "Nothing to be sliced / plate is empty"，模型再小也一样。
        #          但 3MF 工程里摆盘位置已烧进三角形坐标时又需要它，故保留可配。
        arrange=os.environ.get("SLICER_ARRANGE", ""),
    )
