#!/usr/bin/env python3
"""预览几何自检：角度依赖 / 平视可见性 / 拐点接缝。

    python3 tests/ribbon_probe.py <base-url> <job-id>

检查项：
  1. 升 / 斜 / 平（el=1.55 / 0.62 / 0.02）三个视角分别统计「走线像素占比」。
     · 平视：以前线宽是固定毫米数、四边形躺在 XY 平面上，正侧面看是零面积 →
       几乎什么都看不到。现在线宽在屏幕空间展开，平视应该和斜视一个量级。
  2. 斜视下逐层累积视角（accum/all）也要有走线（不能整屏空白）。
  3. 截图存盘，便于人眼复核。
"""
from __future__ import annotations
import json, os, sys
from playwright.sync_api import sync_playwright

CHROME = os.environ.get(
    "PW_CHROME",
    "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/"
    "chromium-1248/chrome-linux-arm64/chrome")
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]

SAMPLE = """(args) => {
  const A = args;
  cam.el = A.el;
  mode = A.mode;
  document.querySelectorAll('[data-mode]').forEach(x => {
    x.classList.toggle('on', x.dataset.mode === mode);
  });
  if (A.cur >= 0 && index.layers[A.cur]) setLayer(A.cur);
  else requestDraw();
  if (A.zoom) cam.dist = A.zoom;
  requestDraw();
  const c = document.querySelector('canvas#gl');
  const gl = c.getContext('webgl2');
  // 先量一次「只有底板」的基线：把走线全藏掉
  const saveHidden = new Set(hidden);
  for (let t = 0; t < 15; t++) hidden.add(t);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); draw();
  let w = c.width, h = c.height;
  const base = new Uint8Array(w * h * 4);
  gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, base);
  hidden.clear(); for (const t of saveHidden) hidden.add(t);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); draw();
  const px = new Uint8Array(w * h * 4);
  gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const BG = 36;
  const isBg = (r, g, b) => Math.abs(r - BG) < 6 && Math.abs(g - BG) < 6 && Math.abs(b - BG) < 6;
  let tot = 0, ink = 0, ink0 = 0;
  for (let i = 0; i < px.length; i += 4) {
    tot++;
    if (!isBg(px[i], px[i + 1], px[i + 2])) ink++;
    if (!isBg(base[i], base[i + 1], base[i + 2])) ink0++;
  }
  return {el: A.el, mode: A.mode, tot, ink, base: ink0,
          ratio: +(100 * (ink - ink0) / tot).toFixed(2),
          ribbon: window.__ribbon ? window.__ribbon.n : -1,
          tier: window.__tier ? window.__tier : null,
          stat: window.__ribStat || null};
}"""


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8039"
    job = sys.argv[2] if len(sys.argv) > 2 else "2f7cf4eabf58"
    outdir = os.environ.get("OUT", "/tmp/rib")
    os.makedirs(outdir, exist_ok=True)
    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
        pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{base}/viewer/?job={job}")
        pg.wait_for_function("() => window.__ready === true", timeout=120000)
        pg.wait_for_timeout(600)
        cases = [
            ("top_single", 1.55, "single", -1),
            ("obl_single", 0.62, "single", -1),
            ("flat_single", 0.02, "single", -1),
            ("flat_accum", 0.02, "accum", -1),
            ("obl_accum", 0.62, "accum", -1),
        ]
        for name, el, mode, cur in cases:
            r = pg.evaluate(SAMPLE, {"el": el, "mode": mode, "cur": cur, "zoom": 0})
            pg.screenshot(path=f"{outdir}/{name}.png")
            print(name, json.dumps(r, ensure_ascii=False))
        if errs:
            print("JS ERROR:", errs[:5])
        br.close()


if __name__ == "__main__":
    main()
