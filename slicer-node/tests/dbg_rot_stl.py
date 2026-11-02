"""最独立的对照：把旋转后的模型写成 STL 直接喂 orca-slicer（完全绕开我们的 3MF 写入器）。
如果 STL 也失败 → 是 Orca 引擎在该姿态的问题。
"""
import math, struct, subprocess
from pathlib import Path
from app.config import load_settings
from app import mesh as M
from app import models as MO

s = load_settings()
PROF = Path("/data/jobs/3a1a055f70e9")
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LC_ALL": "C", "LANG": "C",
       "APPDIR": "/opt/orca", "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin",
       "TMPDIR": "/tmp"}
BOAT = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"


def rx(d):
    a = math.radians(d); c, sn = math.cos(a), math.sin(a)
    return [1, 0, 0, 0, c, -sn, 0, sn, c]


def write_stl(tris, path):
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for tri in tris:
            f.write(struct.pack("<3f", 0, 0, 0))
            for v in tri:
                f.write(struct.pack("<3f", v[0], v[1], v[2]))
            f.write(struct.pack("<H", 0))


mesh = MO.load_mesh(s, BOAT, None)
x0, y0, z0, x1, y1, z1 = mesh.bbox()
center = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
for label, deg in (("stl_rx0", 0), ("stl_rx90", 90), ("stl_rx88", 88)):
    tris = M.transform_tris3d(mesh.tris, 1.0, rx(deg), center, 100.0, 100.0, 0.0)
    work = Path("/tmp") / label; work.mkdir(parents=True, exist_ok=True)
    stl = work / "m.stl"
    write_stl(tris, stl)
    out = work / "out"; out.mkdir(exist_ok=True)
    argv = [s.orca_bin, str(stl), "--slice", "0", "--arrange", "0",
            "--load-settings", "%s;%s" % (PROF / "process.json", PROF / "machine.json"),
            "--load-filaments", str(PROF / "filament_1.json"),
            "--outputdir", str(out), "--logfile", str(work / "orca.log")]
    p = subprocess.run(argv, env=ENV, cwd=str(work), capture_output=True, timeout=900)
    print("%-10s rc=%-4s gcode=%s" % (label, p.returncode, (out / "plate_1.gcode").is_file()), flush=True)
