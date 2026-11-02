#!/usr/bin/env python3
"""平视 + 转头：找「正对镜头的墙」那种视角，比较老/新实现的走线覆盖与发丝线比例。"""
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
  cam.el = 0.02; cam.az = A.az; cam.dist = A.dist;
  draw(); draw();
  const px = new Uint8Array(W * H * 4);
  gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const isBg = i => Math.abs(px[i] - 36) < 6 && Math.abs(px[i + 1] - 36) < 6 && Math.abs(px[i + 2] - 36) < 6;
  let ink = 0; const runs = [];
  for (let y = 0; y < H; y++) {
    let n = 0;
    for (let x = 0; x < W; x++) {
      if (!isBg((y * W + x) * 4)) { ink++; n++; }
      else { if (n >= 1 && n <= 60) runs.push(n); n = 0; }
    }
  }
  let hair = 0; for (const r of runs) if (r <= 2) hair++;
  return {ink, runs: runs.length, hairPct: +(100 * hair / Math.max(runs.length, 1)).toFixed(1)};
}"""
with sync_playwright() as pw:
    url, job, label = sys.argv[1], sys.argv[2], sys.argv[3]
    dist = float(sys.argv[4]) if len(sys.argv) > 4 else 200
    br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
    pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
    pg.goto(f"{url}/viewer/?job={job}")
    pg.wait_for_function("() => window.__ready === true", timeout=120000)
    pg.wait_for_timeout(800)
    tot = 0
    for i in range(12):
        r = pg.evaluate(JS, {"az": -0.9 + i * 0.52, "dist": dist})
        tot += r["ink"]
        print(label, "az", round(-0.9 + i * 0.52, 2), json.dumps(r))
    print(label, "SUM ink", tot)
    br.close()
