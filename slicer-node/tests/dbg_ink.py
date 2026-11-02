#!/usr/bin/env python3
import os, sys, json
from playwright.sync_api import sync_playwright
CHROME = "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/chromium-1248/chrome-linux-arm64/chrome"
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8039"
job = sys.argv[2] if len(sys.argv) > 2 else "2f7cf4eabf58"
JS = """() => {
  const c = document.querySelector('canvas#gl');
  const gl = c.getContext('webgl2');
  const shot = () => { draw();
    const px = new Uint8Array(c.width * c.height * 4);
    gl.readPixels(0,0,c.width,c.height,gl.RGBA,gl.UNSIGNED_BYTE,px);
    let n = 0;
    for (let i = 0; i < px.length; i += 4) if (!(Math.abs(px[i]-36)<6 && Math.abs(px[i+1]-36)<6 && Math.abs(px[i+2]-36)<6)) n++;
    return n; };
  cam.el = 0.62; mode = 'single'; cur = index.layer_count - 1;
  const a = shot();
  hidden.add(0); hidden.add(1); hidden.add(3);
  const b = shot();
  hidden.clear();
  const d = shot();
  return {on: a, minus3: b, restore: d, ribbon: window.__ribbon, rib: window.__rib, cur, layers: index.layer_count,
          prog: !!progRibbon};
}"""
with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
    pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
    logs = []
    pg.on("console", lambda m: logs.append((m.type, m.text)))
    pg.on("pageerror", lambda e: logs.append(("pageerror", str(e))))
    pg.goto(f"{base}/viewer/?job={job}")
    pg.wait_for_function("() => window.__ready === true", timeout=120000)
    pg.wait_for_timeout(500)
    print(json.dumps(pg.evaluate(JS), ensure_ascii=False))
    print(logs[:6])
    pg.screenshot(path="/tmp/dbg_single.png")
    br.close()
