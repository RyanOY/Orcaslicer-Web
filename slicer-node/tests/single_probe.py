#!/usr/bin/env python3
"""单层视角：不同俯仰角下的走线像素 / 实心度 / 缩放跟随。

    python3 tests/single_probe.py <base-url> <job-id> [label]
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

JS = """() => {
  const c = document.querySelector('canvas#gl');
  const gl = c.getContext('webgl2');
  const W = c.width, H = c.height;
  const isBg = (p, i) => Math.abs(p[i] - 36) < 6 && Math.abs(p[i + 1] - 36) < 6 && Math.abs(p[i + 2] - 36) < 6;
  const stat = () => {
    draw();
    const px = new Uint8Array(W * H * 4);
    gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, px);
    let x0 = W, x1 = -1, y0 = H, y1 = -1, ink = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (isBg(px, i)) continue;
      ink++;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    if (x1 < 0) return { ink: 0 };
    return { ink, bb: [x1 - x0 + 1, y1 - y0 + 1],
             fill: +(100 * ink / ((x1 - x0 + 1) * (y1 - y0 + 1))).toFixed(1) };
  };
  mode = 'single'; cur = index.layer_count - 1;
  document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('on', x.dataset.mode === 'single'));
  const out = {};
  for (const el of [1.55, 0.62, 0.30, 0.10, 0.02]) { cam.el = el; out['el' + el] = stat(); }
  const d0 = cam.dist;
  /* 放大后最容易看出「世界坐标偏移」的两个毛病：
     透视把远端拉细（一头粗一头细）、法线正对镜头时线宽塌成 0（没有厚度）。 */
  for (const el of [0.30, 0.10, 0.02]) {
    cam.el = el; cam.dist = d0 * 0.25;
    out['el' + el + '@z4'] = stat();
  }
  /* 同一视角、只转方位角：线宽不应该随朝向变（老版本会时粗时细） */
  cam.dist = d0 * 0.25; cam.el = 0.02;
  const az0 = cam.az;
  const inks = [];
  for (let i = 0; i < 8; i++) { cam.az = az0 + i * Math.PI / 4; inks.push(stat().ink); }
  out['azSweep'] = { min: Math.min(...inks), max: Math.max(...inks),
                     spread: +(100 * (Math.max(...inks) - Math.min(...inks)) / Math.max(...inks)).toFixed(1) };
  cam.az = az0; cam.dist = d0;
  out.__rib = window.__rib; out.__ribbon = window.__ribbon;
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
    r = pg.evaluate(JS)
    for k, v in r.items():
        print(f"{label:>4} {k:<10} {v}")
    if errs: print(label, "ERR", errs[:3])
    br.close()
