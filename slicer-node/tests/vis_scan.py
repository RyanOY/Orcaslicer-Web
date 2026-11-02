#!/usr/bin/env python3
"""走线可见性扫描：三视角 × 多档缩放，连画两帧，量化画布上有没有走线。

    python3 tests/vis_scan.py http://127.0.0.1:8039 <job-id> [w] [h]

指标（占画布像素比）：
  bright = 亮度 > 55 的像素（含底板网格）
  pal    = 色度匹配调色板（外墙/内墙/填充/顶面…）的像素 —— 真正的走线
两帧都要 > 阈值，且第二帧不能比第一帧少一半（防「第一帧有、后面没了」）。
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

CHROME = os.environ.get(
    "PW_CHROME",
    "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/"
    "chromium-1248/chrome-linux-arm64/chrome")
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]

STAT = """() => {
  const c = document.querySelector('canvas#gl');
  const gl = c.getContext('webgl2') || c.getContext('webgl');
  const w = c.width, h = c.height;
  const px = new Uint8Array(w * h * 4);
  const out = [];
  const chroma = (r,g,b) => { const m = Math.max(r,g,b) || 1; return [r/m, g/m, b/m]; };
  const PAL = [[1,.49,.22],[1,.90,.30],[.69,.19,.16],[.59,.33,.80],
               [.70,.53,1],[.30,.82,.63],[.94,.25,.25],[.90,.70,.70]];
  const near = (cc) => {
    for (const p of PAL) {
      if (Math.abs(cc[0]-p[0]) < .13 && Math.abs(cc[1]-p[1]) < .13 &&
          Math.abs(cc[2]-p[2]) < .13) return true;
    }
    return false;
  };
  for (let pass = 0; pass < 2; pass++) {
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    draw();
    gl.finish();
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
    let bright = 0, pal = 0, maxl = 0;
    for (let i = 0; i < px.length; i += 4 * 3) {
      const r = px[i], g = px[i+1], b = px[i+2];
      if (r > maxl) maxl = r; if (g > maxl) maxl = g; if (b > maxl) maxl = b;
      const m = Math.max(r,g,b);
      if (m < 55) continue;
      bright++;
      if (near(chroma(r,g,b))) pal++;
    }
    const n = Math.ceil(px.length / (4 * 3));
    out.push({ bright: bright / n, pal: pal / n, max: maxl });
  }
  return { w, h, frames: out, stats: window.__stats || null,
           rib: window.__rib || null, ribbon: window.__ribbon || null };
}"""


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8039"
    job = sys.argv[2]
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 390
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 844
    tag = f"{w}x{h}"
    rows = []
    errs: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=ARGS)
        page = browser.new_page(viewport={"width": w, "height": h},
                                device_scale_factor=3, is_mobile=True, has_touch=True)
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        page.goto(f"{base}/viewer/?job={job}", wait_until="load")
        page.wait_for_function("() => window.__ready === true", timeout=120000)
        page.wait_for_timeout(1200)
        cv = page.locator("canvas#gl")
        for m in ("single", "accum", "all"):
            page.click(f'[data-mode="{m}"]')
            page.wait_for_timeout(800)
            for zoom, label in ((0, "fit"), (4, "in"), (8, "deep")):
                if zoom:
                    box = cv.bounding_box()
                    page.mouse.move(box["x"] + box["width"] / 2,
                                    box["y"] + box["height"] / 2)
                    for _ in range(zoom):
                        page.mouse.wheel(0, -120)
                        page.wait_for_timeout(90)
                    page.wait_for_timeout(500)
                st = page.evaluate(STAT)
                st["view"], st["zoom"] = m, label
                rows.append(st)
                page.screenshot(path=f"/tmp/vis_{tag}_{m}_{label}.png")
        browser.close()

    print(f"# {job} @ {tag}  segs={(rows[0]['stats'] or {}).get('segs')}")
    print(f"{'view':7}{'zoom':6}{'bright1':>9}{'bright2':>9}{'pal1':>8}{'pal2':>8}"
          f"{'max':>5}  ribbon n / est,coarse")
    bad = 0
    for r in rows:
        f = r["frames"]
        rb = r.get("ribbon") or {}
        ri = r.get("rib") or {}
        print(f"{r['view']:7}{r['zoom']:6}{f[0]['bright']:>9.3f}{f[1]['bright']:>9.3f}"
              f"{f[0]['pal']:>8.3f}{f[1]['pal']:>8.3f}{f[1]['max']:>5}"
              f"  n={rb.get('n')} est={ri.get('est')} co={ri.get('coarse')}")
        if f[0]["pal"] < 0.004 or f[1]["pal"] < 0.004:
            bad += 1
            print("   ^^^ 走线不可见")
        elif f[1]["pal"] < f[0]["pal"] * 0.6:
            bad += 1
            print("   ^^^ 第二帧走线大幅减少（几何丢失）")
    if errs:
        print("! errors:", errs[:3])
    print("FAIL" if bad else "OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
