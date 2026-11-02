#!/usr/bin/env python3
"""对比 flat / tube 两种几何在同一视角下的「实心度」，用来判断
「走线没有厚度（能看穿）」到底是不是缺竖直面造成的。

    python3 tests/tube_fill.py <base-url> <job-id>
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
    const t0 = performance.now();
    draw();
    const ms = performance.now() - t0;
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
    if (x1 < 0) return { ink: 0, tier, ms: +ms.toFixed(1) };
    return { ink, fill: +(100 * ink / ((x1 - x0 + 1) * (y1 - y0 + 1))).toFixed(1),
             tier, ms: +ms.toFixed(1) };
  };
  mode = 'accum'; cur = index.layer_count - 1;
  document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('on', x.dataset.mode === 'accum'));
  const out = {};
  for (const tm of ['off', 'on']) {
    tubeMode = tm; tier = 'flat';
    for (const el of [0.62, 0.30, 0.02]) {
      cam.el = el;
      out[tm + '@' + el] = stat();
    }
  }
  tubeMode = 'off'; tier = 'flat';
  return out;
}"""

with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
    pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8039"
    job = sys.argv[2] if len(sys.argv) > 2 else "2f7cf4eabf58"
    pg.goto(f"{base}/viewer/?job={job}")
    pg.wait_for_function("() => window.__ready === true", timeout=120000)
    pg.wait_for_timeout(600)
    r = pg.evaluate(JS)
    for k, v in r.items():
        print(k, v)
    if errs: print("ERR", errs[:3])
    br.close()
