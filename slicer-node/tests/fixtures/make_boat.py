"""生成一条小船（船体 + 驾驶舱），导出 ASCII STL。

不联网、参数化：船长 80mm、船宽 30mm、吃水 10mm。
拓扑：每个横向站位是一条**闭合折线**（左舷甲板边 → U 形船底 → 右舷甲板边 →
沿甲板直边回到起点），相邻站位之间铺成筒状，首尾用扇形封口 ——
这样每条边正好出现两次，天然流形，Orca 不用修复就能切。
"""
import math
from pathlib import Path

L = 80.0          # 船长
W = 30.0          # 船宽
D = 10.0          # 吃水
N_ST = 26         # 纵向站位
N_PT = 15         # U 形上的点数（含左右舷甲板边）

tris = []


def station(x):
    """(半宽, 龙骨深度)：船首收成尖，船尾略方。"""
    t = (x + L / 2) / L
    if t > 0.62:
        u = (t - 0.62) / 0.38
        w = W / 2 * math.sqrt(max(0.0, 1.0 - u * u)) + 0.4
        d = D * (1.0 - 0.5 * u * u)
    else:
        u = (0.62 - t) / 0.62
        w = W / 2 * (1.0 - 0.14 * u * u)
        d = D * (1.0 - 0.04 * u * u)
    return max(w, 0.4), d


def loop(x):
    """闭合横剖折线：起点=左舷甲板边(-w,0)，到龙骨，再到右舷甲板边(+w,0)。"""
    w, d = station(x)
    pts = []
    for k in range(N_PT):
        a = math.pi * (1.0 - k / (N_PT - 1))     # pi → 0（左舷 → 右舷）
        y = w * math.cos(a)
        z = -d * max(0.0, math.sin(a)) ** 1.6
        pts.append((x, y, z))
    return pts


def add(a, b, c):
    tris.append((a, b, c))


stations = [loop(-L / 2 + L * i / (N_ST - 1)) for i in range(N_ST)]
M = N_PT

# 1) 筒状外壳（k = M-1 → 0 这一段就是甲板面）
for i in range(N_ST - 1):
    s0, s1 = stations[i], stations[i + 1]
    for k in range(M):
        k2 = (k + 1) % M
        a, b = s0[k], s0[k2]
        c, d = s1[k2], s1[k]
        add(a, b, c)
        add(a, c, d)

# 2) 首尾封口（闭合折线扇形）
for s in (stations[0], stations[-1]):
    cx = sum(p[0] for p in s) / M
    cy = sum(p[1] for p in s) / M
    cz = sum(p[2] for p in s) / M
    c = (cx, cy, cz)
    for k in range(M):
        add(c, s[k], s[(k + 1) % M])

# 3) 驾驶舱：方块，底面埋进船体 2mm（重叠比共面安全）
c = dict(x0=-12.0, x1=14.0, y0=-9.0, y1=9.0, z0=-2.0, z1=20.0)
P = {
    "lbb": (c["x0"], c["y0"], c["z0"]), "lbf": (c["x0"], c["y1"], c["z0"]),
    "rbb": (c["x1"], c["y0"], c["z0"]), "rbf": (c["x1"], c["y1"], c["z0"]),
    "ltb": (c["x0"], c["y0"], c["z1"]), "ltf": (c["x0"], c["y1"], c["z1"]),
    "rtb": (c["x1"], c["y0"], c["z1"]), "rtf": (c["x1"], c["y1"], c["z1"]),
}
for q in (("lbb", "lbf", "rbf"), ("lbb", "rbf", "rbb"),
          ("ltb", "rtf", "ltf"), ("ltb", "rtb", "rtf"),
          ("lbb", "rbb", "rtb"), ("lbb", "rtb", "ltb"),
          ("lbf", "ltf", "rtf"), ("lbf", "rtf", "rbf"),
          ("lbb", "ltb", "ltf"), ("lbb", "ltf", "lbf"),
          ("rbb", "rbf", "rtf"), ("rbb", "rtf", "rtb")):
    add(*[P[k] for k in q])


def signed_volume(ts):
    v = 0.0
    for a, b, c in ts:
        v += (a[0] * (b[1] * c[2] - b[2] * c[1])
              - a[1] * (b[0] * c[2] - b[2] * c[0])
              + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return v


# 法向统一朝外：体积为负就整体翻面（不逐面猜）
if signed_volume(tris) < 0:
    tris = [(a, c, b) for a, b, c in tris]

zmin = min(p[2] for t in tris for p in t)
tris = [tuple((p[0], p[1], p[2] - zmin) for p in t) for t in tris]


def norm_of(t):
    a, b, c = t
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    ln = math.sqrt(sum(k * k for k in n)) or 1.0
    return tuple(k / ln for k in n)


out = Path(__file__).with_name("boat.stl")
with out.open("w") as f:
    f.write("solid boat\n")
    for t in tris:
        n = norm_of(t)
        f.write("  facet normal %.6f %.6f %.6f\n    outer loop\n" % n)
        for p in t:
            f.write("      vertex %.4f %.4f %.4f\n" % p)
        f.write("    endloop\n  endfacet\n")
    f.write("endsolid boat\n")

print("triangles %d  volume %.1f mm³  -> %s (%d bytes)" % (
    len(tris), signed_volume(tris), out.name, out.stat().st_size))
print("bbox x[%.1f %.1f] y[%.1f %.1f] z[%.1f %.1f]" % (
    min(p[0] for t in tris for p in t), max(p[0] for t in tris for p in t),
    min(p[1] for t in tris for p in t), max(p[1] for t in tris for p in t),
    min(p[2] for t in tris for p in t), max(p[2] for t in tris for p in t)))
