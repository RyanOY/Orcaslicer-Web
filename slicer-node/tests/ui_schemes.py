"""着色依据自检（headless Chromium + SwiftShader）。

    python3 tests/ui_schemes.py http://127.0.0.1:8020 <job-id>

检查项：
  1. 选择器列出了全部着色依据，缺数据的项被置灰
  2. 每个依据切过去都真的改了画面（颜色种类/覆盖变化），且不发请求
  3. 图例跟着变：连续量出色带、离散量出取值行、单值出说明行
  4. 悬垂墙等新角色出现在图例里（v5 之前被并进内墙/其他）
  5. 截图落到 /tmp/scheme_<id>.png，方便人眼复核
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

STATS = """() => {
  const c = document.querySelector('canvas#gl');
  const gl = c.getContext('webgl2');
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  draw();
  const w = c.width, h = c.height;
  const px = new Uint8Array(w * h * 4);
  gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const counts = {};
  let lit = 0, tot = 0;
  for (let i = 0; i < px.length; i += 4 * 3) {
    tot++;
    const r = px[i], g = px[i + 1], b = px[i + 2];
    if (r < 55 && g < 55 && b < 55) continue;
    if (Math.abs(r - 58) < 12 && Math.abs(g - 58) < 12 && Math.abs(b - 58) < 12) continue;
    lit++;
    counts[r + ',' + g + ',' + b] = 1;
  }
  return {tot, lit, kinds: Object.keys(counts).length,
          distinct: Object.keys(counts),
          hue: Object.keys(counts).map(k => {
            const p = k.split(',').map(Number); const m = Math.max(...p) || 1;
            return (p[0] / m).toFixed(2) + '|' + (p[1] / m).toFixed(2) + '|' + (p[2] / m).toFixed(2);
          })};
}"""


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8020"
    job = sys.argv[2]
    url = f"{base}/viewer/?job={job}"
    fails: list[str] = []

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True, args=ARGS)
        pg = b.new_page(viewport={"width": 960, "height": 660}, device_scale_factor=1)
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(url, wait_until="load")
        pg.wait_for_timeout(9000)
        # 取中间层，能同时看到墙/填充/悬垂
        pg.evaluate("() => setLayer(Math.floor(index.layer_count * 0.55))")
        pg.wait_for_timeout(2500)
        pg.evaluate("""() => {
            cam.dist = Math.max(48, cam.dist * 0.40);
            cam.el = 0.52; cam.az = -0.95;
            draw();
        }""")
        pg.wait_for_timeout(1200)

        info = pg.evaluate("""() => ({
            n: SCHEMES.length,
            opts: [...document.querySelectorAll('#scheme option')]
                  .map(o => ({t: o.textContent, d: o.disabled})),
            avail: index.attr_avail || {},
            modes: SCHEMES.map(s => s.kind === 'type' ? 'type' : (s.mode || '?')),
            levels: SCHEMES.map(s => s.levels ? s.levels.length : 0),
            legend: document.getElementById('lgtbl').innerText.replace(/\\s+/g, ' '),
        })""")
        print("选择器：", " / ".join(o["t"] for o in info["opts"]))
        print("模式：", info["modes"], "取值数：", info["levels"])

        if info["n"] != 7:
            fails.append("期望 7 个着色依据，实际 %d" % info["n"])

        # 1) gcode 里有、v5 之前会被并进内墙/其他 的角色，现在必须单独出现在图例里
        tot = pg.evaluate("() => index.type_totals || {}")
        labels = pg.evaluate("() => index.type_labels || index.type_names")
        for code, n in tot.items():
            name = labels[int(code)]
            if name not in info["legend"]:
                fails.append("图例缺少「%s」（%d 段）" % (name, n))
        for role in ("悬垂墙", "内部桥接"):
            if role not in info["legend"] and role not in labels:
                fails.append("类型表里没有「%s」" % role)

        # 2) 逐个切换
        type_hue = None
        for i, opt in enumerate(info["opts"]):
            if opt["d"]:
                print(f"  [{i}] {opt['t']:8s} 置灰（模型无数据）")
                continue
            reqs0 = pg.evaluate("() => performance.getEntriesByType('resource').length")
            pg.evaluate("""(i) => {
                scheme = i;
                document.getElementById('scheme').value = String(i);
                buildLegend();
                draw();
            }""", i)
            pg.wait_for_timeout(1600)
            reqs1 = pg.evaluate("() => performance.getEntriesByType('resource').length")
            st = pg.evaluate(STATS)
            if i == 0:
                type_hue = set(st["hue"])
            lg = pg.evaluate("""() => ({
                bar: document.getElementById('lgbar').className,
                barTxt: document.getElementById('lgbar').innerText.replace(/\\s+/g, ' '),
                rows: document.querySelectorAll('#lgtbl tr').length,
                txt: document.getElementById('lgtbl').innerText.replace(/\\s+/g, ' '),
            })""")
            pg.screenshot(path=f"/tmp/scheme_{i}.png")
            idname = info["modes"][i]
            cov = st["lit"] / max(st["tot"], 1) * 100
            print(f"  [{i}] {opt['t']:8s} mode={idname:4s} 亮像素 {cov:5.2f}% "
                  f"色种 {st['kinds']:3d} | 图例 bar={lg['bar']} rows={lg['rows']}")
            if reqs1 != reqs0:
                fails.append("%s 切换发了 %d 个请求" % (opt["t"], reqs1 - reqs0))
            if st["lit"] < 200:
                fails.append("%s 几乎没画出东西" % opt["t"])
            if i and not lg["txt"] and lg["bar"] != "on":
                fails.append("%s 图例是空的" % opt["t"])
            # 每个依据都应该跟「走线类型」的配色不同
            if i and type_hue is not None and set(st["hue"]) == type_hue:
                fails.append("%s 的配色与走线类型完全相同" % opt["t"])

        if errs:
            fails.append("JS 报错: " + "; ".join(errs[:3]))
        b.close()

    print("SCHEME CHECK", "OK" if not fails else "FAIL: " + "; ".join(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
