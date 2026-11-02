#!/usr/bin/env python3
import sys, json
from playwright.sync_api import sync_playwright
CHROME = "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/chromium-1248/chrome-linux-arm64/chrome"
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]
JS = """() => {
  mode = 'accum'; cur = index.layer_count - 1; cam.el = 0.02;
  draw();
  const c = document.querySelector('canvas#gl');
  return {rib: window.__rib || null, ribbon: window.__ribbon || null,
          tier: (typeof tier === 'function' ? '?' : tier),
          prog: !!progRibbon, tube: tubeMode,
          hashes: (function(){ /* 采样 40 个像素做指纹 */
            const gl = c.getContext('webgl2');
            const px = new Uint8Array(c.width*c.height*4);
            gl.readPixels(0,0,c.width,c.height,gl.RGBA,gl.UNSIGNED_BYTE,px);
            let h = 0; for (let i = 0; i < px.length; i += 101) h = (h * 31 + px[i]) | 0;
            return h; })()};
}"""
with sync_playwright() as pw:
    for url in sys.argv[1:]:
        br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
        pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
        errs = []
        pg.on("console", lambda m: errs.append((m.type, m.text[:120])))
        pg.goto(f"{url}/viewer/?job=2f7cf4eabf58")
        pg.wait_for_function("() => window.__ready === true", timeout=120000)
        pg.wait_for_timeout(500)
        print(url, json.dumps(pg.evaluate(JS), ensure_ascii=False))
        print("  logs:", errs[:4])
        br.close()
