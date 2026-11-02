// schema 驱动的表单：一份配置里几百个参数，靠 /schema 的元数据自动选控件。
// 用它的地方有两处：配置编辑页、切片页的「参数覆盖」。

const UNIT_FIX = { "mm/s²": "mm/s²", "%": "%" };

export function isSimple(def) {
  const m = def.mode || "simple";
  return m === "simple" || m === "beginner";
}

// Orca 的 preset 里布尔存成字符串（"0"/"1"），而 Boolean("0") === true。
// 不解析就会把所有「关闭」的开关都渲染成勾选 + 开——用户以为开了，实际没开。
export function asBool(v) {
  if (v == null) return false;
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0;
  const s = String(v).trim().toLowerCase();
  return !(s === "" || s === "0" || s === "false" || s === "no" || s === "off" || s === "none");
}

// Orca 的向量型参数在 preset JSON 里经常被写成标量字符串（SimplyPrint 导出尤其多）：
// 比如 nozzle_temperature 存成 "210"、default_filament_colour 存成 "#00AE42"、
// filament_ramming_parameters 存成 "120 100 6.6 ..."。渲染前统一摊平成数组，
// 否则 `.join` 会 TypeError 直接打断整个表单。
function toArr(value) {
  if (value == null) return [];
  if (Array.isArray(value)) return value;
  if (typeof value === "string") {
    const s = value.trim();
    if (s === "") return [];
    return s.split(/[\s,]+/).filter(Boolean);
  }
  return [value];
}

function el(tag, cls, txt) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
}

function labelOf(def, key) {
  return def.zh || def.label || key;
}

function controlFor(key, def, value, onSet) {
  const t = def.t || "string";
  const vals = def.vals || [];
  const vlbl = def.vlbl_zh || def.vlbl || [];

  if (t === "bool") {
    const wrap = el("div", "ctl");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = asBool(value);
    cb.onchange = () => onSet(cb.checked);
    wrap.append(cb, el("span", "hint", cb.checked ? "开" : "关"));
    cb.addEventListener("change", () => { wrap.lastChild.textContent = cb.checked ? "开" : "关"; });
    return wrap;
  }

  if (t === "enum" && vals.length) {
    const wrap = el("div", "ctl");
    const sel = el("select");
    vals.forEach((v, i) => {
      const o = el("option", null, vlbl[i] || v);
      o.value = v;
      sel.append(o);
    });
    sel.value = String(value ?? vals[0]);
    sel.onchange = () => onSet(sel.value);
    wrap.append(sel);
    return wrap;
  }

  if (t === "int" || t === "float" || t === "percent") {
    const wrap = el("div", "ctl");
    const inp = el("input");
    inp.type = "number";
    inp.step = t === "int" ? "1" : (def.step || "0.01");
    if (def.min != null) inp.min = def.min;
    if (def.max != null) inp.max = def.max;
    inp.value = value ?? "";
    inp.onchange = () => {
      const raw = inp.value.trim();
      if (raw === "") return onSet(undefined);
      onSet(t === "int" ? parseInt(raw, 10) : parseFloat(raw));
    };
    wrap.append(inp);
    const unit = def.unit || (t === "percent" ? "%" : "");
    if (unit) wrap.append(el("span", "unit", unit));
    return wrap;
  }

  if (t === "float_or_percent") {
    const wrap = el("div", "ctl");
    const inp = el("input");
    inp.value = value ?? "";
    inp.placeholder = "数值或 50%";
    inp.onchange = () => {
      const raw = inp.value.trim();
      if (raw === "") return onSet(undefined);
      onSet(/%$/.test(raw) ? raw : parseFloat(raw));
    };
    wrap.append(inp, el("span", "unit", def.unit || "mm / %"));
    return wrap;
  }

  // percents 是「向量百分比」（Orca 里按挤出机存成 ["100%", "98%"]），但耗材级的
  // 收缩率一个耗材只有一个值，用逗号 textarea 填实在太难用了 —— 单值时给数字输入框。
  // 回写时保持原形态：原来是数组还数组，原来是标量字符串还标量字符串。
  if (t === "percents") {
    const arr = toArr(value);
    if (arr.length <= 1) {
      const wrap = el("div", "ctl");
      const inp = el("input");
      inp.type = "number";
      inp.step = def.step || "0.1";
      if (def.min != null) inp.min = def.min;
      if (def.max != null) inp.max = def.max;
      inp.value = arr.length ? String(arr[0]).replace(/%/g, "").trim()
        : (def.def != null ? String(def.def) : "");
      inp.onchange = () => {
        const raw = inp.value.trim();
        if (raw === "") return onSet(undefined);
        const n = parseFloat(raw);
        if (!Number.isFinite(n)) return;
        const p = String(n) + "%";
        onSet(Array.isArray(value) ? [p] : (typeof value === "string" ? p : [p]));
      };
      wrap.append(inp, el("span", "unit", def.unit || "%"));
      if (def.min != null || def.max != null) {
        wrap.append(el("span", "hint", `${def.min ?? ""}~${def.max ?? ""}，100 = 不补偿`));
      }
      return wrap;
    }
  }

  if (t === "strings" || t === "enums" || t === "ints" || t === "floats"
      || t === "bools" || t === "points" || t === "percents" || t === "points3") {
    const arr = toArr(value);
    const ta = el("textarea");
    ta.rows = Math.min(6, Math.max(2, arr.length || 1));
    if (t === "points" || t === "points3") {
      ta.value = arr.map(p => Array.isArray(p) ? p.join(",") : p).join("\n");
      ta.placeholder = "每行一个点：x,y";
    } else if (t === "floats" || t === "ints" || t === "percents") {
      ta.value = arr.join(", ");
      ta.placeholder = "逗号分隔（多挤出机按顺序）";
    } else if (t === "bools") {
      ta.value = arr.map(v => (v && v !== "0" && v !== "false" && v !== 0) ? 1 : 0).join(", ");
    } else {
      ta.value = arr.join("\n");
    }
    ta.onchange = () => {
      const raw = ta.value.trim();
      if (!raw) return onSet(undefined);
      if (t === "points" || t === "points3") {
        onSet(raw.split("\n").map(l => l.split(/[\s,]+/).filter(Boolean).map(Number)));
      } else if (t === "strings" || t === "enums") {
        onSet(raw.split("\n").map(s => s.trim()).filter(Boolean));
      } else if (t === "bools") {
        onSet(raw.split(/[\s,]+/).filter(Boolean).map(v => v !== "0" && v !== "false"));
      } else if (t === "ints") {
        onSet(raw.split(/[\s,]+/).filter(Boolean).map(v => parseInt(v, 10)));
      } else if (t === "percents") {
        onSet(raw.split(/[\s,]+/).filter(Boolean).map(v => v.endsWith("%") ? v : String(v) + "%"));
      } else {
        onSet(raw.split(/[\s,]+/).filter(Boolean).map(Number));
      }
    };
    return ta;
  }

  // string / 未知类型
  const isGcode = /gcode|_g$/.test(key) || (typeof value === "string" && value.includes("\n"));
  if (isGcode) {
    const ta = el("textarea");
    ta.rows = 4;
    ta.value = value ?? "";
    ta.onchange = () => onSet(ta.value === "" ? undefined : ta.value);
    return ta;
  }
  const wrap = el("div", "ctl");
  const inp = el("input");
  inp.value = value ?? "";
  inp.onchange = () => onSet(inp.value === "" ? undefined : inp.value);
  wrap.append(inp);
  if (def.unit) wrap.append(el("span", "unit", def.unit));
  return wrap;
}

/**
 * @param container 目标容器
 * @param data      {groups:[{cat,keys}], params:{key:def}}
 * @param doc       当前值对象（会被就地修改）
 * @param opts      {mode, search, onEdit(key, value)}
 */
export function renderForm(container, data, doc, opts) {
  const mode = opts.mode || "simple";
  const q = (opts.search || "").trim().toLowerCase();
  container.textContent = "";
  let shown = 0;

  for (const g of data.groups || []) {
    const keys = g.keys.filter(k => {
      const d = data.params[k] || {};
      if (d.t === "meta" && !(opts.showMeta)) return false;
      if (mode === "simple" && !isSimple(d)) return false;
      if (!q) return true;
      const hay = (k + " " + (d.zh || "") + " " + (d.label || "") + " " + (d.tip_zh || d.tip || "")).toLowerCase();
      return hay.includes(q);
    });
    if (!keys.length) continue;

    const sec = el("section", "grp" + (q ? " open" : (shown < 2 ? " open" : "")));
    const h = el("h3");
    h.append(el("span", null, g.cat), el("span", "n", keys.length + " 项"));
    h.onclick = () => sec.classList.toggle("open");
    const body = el("div", "body");

    for (const k of keys) {
      const def = data.params[k] || {};
      const row = el("div", "field");
      const lbl = el("div", "lbl");
      const b = el("b", null, labelOf(def, k));
      lbl.append(b, el("code", null, k));
      const box = el("div");
      const ctl = controlFor(k, def, doc[k], (v) => {
        if (v === undefined) delete doc[k];
        else doc[k] = v;
        row.classList.add("changed");
        opts.onEdit && opts.onEdit(k, v);
      });
      box.append(ctl);
      const tip = def.tip_zh || def.tip;
      if (tip) box.append(el("div", "tip clip", tip));
      row.append(lbl, box);
      body.append(row);
      shown++;
    }
    sec.append(h, body);
    container.append(sec);
  }

  if (!shown) {
    container.append(el("div", "hint", q ? "没匹配到参数" : "这个视图下没有可改的参数，切到「全部」看看"));
  }
  return shown;
}
