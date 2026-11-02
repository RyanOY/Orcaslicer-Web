#!/usr/bin/env python3
"""平视/斜视下的「实心度」：走线像素占自身包围盒的比例 + 最长的竖直空白。

    python3 tests/fill_probe.py <base-url> <job-id> [label]
"""
from __future__ import annotations
import os, sys
from playwright.sync_api import sync_playwright

CHROME = os.environ.get(
    "PW_CHROME",
    "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/"
    "chromium-1248/chrome-linux-arm64/chrome")
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]

JS = """(A) => {
  const c = document.querySelector('canvas#gl');
  const gl = c.getContext('webgl2');
  const W = c.width, H = c.height;
  const grab = () => {
    draw();
    const px = new Uint8Array(W * H * 4);
    gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, px);
    return px;
  };
  const isBg = (p, i) => Math.abs(p[i] - 36) < 6 && Math.abs(p[i + 1] - 36) < 6 && Math.abs(p[i + 2] - 36) < 6;
  const stat = () => {
    const px = grab();
    let x0 = W, x1 = -1, y0 = H, y1 = -1, ink = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (isBg(px, i)) continue;
      ink++;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    if (x1 < 0) return { ink: 0 };
    const bw = x1 - x0 + 1, bh = y1 - y0 + 1;
    /* 中列与左右 1/4 列的竖直空白：连续空白段的最大/平均长度 */
    const colGap = (cx) => {
      let gap = 0, mx = 0, sum = 0, n = 0;
      for (let y = y0; y <= y1; y++) {
        const i = (y * W + cx) * 4;
        if (isBg(px, i)) { gap++; } else { if (gap) { sum += gap; n++; if (gap > mx) mx = gap; } gap = 0; }
      }
      if (gap) { sum += gap; n++; if (gap > mx) mx = gap; }
      return { max: mx, avg: n ? +(sum / n).toFixed(1) : 0 };
    };
    return { ink, bw, bh, fill: +(100 * ink / (bw * bh)).toFixed(1),
             gapMid: colGap((x0 + x1) >> 1), gapQ1: colGap(x0 + (bw >> 2)),
             gapQ3: colGap(x1 - (bw >> 2)) };
  };
  mode = 'accum'; cur = index.layer_count - 1;
  document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('on', x.dataset.mode === 'accum'));
  const out = {};
  for (const el of [1.55, 0.62, 0.30, 0.10, 0.02]) { cam.el = el; out[el] = stat(); }
  cam.el = 0.02; cam.dist *= 0.25; out['flat_zoom4'] = stat();
  return out;
}"""

with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
    pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8039"
    job = sys.argv[2] if len(sys.argv) > 2 else "2f7cf4eabf58"
    label = sys.argv[3] if len(sys.argv) > 3 else "cur"
    pg.goto(f"{base}/viewer/?job={job}")
    pg.wait_for_function("() => window.__ready === true", timeout=120000)
    pg.wait_for_timeout(600)
    r = pg.evaluate(JS, {})
    for k, v in r.items():
        print(f"{label:>4} el={k:<12} {v}")
    if errs: print(label, "ERR", errs[:3])
    br.close()
