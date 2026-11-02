#!/usr/bin/env python3
"""线宽一致性：统计画面里「一条走线被扫描线切出来的宽度」分布。

    python3 tests/width_probe.py <base-url> <job-id> [label] [el]

一根线段只要被横着切断，切出来的就是它的线宽（像素）。世界坐标里偏移
（老实现）会让宽度随「镜头深度 / 法线朝向」变化，同一张图里能切出 1px
到 6px 都有的线；屏幕空间展开（新实现）应该全部落在同一个宽度附近。
"""
from __future__ import annotations
import os, sys, statistics
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
  mode = 'single'; cur = index.layer_count - 1;
  document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('on', x.dataset.mode === 'single'));
  cam.el = A.el; cam.dist *= 0.25;
  draw(); draw();
  const px = new Uint8Array(W * H * 4);
  gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const isBg = i => Math.abs(px[i] - 36) < 6 && Math.abs(px[i + 1] - 36) < 6 && Math.abs(px[i + 2] - 36) < 6;
  const runs = [];
  const scan = (getPixel, len, step) => {
    for (let a = 0; a < len; a += step) {
      let n = 0;
      for (let b = 0; b < len; b++) {
        if (!isBg(getPixel(a, b))) n++;
        else { if (n >= 1 && n <= 40) runs.push(n); n = 0; }
      }
      if (n >= 1 && n <= 40) runs.push(n);
    }
  };
  scan((a, b) => (b * W + a) * 4, W, 1);           // 横着扫 → 竖直方向的宽度
  scan((a, b) => (a * W + b) * 4, H, 1);           // 竖着扫 → 水平方向的宽度
  runs.sort((x, y) => x - y);
  const q = f => runs[Math.min(runs.length - 1, Math.floor(f * runs.length))];
  return {n: runs.length, p10: q(0.10), p50: q(0.50), p90: q(0.90), p99: q(0.99),
          min: runs[0], max: runs[runs.length - 1],
          mean: +statistics_mean(runs).toFixed(2)};
  function statistics_mean(a) { let s = 0; for (const v of a) s += v; return s / a.length; }
}"""

with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
    pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
    pg.goto(f"{sys.argv[1]}/viewer/?job={sys.argv[2]}")
    pg.wait_for_function("() => window.__ready === true", timeout=120000)
    pg.wait_for_timeout(800)
    label = sys.argv[3] if len(sys.argv) > 3 else "cur"
    for el in [1.55, 0.62, 0.02]:
        r = pg.evaluate(JS, {"el": el})
        print(f"{label:>4} el={el:<5} {r}")
    br.close()
