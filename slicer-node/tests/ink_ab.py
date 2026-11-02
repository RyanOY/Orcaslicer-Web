#!/usr/bin/env python3
"""A/B：同一个任务、不同俯仰角，统计走线占用像素（底板基线单独量）。

    python3 tests/ink_ab.py <base-url> <job-id> [label]
"""
from __future__ import annotations
import os, sys, json
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
  const ink = () => {
    draw();
    const px = new Uint8Array(W * H * 4);
    gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, px);
    let n = 0;
    for (let i = 0; i < px.length; i += 4)
      if (!(Math.abs(px[i] - 36) < 6 && Math.abs(px[i + 1] - 36) < 6 && Math.abs(px[i + 2] - 36) < 6)) n++;
    return n;
  };
  const setMode = m => { mode = m;
    document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('on', x.dataset.mode === m)); };
  const out = {};
  /* 底板基线：把整份数据藏起来（uHiddenMask 只在类型着色下生效，先切 scheme=0） */
  const sch0 = scheme; scheme = 0;
  const setHid = on => {
    hidden.clear();
    if (on) for (let t = 0; t < 15; t++) hidden.add(t);
  };
  const modes = [['single', index.layer_count - 1], ['accum', index.layer_count - 1],
                 ['all', index.layer_count - 1]];
  for (const [m, c0] of modes) {
    setMode(m); cur = c0;
    for (const el of [1.55, 0.92, 0.62, 0.30, 0.10, 0.02]) {
      cam.el = el;
      setHid(true);  const base = ink();
      setHid(false); const full = ink();
      out[m + '_' + el] = full - base;
      out[m + '_' + el + '_base'] = base;
    }
  }
  scheme = sch0;
  setMode('single'); cam.el = 1.55; cur = index.layer_count - 1;
  const d0 = cam.dist;
  setHid(false);
  out['zoom1'] = ink();
  cam.dist = d0 / 4; out['zoom4'] = ink();
  cam.dist = d0;
  out.__rib = window.__rib; out.__ribbon = window.__ribbon;
  return out;
}"""


def run(base, job, label):
    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
        pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{base}/viewer/?job={job}")
        pg.wait_for_function("() => window.__ready === true", timeout=120000)
        pg.wait_for_timeout(600)
        r = pg.evaluate(JS, {})
        for k, v in r.items():
            print(f"{label:>4} {k:>14} {v}")
        if errs:
            print(label, "ERR", errs[:3])
        pg.screenshot(path=f"/tmp/ab_{label}_flat.png")
        br.close()


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8039"
    job = sys.argv[2] if len(sys.argv) > 2 else "2f7cf4eabf58"
    run(base, job, sys.argv[3] if len(sys.argv) > 3 else "cur")
