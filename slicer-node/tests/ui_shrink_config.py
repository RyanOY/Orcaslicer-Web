"""配置页 → 耗材 → 收缩率分组是否可见可改（真实浏览器）。

    python3 tests/ui_shrink_config.py [base]
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8039"
CHROME = os.environ.get(
    "PW_CHROME",
    "/vol1/@appconf/com.dustinky.qwenpaw/.cache/ms-playwright/"
    "chromium-1248/chrome-linux-arm64/chrome")

fails: list[str] = []
errors: list[str] = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None,
                          args=["--no-sandbox", "--disable-dev-shm-usage"])
    page = b.new_page(viewport={"width": 1400, "height": 950})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/", wait_until="networkidle")

    page.click('button[data-step="config"]')
    page.wait_for_timeout(800)
    page.click('#cfg-tabs button[data-kind="filament"]')
    page.wait_for_timeout(600)
    page.locator("#cfg-items > *").first.click()
    page.wait_for_timeout(1500)

    page.fill("#cfg-search", "收缩")
    page.wait_for_timeout(600)
    labels = page.eval_on_selector_all(".cfg-body .field .lbl b",
                                       "e => e.map(x => x.textContent)") or \
        page.eval_on_selector_all(".field .lbl b", "e => e.map(x => x.textContent)")
    keys = page.eval_on_selector_all(".cfg-body .field .lbl code",
                                     "e => e.map(x => x.textContent)") or \
        page.eval_on_selector_all(".field .lbl code", "e => e.map(x => x.textContent)")
    types = page.eval_on_selector_all(".cfg-body .field input",
                                      "e => e.map(x => x.type)") or \
        page.eval_on_selector_all(".cfg-body .field input", "e => e.map(x => x.type)")
    vals = page.eval_on_selector_all(".cfg-body .field input",
                                     "e => e.map(x => x.value)") or \
        page.eval_on_selector_all(".cfg-body .field input", "e => e.map(x => x.value)")
    print("  命中:", list(zip(labels, keys)), types, vals)
    check("filament_shrink" in keys, "配置页搜到收缩率 XY")
    check("filament_shrinkage_compensation_z" in keys, "配置页搜到收缩率 Z")
    check(types.count("number") >= 2, "两个都是数字框")

    b.close()

real = [e for e in errors if "favicon" not in e]
check(not real, f"无 JS 报错: {real[:3]}")
print("\n失败:", len(fails))
sys.exit(1 if fails else 0)
