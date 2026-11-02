#!/usr/bin/env python3
"""单层放大后：每段走线的屏幕宽度分布（切出来的 run 长度）+ 路径判定。"""
import sys, json
from playwright.sync_api import sync_playwright
CHROME = "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/chromium-1248/chrome-linux-arm64/chrome"
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]
JS = """(A) => {
  const c = document.querySelector('canvas#gl');
  const gl = c.getContext('webgl2');
  const W = c.width, H = c.height;
  mode = 'single'; cur = index.layer_count - 1;
  cam.el = A.el; cam.az = -0.9;
  cam.dist = A.dist;
  draw(); draw();
  const px = new Uint8Array(W * H * 4);
  gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const isBg = i => Math.abs(px[i] - 36) < 6 && Math.abs(px[i + 1] - 36) < 6 && Math.abs(px[i + 2] - 36) < 6;
  const runs = [];
  for (let y = 0; y < H; y++) {
    let n = 0;
    for (let x = 0; x < W; x++) {
      if (!isBg((y * W + x) * 4)) n++;
      else { if (n >= 1 && n <= 60) runs.push(n); n = 0; }
    }
  }
  runs.sort((a, b) => a - b);
  const q = f => runs[Math.min(runs.length - 1, Math.max(0, Math.floor(f * runs.length)))];
  let hair = 0; for (const r of runs) if (r <= 2) hair++;
  return {n: runs.length, p5: q(0.05), p25: q(0.25), p50: q(0.50), p75: q(0.75), p95: q(0.95),
          hairPct: +(100 * hair / Math.max(runs.length, 1)).toFixed(1),
          ribbon: (window.__ribbon || {}).key, ribN: (window.__rib || {}).n, dist: cam.dist};
}"""
with sync_playwright() as pw:
    url = sys.argv[1]; job = sys.argv[2]; label = sys.argv[3]
    br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
    pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
    pg.goto(f"{url}/viewer/?job={job}")
    pg.wait_for_function("() => window.__ready === true", timeout=120000)
    pg.wait_for_timeout(800)
    for el in [1.55, 0.62, 0.10, 0.02]:
        print(label, el, json.dumps(pg.evaluate(JS, {"el": el, "dist": 120}), ensure_ascii=False))
    br.close()
