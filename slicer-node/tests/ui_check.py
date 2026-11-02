"""预览页自检（headless Chromium + SwiftShader，无需人眼）。

    python3 tests/ui_check.py http://127.0.0.1:8020 <job-id>

检查项：
  1. 无 JS 报错、底栏/图例数据齐全
  2. 四种窗口尺寸下，顶栏/层条/图例/底栏不溢出、不重叠
  3. 画面里真的出现了调色板颜色（几何画出来了，不是空屏）
  4. 图例开关是本地的（切换不发请求）且真的少画了该类
"""
from __future__ import annotations

PAGE = __import__("os").environ.get("UI_PAGE", "")
import os
import sys

from playwright.sync_api import sync_playwright

CHROME = os.environ.get(
    "PW_CHROME",
    "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/"
    "chromium-1248/chrome-linux-arm64/chrome")
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]

PALETTE = {
    "outer_wall": (255, 125, 56), "inner_wall": (255, 230, 77),
    "sparse_infill": (176, 48, 41), "solid_infill": (150, 84, 204),
    "support": (0, 255, 0), "bridge": (77, 128, 186),
    "top_surface": (240, 64, 64), "other": (230, 179, 179),
}

SAMPLE_PX = """() => {
  const c = document.querySelector('canvas#gl');
  const gl = c.getContext('webgl2');
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  draw();
  const w = c.width, h = c.height;
  const px = new Uint8Array(w * h * 4);
  gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const counts = {};
  let tot = 0;
  for (let i = 0; i < px.length; i += 4 * 5) {
    tot++;
    const r = px[i], g = px[i + 1], b = px[i + 2];
    if (r < 60 && g < 60 && b < 60) continue;      // 背景/网格
    counts[r + ',' + g + ',' + b] = (counts[r + ',' + g + ',' + b] || 0) + 1;
  }
  return {tot, counts};
}"""

LAYOUT = """() => {
  const ids = ['topbar','layercol','legend','bottombar'];
  const vw = innerWidth, vh = innerHeight;
  const rect = el => { const r = el.getBoundingClientRect();
    return {x: r.x, y: r.y, w: r.width, h: r.height}; };
  const rs = {};
  const issues = [];
  for (const id of ids) {
    const r = rect(document.getElementById(id));
    rs[id] = r;
    if (r.x < -1 || r.y < -1 || r.x + r.w > vw + 1 || r.y + r.h > vh + 1)
      issues.push(id + ' 溢出视口');
  }
  for (let i = 0; i < ids.length; i++)
    for (let j = i + 1; j < ids.length; j++) {
      const a = rs[ids[i]], b = rs[ids[j]];
      const hit = !(a.x + a.w <= b.x || b.x + b.w <= a.x ||
                    a.y + a.h <= b.y || b.y + b.h <= a.y);
      if (hit) issues.push(ids[i] + ' 与 ' + ids[j] + ' 重叠');
    }
  return issues;
}"""


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8020"
    job = sys.argv[2]
    global PAGE
    url = f"{base}/viewer/{PAGE}?job={job}"
    fails = []

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True, args=ARGS)
        pg = b.new_page(viewport={"width": 960, "height": 640}, device_scale_factor=1)
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(url, wait_until="load")
        pg.wait_for_timeout(8000)

        hud = pg.evaluate("""() => ({
            title: document.getElementById('title').textContent,
            bar: document.getElementById('bottombar').innerText.replace(/\\s+/g,' '),
            rows: document.querySelectorAll('#lgtbl tr').length,
            hint: document.getElementById('hint').textContent})""")
        print("HUD:", hud["title"], "|", hud["bar"])
        if hud["rows"] < 2:
            fails.append("图例为空")
        if "—" in hud["bar"]:
            fails.append("底栏有未填字段")

        # 画面覆盖。
        # 注意：最顶层往往只有一个尖端（快艇的天线、宝塔的塔尖），
        # 单层模式要再多看一个中间层，否则会把「正常」误判成「空屏」。
        # 体积管渲染会给每个像素加明暗（法线光照 + 朝下压暗），
        # 所以不能再按 RGB 绝对值比调色板，改比「色度」：除以最亮通道后再比。
        def _chroma(c):
            m = max(c) or 1
            return c[0] / m, c[1] / m, c[2] / m

        PAL_C = {name: _chroma(c) for name, c in PALETTE.items()}

        def coverage():
            r = pg.evaluate(SAMPLE_PX)
            hits = 0
            for rgb, n in r["counts"].items():
                parts = tuple(int(v) for v in rgb.split(","))
                if max(parts) < 55:
                    continue                      # 太暗（背景/暗部），不参与
                cc = _chroma(parts)
                for c in PAL_C.values():
                    if all(abs(cc[k] - c[k]) <= 0.11 for k in range(3)):
                        hits += n
                        break
            return hits / max(r["tot"], 1) * 100

        single_cov = []
        for mode in ("single", "accum", "all"):
            pg.click(f'button[data-mode="{mode}"]')
            pg.wait_for_timeout(6000 if mode != "all" else 15000)
            if mode == "single":
                single_cov.append(coverage())
                pg.evaluate("() => setLayer(Math.floor(index.layer_count * 0.5))")
                pg.wait_for_timeout(3000)
                single_cov.append(coverage())
                print(f"  single 调色板覆盖 {single_cov[0]:5.2f}% (顶) / "
                      f"{single_cov[1]:5.2f}% (中)")
                if max(single_cov) < 0.3:
                    fails.append("single 模式几乎没画出东西")
                continue
            pct = coverage()
            print(f"  {mode:6s} 调色板覆盖 {pct:5.2f}%")
            if pct < 0.3:
                fails.append(f"{mode} 模式几乎没画出东西")

        # 类型开关：本地过滤（不发请求）+ 真的少画
        before = pg.evaluate("() => performance.getEntriesByType('resource').length")
        pg.evaluate("""() => {
            const row = [...document.querySelectorAll('#lgtbl tr.row')]
                .find(r => r.innerText.startsWith('内墙'));
            if (row) row.click();
        }""")
        pg.wait_for_timeout(1500)
        after = pg.evaluate("() => performance.getEntriesByType('resource').length")
        r = pg.evaluate(SAMPLE_PX)
        yellow = r["counts"].get("255,230,77", 0)
        print(f"  关掉内墙：新增请求 {after - before}，黄色像素 {yellow}")
        if after - before:
            fails.append("切换类型竟然发了请求")
        if yellow > r["tot"] * 0.001:
            fails.append("关掉内墙后仍画出了内墙")

        # 布局
        for w, h in ((960, 640), (390, 780), (360, 640), (1280, 800)):
            pg.set_viewport_size({"width": w, "height": h})
            pg.wait_for_timeout(900)
            issues = pg.evaluate(LAYOUT)
            print(f"  {w}x{h}: {'OK' if not issues else issues}")
            fails.extend(f"{w}x{h} {i}" for i in issues)

        if errs:
            fails.append("JS 报错: " + "; ".join(errs[:3]))
        b.close()

    print("UI CHECK", "OK" if not fails else "FAIL: " + "; ".join(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
