import io
p = "/vol1/@appshare/com.dustinky.qwenpaw/.qwenpaw/workspaces/default/projects/orca-web-slicer/docs/feature-status.md"
s = io.open(p, encoding="utf-8").read().split("\n")

# 1) 删掉上一轮追加到文末的那一行
s = [ln for ln in s if "屏幕空间等宽折线（0.11.2）" not in ln]

row = ("| 屏幕空间等宽折线（0.11.2） | 角点只存「法线角 + ±半线宽」，线宽由顶点着色器在**屏幕空间**展开："
       "把中心点和中心点+一个半线宽都投影，取差值方向（丢掉长度）再乘回 `半线宽×uProj/w` 的像素数。"
       "每个顶点离中心线恒为半个线宽（像素），与视角/深度/朝向无关；拐点两侧仍是同一组数，接缝不缺口不重叠 | "
       "25.9 万段单层、dist=120：平视 el=0.02 发丝线（≤2 px）1.4%→**0.4%**；平视+正对墙 az=4.82 6.3%→**1.5%**；"
       "12 个方位角走线像素 +1.0%；俯视线宽分布 p5/p50/p95 = 5/5/7 px 不变 |")

# 2) 插到 0.11.1 那行后面
idx = next(i for i, ln in enumerate(s) if ln.startswith("| 折线回退（0.11.1）"))
s.insert(idx + 1, row)
io.open(p, "w", encoding="utf-8").write("\n".join(s))
print("ok")
