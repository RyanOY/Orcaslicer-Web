"""调 orca-slicer 的那一层：命令拼装、执行、产物解析。

CLI 实测要点（2.4.2 / aarch64）：
- 必须用 `--slice 0 --outputdir DIR`，产物是 DIR/plate_1.gcode + DIR/result.json
- `--export-3mf` 会段错误，别碰
- LC_ALL=C，否则某些 locale 下会崩
- 不需要 DISPLAY（headless 原生可跑）
"""
import asyncio
import subprocess
import json
import os
import time
from pathlib import Path
from typing import Any

from .config import Settings

LAYER_MARKER = b";LAYER_CHANGE"


class SliceError(Exception):
    pass


def build_argv(settings: Settings, model: Path, outdir: Path,
               machine_json: Path, process_json: Path,
               filament_json: Path | list[Path] | None,
               logfile: Path) -> list[str]:
    argv = [
        settings.orca_bin,
        str(model),
        "--slice", "0",
        # 关掉自动排版：我们的 3MF 里三角形已经是最终坐标（摆盘的摆放结果），
        # 默认 auto 会让 Orca 重新居中/排布，摆盘位置全部失效。
        "--arrange", "0",
        "--load-settings", "%s;%s" % (process_json, machine_json),
    ]
    if filament_json:
        # 多耗材/多工具头：--load-filaments 收分号分隔的列表，顺序 = 槽位号
        if isinstance(filament_json, (list, tuple)):
            argv += ["--load-filaments", ";".join(str(p) for p in filament_json)]
        else:
            argv += ["--load-filaments", str(filament_json)]
    argv += ["--outputdir", str(outdir), "--logfile", str(logfile)]
    return argv


_SCHEMA_PARAMS: dict[str, dict] | None = None


def _schema_params() -> dict[str, dict]:
    """惰性读一次 schema/params.json（774 个参数的 key→定义，含 kinds/t）。"""
    global _SCHEMA_PARAMS
    if _SCHEMA_PARAMS is None:
        from .schema import Schema
        p = Path(__file__).parent / "schema" / "params.json"
        _SCHEMA_PARAMS = Schema(p).params if p.is_file() else {}
    return _SCHEMA_PARAMS


def _num_str(v: Any) -> str:
    """数字 → 干净字符串（Orca 的 Config 内部全是字符串，标量写 number 会被忽略）。"""
    if isinstance(v, bool):
        return "1" if v else "0"
    f = float(v)
    if f.is_integer():
        return str(int(f))
    return repr(f)


def _coerce(t: str | None, value: Any) -> Any:
    """把 override 值转成 OrcaSlicer 配置文件里该有的形态。

    实测（2.4.2）：profile 的 JSON 标量一律是字符串、向量一律是字符串列表；
    写原生 number/bool 会被静默忽略（layer_height=0.28 无效，写 "0.28" 才生效）。
    所以这里统一字符串化——前端按 schema 送类型正确的值，此处只做格式转换。
    """
    if value is None:
        return None
    t = t or ""
    if t == "bool":
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, str):
            return "1" if value.strip().lower() in ("1", "true", "yes", "on", "y") else "0"
        return "1" if value else "0"
    if t == "int":
        return str(int(float(value)))
    if t == "float":
        return _num_str(value)
    if t == "percent":
        if isinstance(value, str):
            return value if value.endswith("%") else value + "%"
        return _num_str(value) + "%"
    if t == "float_or_percent":
        return value if isinstance(value, str) else _num_str(value)
    # 向量类：统一成字符串列表
    if t == "floats":
        vals = value if isinstance(value, (list, tuple)) else [value]
        return [_num_str(x) for x in vals]
    if t == "ints":
        vals = value if isinstance(value, (list, tuple)) else [value]
        return [str(int(float(x))) for x in vals]
    if t == "percents":
        vals = value if isinstance(value, (list, tuple)) else [value]
        return [_coerce("percent", x) for x in vals]
    if t in ("strings", "enums"):
        return [str(x) for x in value] if isinstance(value, (list, tuple)) else [str(value)]
    if t == "bools":
        vals = value if isinstance(value, (list, tuple)) else [value]
        return [_coerce("bool", x) for x in vals]
    # string / enum / point(s) / pointsgroups / 未知类型：原样返回
    return value


def _apply_overrides(machine_doc: dict, process_doc: dict,
                     filaments: list[dict], overrides: dict[str, Any]) -> None:
    """把 overrides 按 schema kinds 路由进 machine/process/filament 文档（就地改）。

    Orca 2.4.2 的 CLI 不接受任意 `--配置项=值`（会报 "Invalid option" 然后 exit 254），
    所以覆盖项不能再拼命令行参数，改成写进 --load-settings 的 JSON。
    """
    if not overrides:
        return
    params = _schema_params()
    for key, value in overrides.items():
        meta = params.get(key) or {}
        kinds = meta.get("kinds") or []
        v = _coerce(meta.get("t"), value)
        # 前缀兜底：machine_/filament_ 命名的 key 直接按名字定归属
        if key.startswith("machine_"):
            machine_doc[key] = v
        elif key.startswith("filament_"):
            for fd in filaments:
                fd[key] = v
        elif kinds and "machine" in kinds and "process" not in kinds and "filament" not in kinds:
            machine_doc[key] = v
        elif kinds and "filament" in kinds and "process" not in kinds and "machine" not in kinds:
            for fd in filaments:
                fd[key] = v
        else:
            process_doc[key] = v


def _count_layers(gcode: Path) -> int:
    n = 0
    with open(gcode, "rb") as f:
        for line in f:
            if line.startswith(LAYER_MARKER):
                n += 1
    return n


def _read_result(outdir: Path) -> dict[str, Any]:
    p = outdir / "result.json"
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _summarize(outdir: Path) -> dict[str, Any]:
    gcode = outdir / "plate_1.gcode"
    meta = _read_result(outdir)
    plates = meta.get("sliced_plates") or []
    plate = plates[0] if plates else {}
    info: dict[str, Any] = {
        "return_code": meta.get("return_code"),
        "error_string": (meta.get("error_string") or "").strip() or None,
        "layer_height": meta.get("layer_height"),
        "sliced_time": plate.get("sliced_time"),
        "triangle_count": plate.get("triangle_count"),
        "warning_message": plate.get("warning_message") or None,
        "gcode_bytes": None,
        "layers": None,
    }
    if gcode.is_file():
        info["gcode_bytes"] = gcode.stat().st_size
        info["layers"] = _count_layers(gcode)
    return info


def _flake(v: Any) -> Any:
    """把文档里所有标量/数组元素统一成 Orca 认的形态：标量 → 字符串，数组 → 字符串数组。

    实测（2.4.2）：`load_from_json: ... invalid json array for cool_plate_temp`
    就是因为 profile 里写了 `[48]`（数字）而不是 `["48"]`。用户导入的 profile
    经常带原生数字/布尔，所以写盘前必须整体过一遍。
    """
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return _num_str(v)
    if isinstance(v, (list, tuple)):
        return [_flake(x) for x in v]
    if isinstance(v, dict):
        return {k: _flake(x) for k, x in v.items()}
    if v is None:
        return ""
    return v


async def run_slice(settings: Settings, work_dir: Path, model: Path,
                    docs: dict[str, dict], overrides: dict[str, Any],
                    timeout: int) -> tuple[dict[str, Any], Path | None, Path]:
    """跑一次切片。返回 (summary, gcode_path|None, logfile_path)。"""
    outdir = work_dir / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    machine_json = work_dir / "machine.json"
    process_json = work_dir / "process.json"

    # 覆盖项在这里合并进 JSON，不再走命令行参数（见 _apply_overrides 说明）。
    machine_doc = dict(docs["machine"])
    process_doc = dict(docs["process"])
    filaments = docs.get("filaments") or ([docs["filament"]] if docs.get("filament") else [])
    filaments = [dict(fd) for fd in filaments if fd]
    _apply_overrides(machine_doc, process_doc, filaments, overrides)

    machine_json.write_text(json.dumps(_flake(machine_doc), ensure_ascii=False), encoding="utf-8")
    process_json.write_text(json.dumps(_flake(process_doc), ensure_ascii=False), encoding="utf-8")
    filament_json: Path | list[Path] | None = None
    if filaments:
        paths: list[Path] = []
        for i, fdoc in enumerate(filaments, start=1):
            fp = work_dir / ("filament_%d.json" % i)
            fp.write_text(json.dumps(_flake(fdoc), ensure_ascii=False), encoding="utf-8")
            paths.append(fp)
        filament_json = paths[0] if len(paths) == 1 else paths

    logfile = work_dir / "orca.log"
    argv = build_argv(settings, model, outdir, machine_json, process_json,
                      filament_json, logfile)

    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(settings.home),
        "LC_ALL": "C",
        "LANG": "C",
        "APPDIR": "/opt/orca",
        "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin",
        "TMPDIR": str(work_dir),
    }

    started = time.time()
    with open(logfile, "ab") as log_fp:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=log_fp, stderr=asyncio.subprocess.STDOUT, cwd=str(work_dir), env=env,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise SliceError("超时（%ss）" % timeout)

    elapsed = round(time.time() - started, 2)
    summary = _summarize(outdir)
    summary["wall_seconds"] = elapsed
    summary["exit_code"] = proc.returncode

    gcode = outdir / "plate_1.gcode"
    ok = (proc.returncode == 0 and summary.get("return_code") == 0 and gcode.is_file())
    if not ok:
        raise SliceError(
            "切片失败 exit=%s rc=%s %s | %s"
            % (proc.returncode, summary.get("return_code"),
               summary.get("error_string") or "", _tail(logfile))
        )
    return summary, gcode, logfile


def _tail(path: Path, n: int = 600) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def orca_version(settings: Settings) -> str | None:
    """跑一次 --help，从首行抠出 'OrcaSlicer-2.4.2'。启动时同步调一次。"""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "LC_ALL": "C",
        "APPDIR": "/opt/orca",
        "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin",
    }
    try:
        proc = subprocess.run([settings.orca_bin, "--help"], capture_output=True,
                              timeout=60, env=env, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.lower().startswith("orcaslicer"):
            return line.rstrip(":")
    return None
