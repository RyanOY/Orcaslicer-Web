"""老任务（v4 索引：没有属性列）在新前端下必须照样能看。

任务列表只在进程内存里，重启后的实例认不出旧任务，所以这里用 Playwright 的
请求拦截，把磁盘上旧任务的 preview 目录「假装」成一个能访问的任务来测前端的降级路径。

    python3 tests/ui_backward.py <base-url> <旧任务 preview 目录>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROME = os.environ.get(
    "PW_CHROME",
    "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/"
    "chromium-1248/chrome-linux-arm64/chrome")
FAKE = "deadbeef0000"


def main() -> int:
    base = sys.argv[1]
    src = Path(sys.argv[2])
    index = json.loads((src / "index.json").read_text(encoding="utf-8"))
    fails: list[str] = []

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--use-gl=angle", "--use-angle=swiftshader",
                                    "--enable-unsafe-swiftshader", "--no-sandbox"])
        pg = b.new_page(viewport={"width": 900, "height": 620})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        def handler(route):
            url = route.request.url
            tail = url.split(f"/jobs/{FAKE}/preview")[-1].split("?")[0]
            if tail in ("", "/"):
                return route.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(index))
            i = int(tail.strip("/"))
            ly = index["layers"][i]
            n = ly["count"]
            stride = 20 if int(index.get("version", 0)) >= 5 else 18
            with open(src / "segments.bin", "rb") as f:
                f.seek(ly["offset"])
                body = f.read(n * stride)
            return route.fulfill(status=200, content_type="application/octet-stream",
                                 body=body,
                                 headers={"X-Segments": str(n), "X-Z": str(ly["z"])})

        pg.route(f"**/jobs/{FAKE}/preview**", handler)
        pg.goto(f"{base}/viewer/?job={FAKE}", wait_until="load")
        pg.wait_for_timeout(9000)

        info = pg.evaluate("""() => ({
            ver: index.version,
            opts: [...document.querySelectorAll('#scheme option')]
                  .map(o => o.textContent),
            gray: [...document.querySelectorAll('#scheme option')]
                  .map(o => o.disabled),
            rows: document.querySelectorAll('#lgtbl tr').length,
            lit: (() => {
                const c = document.querySelector('canvas#gl');
                const gl = c.getContext('webgl2');
                gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); draw();
                const px = new Uint8Array(c.width * c.height * 4);
                gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
                let k = 0;
                for (let i = 0; i < px.length; i += 4 * 3)
                    if (!(px[i] < 55 && px[i+1] < 55 && px[i+2] < 55)) k++;
                return k;
            })(),
        })""")
        print("老任务索引版本", info["ver"], "| 图例行", info["rows"], "| 亮像素样本", info["lit"])
        print("着色依据：", " / ".join(f"{o}{'[灰]' if g else ''}"
                                        for o, g in zip(info["opts"], info["gray"])))
        if info["lit"] < 150:
            fails.append("老任务画不出来")
        if info["rows"] < 2:
            fails.append("老任务图例为空")
        if not all(info["gray"][1:]):
            fails.append("老任务里属性项应该全部置灰")
        if errs:
            fails.append("JS 报错: " + "; ".join(errs[:2]))
        pg.screenshot(path="/tmp/backward.png")
        b.close()

    print("BACKWARD", "OK" if not fails else "FAIL: " + "; ".join(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
