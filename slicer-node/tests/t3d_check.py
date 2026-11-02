import sys, time, math
from pathlib import Path
sys.path.insert(0, ".")
from app import mesh as M
from app import plate as P

def axis_angle(axis, angle):
    x, y, z = axis
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return [t*x*x+c, t*x*y-s*z, t*x*z+s*y,
            t*x*y+s*z, t*y*y+c, t*y*z-s*x,
            t*x*z-s*y, t*y*z+s*x, t*z*z+c]

p = "/vol1/1000/orca/model/SpeedBoatRace_Bambu+Pla+Basic_A1_Mini.3mf"
t0 = time.time(); m = M.load(Path(p)); print("load %.2fs tris=%d" % (time.time()-t0, len(m.tris)))
bb = m.bbox(); print("bbox", [round(v,2) for v in bb])
center = ((bb[0]+bb[3])/2, (bb[1]+bb[4])/2, (bb[2]+bb[5])/2)

# 贴面：把局部法线 +X 转到 -Z。用 alignNegZ 等价：axis = cross((1,0,0),(0,0,-1)) = (0,1,0)，angle=90°
R = axis_angle((0,1,0), math.pi/2)
print("R (rotY +90):", [round(v,4) for v in R])

t0 = time.time()
tris = M.transform_tris3d(m.tris, 1.0, R, center, 100, 100, 0)
print("transform_tris3d %.2fs" % (time.time()-t0))
zmin = min(v[2] for t in tris for v in t)
zmax = max(v[2] for t in tris for v in t)
xs = [v[0] for t in tris for v in t]; ys = [v[1] for t in tris for v in t]
print("min z=%.3f (期望 0), max z=%.3f" % (zmin, zmax))
print("XY center=%.2f,%.2f (期望 100,100)" % ((min(xs)+max(xs))/2, (min(ys)+max(ys))/2))

# 二进制网格 + 投影凸包性能
t0=time.time(); verts, faces = M.index_mesh(m.tris); print("index_mesh %.2fs verts=%d faces=%d" % (time.time()-t0, len(verts), len(faces)))
t0=time.time(); b = M.mesh_binary(m); print("mesh_binary(用缓存焊接) %.2fs bytes=%d" % (time.time()-t0, len(b)))

# 用 plate 的 _m_hull 测贴面后占地凸包
o = {"file":"x","m":R,"rot":0,"scale":1.0,"x":100,"y":100,"z":0}
sh = {"hull": M.footprint(m.tris) if hasattr(M,'footprint') else None, "bbox": bb}
t0=time.time(); hull = P._m_hull(m, "x", R); print("_m_hull %.2fs pts=%d" % (time.time()-t0, len(hull)))
fp = P._footprint_hull(sh, m, o); print("_footprint_hull pts=%d" % len(fp))

# 一致性：R=rot_z(90) 时 transform_tris3d == transform_tris（z0=0）
Rz = M.rot_z_matrix(90)
a = M.transform_tris(m.tris, 1.0, 90, center[0], center[1], 50, 60, 0)
bt = M.transform_tris3d(m.tris, 1.0, Rz, center, 50, 60, 0)
# 抽几个点对比
import itertools
av = sorted({(round(v[0],2),round(v[1],2),round(v[2],2)) for t in a for v in t})
bv = sorted({(round(v[0],2),round(v[1],2),round(v[2],2)) for t in bt for v in t})
print("rot90 一致性:", av == bv, "| zmin old/new:", min(v[2] for v in av), min(v[2] for v in bv))
