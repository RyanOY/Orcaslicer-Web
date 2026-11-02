"""摆盘页的检查要按「切片后的尺寸」算：加一个对象，切到 90% 收缩的耗材，看警告。

    python3 tests/ui_plate_shrink.py [base]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8039"
CHROME = os.environ.get(
    "PW_CHROME",
    "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/"
    "chromium-1248/chrome-linux-arm64/chrome")
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--no-sandbox", "--disable-dev-shm-usage"]

TMP = "zz-shrink-ui"


def call(path, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


src = dict(call("/profiles/filament/generic-pla")["doc"])
src["filament_shrink"] = "90%"
src["filament_shrinkage_compensation_z"] = "90%"
call("/profiles/filament/" + TMP, {"doc": src}, method="PUT")

fails: list[str] = []
errors: list[str] = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None,
                              args=ARGS)
        page = b.new_page(viewport={"width": 1400, "height": 950})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/", wait_until="networkidle")

        # 加一个模型到摆盘
        page.click("#models button.primary")
        page.wait_for_timeout(1200)
        n = page.eval_on_selector_all("#objs > *", "e => e.length")
        check(n > 0, f"摆盘里有 {n} 个对象")

        page.click('#steps button[data-step="plate"]')
        page.wait_for_timeout(1500)
        ok0 = page.inner_text("#plate-ok")
        w0 = page.inner_text("#plate-warns")
        print("  100% 耗材:", (ok0 or w0).strip().splitlines()[:2])

        # 换成 90% 收缩的耗材（#filaments 在摆盘/切片页的侧栏里）
        page.click('#steps button[data-step="plate"]')
        page.wait_for_timeout(400)
        sels = page.locator("#filaments select")
        for i in range(sels.count()):
            try:
                sels.nth(i).select_option(TMP)
            except Exception as e:
                print("  槽位 %d 切换失败: %s" % (i, e))
        page.wait_for_timeout(2000)
        ok1 = page.inner_text("#plate-ok")
        w1 = page.inner_text("#plate-warns")
        print("  90% 耗材:", (w1 or ok1).strip().splitlines()[:3])
        check("收缩率 XY 90%" in w1, "摆盘检查提示了 90% 收缩")
        # 放大后是否真的报出界，取决于对象大小；这里只要求不崩、有提示

        b.close()

    real = [e for e in errors if "favicon" not in e]
    check(not real, f"无 JS 报错: {real[:3]}")
finally:
    call("/profiles/filament/" + TMP, method="DELETE")
    print("临时耗材已删除")

print("\n失败:", len(fails))
sys.exit(1 if fails else 0)
