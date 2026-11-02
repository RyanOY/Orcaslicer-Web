#!/usr/bin/env python3
"""把 buildRibbons 的「世界坐标偏移 + 斜接/斜切楔形」换成「角平分线法线 + 半线宽」。

改完的语义：
  · 每个折线角点算一个单位法线角（两侧法线的归一化和 = 角平分线）；
  · 拐点两侧的顶点读到同一个 (角度, 半线宽) → 屏幕上是同一个点，不缺口不重叠；
  · 展开量交给着色器按像素计算（见 VS_RIBBON），所以：
      - 任何视角等宽，不会一头粗一头细（透视不再拉细远端）；
      - 镜头平视时不是零面积四边形，线仍然看得见（"没有厚度"的根因）。
"""
import io, sys

P = "/vol1/@appshare/com.dustinky.qwenpaw/.qwenpaw/workspaces/default/projects/orca-web-slicer/slicer-node/viewer/index.html"
src = io.open(P, encoding="utf-8").read().split("\n")

# 1-based 行号：1464 是「关节」注释块首行，1540 是楔形循环的收尾大括号
lo, hi = 1464 - 1, 1539
head = src[lo]
tail = src[hi - 1]
assert "关节：能斜接就斜接" in head, head
assert tail.strip() == "}", tail
assert "clearRun();" in src[hi], src[hi]

NEW = r'''    /* ---- 角点法线：一条折线就是一条「等宽带子」，每个角点只存一个法线方向 ----
       法线 = 入段左法线与出段左法线的归一化和（角平分线）。拐点前后两段
       读到的**是同一个数**，所以屏幕上必然是同一个点：不缺口、不重叠、
       也不会像逐段四边形那样在每个关节掐一次腰。

       这里刻意不再乘 `sec(θ/2)`（斜接长度），因为线宽现在由着色器在屏幕
       空间展开 —— 屏幕上从角点沿法线走半个线宽，乘与不乘的差别只是
       「圆角 vs 尖角」，而在 1~3 px 的线宽下根本看不出来；
       反过来，保留世界坐标的斜接长度会破坏等宽（斜接尖角比线宽还长）。 */
    const nAng = new Float32Array(m), nHw = new Float32Array(m);
    for (let k = 0; k < m; k++) {
      const pk = closed ? (k - 1 + nPiece) % nPiece : (k > 0 ? k - 1 : -1);   // 入段
      const qk = closed ? k % nPiece : (k < m - 1 ? k : -1);                  // 出段
      let nx = 0, ny = 0, hw = 0, cnt = 0;
      if (pk >= 0) { nx += -dirY[pk]; ny += dirX[pk]; hw += phwP[pk]; cnt++; }
      if (qk >= 0) { nx += -dirY[qk]; ny += dirX[qk]; hw += phwP[qk]; cnt++; }
      let l = Math.hypot(nx, ny);
      if (cnt === 0) {                              // 孤立段：随便给个法线
        nx = 0; ny = 1; l = 1;
      } else if (l < 1e-6) {                        // ≈180° 折返：退回入段法线
        const s = pk >= 0 ? pk : qk;
        nx = -dirY[s]; ny = dirX[s]; l = 1;
      } else {
        nx /= l; ny /= l;
      }
      nAng[k] = Math.atan2(ny, nx);
      nHw[k] = cnt ? hw / cnt : phwP[0];
    }

    /* ---- 发出几何：每段一个四边形 ----
       颜色/属性取该段覆盖的原始段里最长的那条，速度/加速度着色仍然逐段精确。 */
    for (let k = 0; k < nPiece; k++) {
      const ia = keep[k];
      const ib = closed ? keep[(k + 1) % m] : keep[k + 1];
      const kb = closed ? (k + 1) % m : k + 1;
      const bi = rep[k];
      const ty = pty[bi], at = pat[bi], va = pva[bi], ae = pae[bi];
      const x0 = px[ia], y0 = py[ia], x1 = px[ib], y1 = py[ib];
      const a0 = nAng[k], h0 = nHw[k], a1 = nAng[kb], h1 = nHw[kb];
      vtx(x0, y0, z, a0, 1, h0, ty, at, va, ae);
      vtx(x0, y0, z, a0, -1, h0, ty, at, va, ae);
      vtx(x1, y1, z, a1, 1, h1, ty, at, va, ae);
      vtx(x1, y1, z, a1, 1, h1, ty, at, va, ae);
      vtx(x0, y0, z, a0, -1, h0, ty, at, va, ae);
      vtx(x1, y1, z, a1, -1, h1, ty, at, va, ae);
    }'''.split("\n")

src[lo:hi] = NEW
io.open(P, "w", encoding="utf-8").write("\n".join(src))
print("patched", lo + 1, "-", hi)
