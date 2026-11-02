"""对照实验：同一个「立起来 90°」的船，用两种方式交给 Orca。
A. 我们现在的做法：顶点已经烘好（transform_tris3d）
B. 让 Orca 自己转：顶点保持原样，靠 build item 的 transform 属性

如果 B 也切失败 → 是 Orca 引擎在这个姿态下的问题，不是我们烘坐标的错。
"""
import math, subprocess, zipfile
from pathlib import Path
from app.config import load_settings
from app import plate as P, mesh as M
from app import models as MO

s = load_settings()
PROF = Path("/data/jobs/3a1a055f70e9")
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LC_ALL": "C", "LANG": "C",
       "APPDIR": "/opt/orca", "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin",
       "TMPDIR": "/tmp"}
BOAT = "SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"


def slice_it(tmf, outdir):
    argv = [s.orca_bin, str(tmf), "--slice", "0", "--arrange", "0",
            "--load-settings", "%s;%s" % (PROF / "process.json", PROF / "machine.json"),
            "--load-filaments", str(PROF / "filament_1.json"),
            "--outputdir", str(outdir), "--logfile", str(outdir / "orca.log")]
    p = subprocess.run(argv, env=ENV, cwd=str(outdir), capture_output=True, timeout=900)
    return p.returncode, (outdir / "plate_1.gcode").is_file()


a = math.radians(90); c, sn = math.cos(a), math.sin(a)
Rx = [1, 0, 0, 0, c, -sn, 0, sn, c]           # 列向量约定
# A：顶点烘好
work = Path("/tmp/cmp_a"); work.mkdir(parents=True, exist_ok=True)
o = {"file": BOAT, "part": "", "name": "boat", "x": 100, "y": 100, "z": 0,
     "rot": 0, "scale": 1, "extruder": 1, "m": Rx}
P.build(s, MO, [o], work / "plate.3mf")
print("A baked:", slice_it(work / "plate.3mf", work))

# B：转置成 3MF 的行向量 4×3 矩阵 + 平移，让旋转后 XY 中心 (100,100)、min z = 0
mesh = MO.load_mesh(s, BOAT, None)
x0, y0, z0, x1, y1, z1 = mesh.bbox()
cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
# 旋转后 z' = y  → min z' = y0 - cy（相对中心）；先摆到中心再平移
tz = -(y0 - cy)                    # 让旋转后的最低点落到 0
tx = 100 - cx
ty = 100 + cz                      # y' = -z → 中心 y' = -cz 相对，需要补回来
MT = [[Rx[0], Rx[3], Rx[6]], [Rx[1], Rx[4], Rx[7]], [Rx[2], Rx[5], Rx[8]]]
tstr = " ".join("%.6f" % v for row in MT for v in row) + " %.6f %.6f %.6f" % (tx, ty, tz)

vs = [v for tri in mesh.tris for v in tri]
uniq = {}
idx = []
for v in vs:
    k = (round(v[0], 4), round(v[1], 4), round(v[2], 4))
    if k not in uniq:
        uniq[k] = len(uniq)
    idx.append(uniq[k])
verts = '\n'.join('<vertex x="%g" y="%g" z="%g"/>' % k for k in uniq)
tris = '\n'.join('<triangle v1="%d" v2="%d" v3="%d"/>' % (idx[i], idx[i + 1], idx[i + 2])
                 for i in range(0, len(idx), 3))
model = ('<?xml version="1.0" encoding="UTF-8"?>'
         '<model unit="millimeter" xml:lang="zh-CN" '
         'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
         '<resources><object id="1" type="model" name="boat">'
         '<mesh><vertices>%s</vertices><triangles>%s</triangles></mesh>'
         '</object></resources>'
         '<build><item objectid="1" printable="1" transform="%s"/></build></model>'
         % (verts, tris, tstr))
wb = Path("/tmp/cmp_b"); wb.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(wb / "plate.3mf", "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("[Content_Types].xml", M.CONTENT_TYPES)
    zf.writestr("_rels/.rels", M.RELS)
    zf.writestr("3D/3dmodel.model", model)
print("B transform:", slice_it(wb / "plate.3mf", wb), "| transform =", tstr)
