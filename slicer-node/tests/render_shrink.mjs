// 无浏览器环境下验证「配置编辑页」真的把收缩率渲染出来了。
// 用最小 DOM 垫片跑 renderForm，喂 /profiles/filament/jayo-pla 的真实返回。
// 跑法：node tests/render_shrink.mjs
import { renderForm } from "../web/form.js";

class Node_ {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.className = ""; this.style = {};
    this.dataset = {}; this._text = ""; this.value = "";
    this.classList = {
      add: (c) => { this.className += " " + c; },
      remove: (c) => { this.className = this.className.replace(c, ""); },
      toggle: () => {},
    };
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = v; if (v === "") this.children = []; }
  append(...n) { this.children.push(...n); }
  appendChild(n) { this.children.push(n); }
  addEventListener() {}
  querySelector() { return null; }
  closest() { return null; }
}

globalThis.document = {
  createElement: (tag) => new Node_(tag),
  activeElement: null,
};

function walk(n, fn) { fn(n); for (const c of n.children || []) walk(c, fn); }

const data = await (await fetch("http://127.0.0.1:8039/profiles/filament/jayo-pla")).json();
const doc = { ...data.doc };
const root = new Node_("div");
const shown = renderForm(root, data, doc, { mode: "simple", search: "" });
console.log("渲染字段数:", shown);

let sect = null;
walk(root, (n) => {
  const t = n.children[0]?.children?.[0]?.textContent || "";
  if (n.className.includes("grp") && t.includes("收缩率")) sect = n;
});
if (!sect) { console.error("FAIL: 简单视图里没有「收缩率」分组"); process.exit(1); }

const rows = sect.children[1].children;
console.log("收缩率分组字段:", rows.map((r) => r.children[0].children[0]._text));
const inputs = [];
walk(sect, (n) => { if (n.tagName === "input") inputs.push(n); });
console.log("控件:", inputs.map((i) => `${i.type}=${i.value}${i.min ? " min" + i.min : ""}`));
if (inputs.length !== 2) { console.error("FAIL: 应该有 2 个数字输入框"); process.exit(1); }
if (inputs.some((i) => i.type !== "number")) { console.error("FAIL: 应该是 number 输入框"); process.exit(1); }

// 模拟用户把 XY 改成 92%：确认写回 doc 的是数组形态 ["92%"]
inputs[0].value = "92";
inputs[0].onchange();
console.log("改完后 doc:", doc.filament_shrink, doc.filament_shrinkage_compensation_z);
if (JSON.stringify(doc.filament_shrink) !== '["92%"]') { console.error("FAIL: XY 写回形态不对"); process.exit(1); }
inputs[1].value = "97.5";
inputs[1].onchange();
console.log("Z 写回:", doc.filament_shrinkage_compensation_z);
if (doc.filament_shrinkage_compensation_z !== "97.5%") { console.error("FAIL: Z 写回形态不对"); process.exit(1); }

console.log("渲染与回写通过");
