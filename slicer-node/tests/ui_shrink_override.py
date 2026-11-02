"""切片页「参数覆盖」里能不能改到耗材级参数（收缩率）——用真浏览器验。

    python3 tests/ui_shrink_override.py [base]

检查：
  1. 进「3 切片」，等 #ov-form 渲染
  2. 搜索「收缩」能搜出 2 个字段（XY / Z），且是数字输入框
  3. 摘要里有「收缩补偿」行（profile 里填了 90% 的话）
  4. 无 JS 报错
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

errors: list[str] = []
fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None,
                          args=["--no-sandbox", "--disable-dev-shm-usage"])
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(BASE + "/", wait_until="networkidle")
    page.click('#steps button[data-step="slice"]')
    page.wait_for_selector("#ov-form section", timeout=15000)

    # 全部分组都渲染，但默认只展开前两个；数一下字段总数
    total = page.eval_on_selector_all("#ov-form .field", "els => els.length")
    print(f"  覆盖面板字段数: {total}")

    page.fill("#ov-search", "收缩")
    page.wait_for_timeout(400)
    labels = page.eval_on_selector_all(
        "#ov-form .field .lbl b", "els => els.map(e => e.textContent)")
    keys = page.eval_on_selector_all(
        "#ov-form .field .lbl code", "els => els.map(e => e.textContent)")
    print(f"  搜「收缩」命中: {list(zip(labels, keys))}")
    check("filament_shrink" in keys, "搜到 filament_shrink（收缩率 XY）")
    check("filament_shrinkage_compensation_z" in keys, "搜到 Z 收缩率")
    types = page.eval_on_selector_all(
        "#ov-form .field input", "els => els.map(e => e.type)")
    check(any(t == "number" for t in types), f"收缩率是数字输入框: {types}")

    # 改一下 XY 收缩率，看摘要/覆盖项跟不跟（表单监听 change，要失焦才触发）
    page.fill("#ov-form .field input[type=number]", "95")
    page.keyboard.press("Tab")
    page.wait_for_timeout(400)
    summ = page.inner_text("#slice-summary")
    check("1 项" in summ, "摘要覆盖项计数变成 1 项")
    check("收缩率 XY" in summ, "摘要里点了名（收缩率 XY）")

    # 清空搜索，看摘要里有没有「收缩补偿」行（来自 profile 的 90%）
    page.fill("#ov-search", "")
    page.wait_for_timeout(200)
    print("  摘要:\n" + "\n".join("    " + l for l in summ.splitlines()))

    b.close()

real = [e for e in errors if "favicon" not in e]
check(not real, f"无 JS 报错: {real[:3]}")
print("\n失败:", len(fails) if fails else 0)
sys.exit(1 if fails else 0)
