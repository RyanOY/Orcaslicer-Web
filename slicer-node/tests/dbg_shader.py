#!/usr/bin/env python3
import sys, json
from playwright.sync_api import sync_playwright
CHROME = "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/chromium-1248/chrome-linux-arm64/chrome"
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]
JS = """() => {
  mode = 'single'; cur = index.layer_count - 1; cam.el = 0.62;
  draw(); draw();
  const sh = gl.getAttachedShaders(progRibbon.p);
  const src = gl.getShaderSource(sh[0]);
  return {vsLen: src.length, hasAngle: src.indexOf('aRib.w') >= 0,
          hasUViewport: src.split('uViewport').length - 1,
          ribbon: window.__ribbon || null, rib: window.__rib || null,
          md5: src.length + ':' + src.slice(-60)};
}"""
with sync_playwright() as pw:
    for url in sys.argv[1:]:
        br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
        pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
        pg.goto(f"{url}/viewer/?job=2f7cf4eabf58")
        pg.wait_for_function("() => window.__ready === true", timeout=120000)
        pg.wait_for_timeout(600)
        print(url, json.dumps(pg.evaluate(JS), ensure_ascii=False))
        br.close()
