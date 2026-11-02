#!/usr/bin/env python3
"""折线顶点缓冲自检：NaN、半线宽范围、以及「拐点两侧读到同一组数」。"""
import sys, json
from playwright.sync_api import sync_playwright
CHROME = "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/chromium-1248/chrome-linux-arm64/chrome"
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]
JS = """() => {
  mode = 'single'; cur = index.layer_count - 1; cam.el = 0.62;
  draw(); draw();
  const a = ribbonArr, n = window.__ribbon ? window.__ribbon.n : 0, F = RIB_FLOATS;
  let nan = 0, tiny = 0, hwMin = 1e9, hwMax = -1e9;
  for (let i = 0; i < n * F; i++) if (!isFinite(a[i])) nan++;
  for (let i = 0; i < n; i++) {
    const hw = a[i * F + 8];
    const v = Math.abs(hw);
    if (v < hwMin) hwMin = v;
    if (v > hwMax) hwMax = v;
    if (v < 0.02) tiny++;
  }
  /* 拐点一致性：每 6 个顶点一段四边形，v2/v3 是终点，v4/v5 是起点那一对；
     相邻两段的公共端点应该拿到同样的 (角度, 半线宽) */
  let joinBad = 0, joins = 0;
  for (let k = 0; k + F * 12 <= n * F; k += F * 6) {
    const e = k + F * 2;                                    // 本段终点顶点 v2
    const s2 = k + F * 6;                                   // 下一段起点顶点 v0
    const x1 = a[e], y1 = a[e + 1], x2 = a[s2], y2 = a[s2 + 1];
    if (Math.abs(x1 - x2) > 1e-4 || Math.abs(y1 - y2) > 1e-4) continue;  // 不是同一折线
    joins++;
    if (Math.abs(a[e + 3] - a[s2 + 3]) > 1e-6 ||
        Math.abs(Math.abs(a[e + 8]) - Math.abs(a[s2 + 8])) > 1e-6) joinBad++;
  }
  return {n, nan, tiny, hwMin: +hwMin.toFixed(3), hwMax: +hwMax.toFixed(3), joins, joinBad};
}"""
with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path=CHROME, args=ARGS)
    pg = br.new_page(viewport={"width": 900, "height": 760}, device_scale_factor=1)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(f"{sys.argv[1]}/viewer/?job={sys.argv[2]}")
    pg.wait_for_function("() => window.__ready === true", timeout=120000)
    pg.wait_for_timeout(600)
    print(json.dumps(pg.evaluate(JS), ensure_ascii=False))
    print("errs", errs[:3])
    br.close()
