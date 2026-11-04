import { renderForm, asBool } from "./form.js";
import { createPlate3D, PALETTE } from "./plate3d.js";

const $ = (s) => document.querySelector(s);
const LS = "orcaweb.v1";

const state = {
  health: null,
  models: [],
  profiles: { machine: [], process: [], filament: [] },
  plate: { objects: [], machine: "", process: "", filaments: [], bed: null },
  overrides: {},
  // 热床板类型：Orca 里热床温度不是「直接给一个数」，而是按 curr_bed_type 去耗材配置里
  // 取对应那一栏（cool/hot/textured/eng/supertack_plate_temp）。默认 Cool Plate = 35℃，
  // 这就是「设了 48℃ 却切出 35℃」的根因。
  bedType: "Cool Plate",
  filManual: {},   // 用户手选的耗材槽：别被料盘绑定覆盖回去
  rotAxis: "z",    // 「旋转」作用的轴：x / y / z（工具栏 X/Y/Z 分段按钮）
  sliceDoc: null,
  sliceData: null,
  slotShrink: null,   // 每个耗材槽的收缩率（loadSliceSchema 里填，摘要要显示）
  sel: -1,
  job: null,
  cfg: { kind: "machine", name: null, data: null, doc: null, dirty: false, mode: "simple" },
  mode: "simple",
  view: "models",
};

// ---------------------------------------------------------------- 小工具

function el(tag, cls, txt) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
}

// #cfg-empty 一开始住在 #cfg-body 里，但 renderForm 会清空 #cfg-body（顺带把它删掉），
// 之后再 openProfile/deleteCfg 就会拿到 null。这里保证它需要时一定存在。
function cfgEmpty() {
  let e = $("#cfg-empty");
  if (!e) {
    e = el("div", "cfg-empty");
    e.id = "cfg-empty";
    e.textContent = "从左边选一份配置开始编辑";
    $("#cfg-body").append(e);
  }
  return e;
}

let toastTimer = 0;
function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast on" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, isErr ? 5200 : 2600);
}

async function api(path, { method = "GET", body, form } = {}) {
  // 兜底：带了 body 但没写 method 的调用，一律按 POST 处理（GET 不能带 body）。
  if (method === "GET" && (body !== undefined || form)) method = "POST";
  const opt = { method, headers: {} };
  if (form) opt.body = form;
  else if (body !== undefined) {
    opt.body = JSON.stringify(body);
    opt.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, opt);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!r.ok) {
    const detail = (data && (data.detail || data.raw)) || r.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function fmtBytes(n) {
  if (!n) return "0 B";
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  if (n > 1024) return (n / 1024).toFixed(0) + " KB";
  return n + " B";
}

function fmtTime(sec) {
  if (!sec && sec !== 0) return "—";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h) return `${h}小时${m}分`;
  if (m) return `${m}分${s % 60}秒`;
  return `${s}秒`;
}

function save() {
  try {
    localStorage.setItem(LS, JSON.stringify({
      plate: state.plate, overrides: state.overrides, jobId: state.job?.id || null,
      bedType: state.bedType, filManual: state.filManual,
    }));
  } catch { /* 存不下就算了 */ }
}

function restore() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS) || "{}");
    if (raw.plate) {
      state.plate = { ...state.plate, ...raw.plate };
      state.plate.objects = (state.plate.objects || []).filter(o => o && o.file);
    }
    if (raw.overrides) state.overrides = raw.overrides;
    if (raw.bedType) state.bedType = raw.bedType;
    if (raw.filManual) state.filManual = raw.filManual;
    if (raw.jobId) state.job = { id: raw.jobId, status: "unknown" };
  } catch { /* ignore */ }
}

// ---------------------------------------------------------------- 路由

const STEPS = ["models", "plate", "slice", "preview", "print", "spool", "config"];

function go(step) {
  state.view = step;
  STEPS.forEach(s => {
    document.querySelector(`#view-${s}`)?.classList.toggle("on", s === step);
    document.querySelector(`nav.steps button[data-step="${s}"]`)?.classList.toggle("on", s === step);
  });
  if (step === "print") startLive();
  else stopLive();
  if (step === "spool") renderSpool();
  if (step === "plate") {
    // 切过来的这一帧布局可能还没结算（画布 clientWidth 为 0），首帧会画不出来，
    // 所以立刻画一次、下一帧再画一次、150ms 兜底再画一次。
    plate.refresh();
    requestAnimationFrame(() => plate.refresh());
    setTimeout(() => { if (state.view === "plate") plate.refresh(); }, 150);
  }
  if (step === "slice") renderSlice();
  if (step === "preview") renderPreview();
  if (step === "print") renderPrint();
  if (step === "config") renderCfgList();
  document.querySelectorAll("nav.steps button").forEach(b => {
    const i = STEPS.indexOf(b.dataset.step), j = STEPS.indexOf(step);
    b.classList.toggle("done", i >= 0 && j >= 0 && i < j);
  });
}

// ---------------------------------------------------------------- 模型库

async function loadHealth() {
  try {
    const h = await api("/health");
    state.health = h;
    $("#health").textContent = `${h.orca || "orca?"} · 队列 ${h.queue.running}/${h.queue.concurrency}`;
  } catch (e) {
    $("#health").textContent = "服务未连接";
  }
}

async function loadModels() {
  const r = await api("/models");
  state.models = r.models || [];
  $("#models-path").textContent = r.dir || "";
  $("#model-dir").textContent = (r.dir || "") + (r.extra_dirs?.length ? "  +" + r.extra_dirs.join(" ") : "");
  renderModels();
  // 缩略图要轮廓：没有缓存的补一次；3MF 可能含多个部件，全部补一次拿 parts。
  const missing = state.models.filter(m => !m.hull || /\.3mf$/i.test(m.file));
  for (const m of missing.slice(0, 24)) {
    try {
      const info = await api(`/models/${encodeURIComponent(m.file)}/info`);
      Object.assign(m, info);
      renderModels();
    } catch { /* 解析不了的就不画轮廓 */ }
  }
}

function drawSilhouette(cv, m) {
  const dpr = window.devicePixelRatio || 1;
  const r = cv.getBoundingClientRect();
  cv.width = Math.max(1, Math.round((r.width || 180) * dpr));
  cv.height = Math.max(1, Math.round((r.height || 74) * dpr));
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = cv.width / dpr, h = cv.height / dpr;
  ctx.clearRect(0, 0, w, h);
  const hull = m.hull || [];
  if (!hull.length) return;
  const xs = hull.map(p => p[0]), ys = hull.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const s = Math.min((w - 16) / Math.max(1e-6, x1 - x0), (h - 14) / Math.max(1e-6, y1 - y0));
  const ox = (w - (x1 - x0) * s) / 2, oy = (h - (y1 - y0) * s) / 2;
  ctx.beginPath();
  hull.forEach(([x, y], i) => {
    const px = ox + (x - x0) * s, py = h - (oy + (y - y0) * s);
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  });
  ctx.closePath();
  ctx.fillStyle = "rgba(255,125,56,.22)";
  ctx.strokeStyle = "rgba(255,125,56,.9)";
  ctx.lineWidth = 1.2;
  ctx.fill();
  ctx.stroke();
}

function renderModels() {
  const box = $("#models");
  box.textContent = "";
  $("#models-empty").style.display = state.models.length ? "none" : "block";
  for (const m of state.models) {
    const card = el("div", "mcard");
    card.dataset.file = m.file;
    const nm = el("div", "nm", m.file);
    nm.title = m.file;
    const cv = document.createElement("canvas");
    const meta = el("div", "meta");
    meta.append(
      el("span", null, m.w ? `${m.w}×${m.d}×${m.h} mm` : fmtBytes(m.size)),
      el("span", null, m.tris ? `${(m.tris / 1000).toFixed(1)}k 面` : ""),
    );
    const parts = (m.parts && m.parts.length) ? m.parts : null;
    const acts = el("div", "acts");
    const add = el("button", "primary", parts ? `＋ 摆盘（${parts.length} 个部件）` : "＋ 摆盘");
    add.onclick = (e) => { e.stopPropagation(); addToPlate(m.file); };
    const del = el("button", "ghost danger", "删除");
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`删除 ${m.file}？`)) return;
      try { await api(`/models/${encodeURIComponent(m.file)}`, { method: "DELETE" }); loadModels(); }
      catch (err) { toast(err.message, true); }
    };
    acts.append(add, del);
    card.append(nm, cv, meta, acts);
    card.onclick = () => addToPlate(m.file);
    box.append(card);
    drawSilhouette(cv, m);

    // 多部件 3MF：列出每个部件，可单独加入摆盘
    if (parts) {
      const plist = el("div", "parts");
      parts.forEach(p => {
        const row = el("div", "part-row");
        const pnm = el("div", "pnm", p.name);
        pnm.title = p.name;
        const psz = el("span", "psz", `${p.w}×${p.d}×${p.h}mm · ${(p.tris / 1000).toFixed(1)}k`);
        const pb = el("button", "ghost", "＋");
        pb.title = "只加入这个部件";
        pb.onclick = (e) => { e.stopPropagation(); addToPlate(m.file, p.id); };
        row.append(pnm, psz, pb);
        plist.append(row);
      });
      card.append(plist);
    }
  }
}

// ---------------------------------------------------------------- 摆盘

const plate = createPlate3D($("#bed"), {});
window.__plate = plate;   // 排障入口：控制台里 __plate.debug() 看画布/网格状态
plate.api.onSelect = (i) => { state.sel = i; renderObjs(); };
plate.api.onChange = () => { renderObjs(); checkPlate(); save(); commit(); };
plate.api.onLayFace = (i) => {
  const o = state.plate.objects[i];
  if (o) toast("已把该面旋转到朝下贴床（RX/RY/RZ 已更新，清零即可复位）");
};

function bedOf() {
  return state.plate.bed || { w: 200, d: 200, x0: 0, y0: 0, circular: false, h: 0, extruders: 1, mmu: false };
}

// 新对象默认落在哪个工具头：MMU 机器上应该是「真装了料盘的那个槽」。
// 一直用 T1 的话，哪怕料盘挂在 T4、T4 也选了 JAYO，切出来用的还是 T1 的配置（35℃ 热床）。
function defaultExtruder() {
  const slots = (state.spools && state.spools.slots) || [];
  const ace = state.prLive && state.prLive.ace;
  const cur = ace && ace.enabled ? ace.current : null;
  if (typeof cur === "number" && slots[cur] && slots[cur].spool && slots[cur].spool.filament) return cur + 1;
  const i = slots.findIndex(s => s && s.spool && s.spool.filament);
  return i >= 0 ? i + 1 : 1;
}

async function addToPlate(file, part) {
  let info;
  try {
    info = await api(`/models/${encodeURIComponent(file)}/info`);
  } catch (e) { toast("读模型失败：" + e.message, true); return; }
  const bed = bedOf();
  const parts = (info.parts && info.parts.length) ? info.parts : null;

  const push = (p) => {
    state.plate.objects.push({
      uid: Math.random().toString(36).slice(2, 8),
      file,
      part: p ? String(p.id) : "",
      name: p ? p.name : file.replace(/\.(stl|3mf|obj)$/i, ""),
      x: (bed.x0 || 0) + bed.w / 2, y: (bed.y0 || 0) + bed.d / 2, z: 0,
      rot: 0, rx: 0, ry: 0, rz: 0, scale: 1, extruder: defaultExtruder(),
      hull: p ? p.hull : info.hull,
      bbox: p ? p.bbox : info.bbox,
      w: p ? p.w : info.w, d: p ? p.d : info.d, h: p ? p.h : info.h,
    });
  };

  if (part) {
    const p = parts && parts.find(x => String(x.id) === String(part));
    if (!p) return toast("找不到该部件", true);
    push(p);
    toast(`已加入 ${p.name}`);
  } else if (parts) {
    parts.forEach(push);
    toast(`已加入 ${file} 的 ${parts.length} 个部件`);
  } else {
    push(null);
    toast(`已加入 ${file}`);
  }
  autoArrange(false);
  save();
  // 加入后直接跳到摆盘页：留在模型库只弹个 toast，很容易以为「点了没反应」
  go("plate");
  syncPlate();
}

function renderObjs() {
  const box = $("#objs");
  box.textContent = "";
  const objs = state.plate.objects;
  $("#objcount").textContent = objs.length ? `${objs.length} 个` : "";
  $("#objs-empty").style.display = objs.length ? "none" : "block";
  const nFil = toolCount();
  objs.forEach((o, i) => {
    const d = el("div", "obj" + (i === state.sel ? " sel" : ""));
    const top = el("div", "top");
    const sw = el("i", "swatch");
    sw.style.background = PALETTE[((o.extruder || 1) - 1) % PALETTE.length];
    const nm = el("div", "nm", o.name || o.file.replace(/\.(stl|3mf|obj)$/i, ""));
    nm.title = o.part ? `${o.file} · ${o.name}` : o.file;
    const sel = el("select");
    sel.style.width = "auto";
    sel.style.maxWidth = "128px";
    sel.style.minWidth = "64px";
    for (let k = 1; k <= Math.max(nFil, o.extruder || 1); k++) {
      const fil = state.plate.filaments[k - 1];
      const opt = el("option", null, "T" + k + (fil ? " · " + fil : ""));
      opt.value = String(k);
      sel.append(opt);
    }
    sel.value = String(o.extruder || 1);
    sel.onchange = () => { o.extruder = parseInt(sel.value, 10); plate.redraw(); renderObjs(); checkPlate(); save(); commit(); };
    const del = el("button", "ghost danger", "×");
    del.style.padding = "0 6px";
    del.onclick = () => { state.plate.objects.splice(i, 1); state.sel = -1; syncPlate(); save(); };
    top.append(sw, nm, sel, del);
    d.append(top);

    const nums = el("div", "nums");
    const mk = (label, key, step) => {
      const cell = el("div");
      const lb = el("label", null, label);
      const inp = document.createElement("input");
      inp.type = "number";
      inp.step = step;
      inp.value = o[key];
      inp.onchange = () => {
        const v = parseFloat(inp.value);
        if (!Number.isFinite(v)) return;
        o[key] = v;
        plate.redraw();
        if (key !== "z") checkPlate();
        save();
        commit();
      };
      cell.append(lb, inp);
      return cell;
    };
    // 三轴旋转：o.rx/o.ry/o.rz 就是权威状态，填多少就是多少（不会因为欧拉分解漂移）
    const ang = plate.anglesOf(i);
    const mkAng = (label, axis, step) => {
      const cell = el("div");
      const lb = el("label", null, label);
      const inp = document.createElement("input");
      inp.type = "number";
      inp.step = step;
      inp.value = fmtDeg(ang[axis]);
      inp.onchange = () => {
        const v = parseFloat(inp.value);
        if (!Number.isFinite(v)) return;
        const e = plate.anglesOf(i);
        e[axis] = v;
        const got = plate.setAngles(i, e);
        if (got) for (const k of ["rx", "ry", "rz"]) {
          const f = d.querySelector(`input[data-ang="${k}"]`);
          if (f && document.activeElement !== f) f.value = fmtDeg(got[k]);
        }
        checkPlate();
        save();
        commit();
      };
      inp.dataset.ang = axis;
      cell.append(lb, inp);
      return cell;
    };
    nums.append(mk("X", "x", 0.5), mk("Y", "y", 0.5), mk("缩放", "scale", 0.05),
      mkAng("RX°", "rx", 15), mkAng("RY°", "ry", 15), mkAng("RZ°", "rz", 15));
    d.append(nums);
    // 点卡片空白处才选中；点下拉框/数字输入/按钮时不能冒泡到卡片再触发 renderObjs()，
    // 否则会把正在交互的控件整个重建掉，导致下拉打不开、输入框聚不了焦。
    d.onclick = (e) => {
      const t = e.target;
      if (t && t.closest && t.closest("select, input, button, label")) return;
      state.sel = i; plate.select(i); renderObjs();
    };
    box.append(d);
  });
}

// ---------------------------------------------------------------- 热床板类型

// 值必须是 Orca 认识的字符串，否则会被静默退回 Cool Plate（实测 "Hot Plate" /
// "Smooth PEI Plate" 都不认）。zh 只是给人看的中文名。
const BED_TYPES = [
  { v: "Cool Plate", zh: "低温板 Cool", k: "cool_plate_temp" },
  { v: "High Temp Plate", zh: "高温板/光滑PEI", k: "hot_plate_temp" },
  { v: "Textured PEI Plate", zh: "纹理PEI", k: "textured_plate_temp" },
  { v: "Engineering Plate", zh: "工程板", k: "eng_plate_temp" },
  { v: "Supertack Plate", zh: "超粘板", k: "supertack_plate_temp" },
];

const filDocCache = {};
const bedTempCache = {};   // bedType → 本次选中的最高温度

async function filamentDoc(name) {
  if (!name) return null;
  if (name in filDocCache) return filDocCache[name];
  try {
    const r = await api(`/profiles/filament/${encodeURIComponent(name)}`);
    filDocCache[name] = (r && r.doc) || null;   // 接口返回 {name,kind,source,doc}
  } catch { filDocCache[name] = null; }
  return filDocCache[name];
}

// 「90%」/["90%"]/90 → 90；取不到就当 100（不补偿）
function pctNum(v) {
  if (Array.isArray(v)) v = v[0];
  if (v == null) return 100;
  const n = parseFloat(String(v).replace("%", ""));
  return Number.isFinite(n) && n > 0 ? n : 100;
}

// 槽位耗材 / 工艺 / 机器换了：切片页的元数据（含各槽收缩率）作废，下次进切片页重取。
function invalidateSliceSchema() {
  state.sliceData = null;
  state.sliceDoc = null;
  state.slotShrink = null;
  if (state.view === "slice") void renderSlice();
}

function tempOf(doc, key) {
  if (!doc) return null;
  const v = doc[key];
  const a = Array.isArray(v) ? v : [v];
  const n = parseFloat(a[0]);
  return Number.isFinite(n) ? n : null;
}

async function bedTempFor(type) {
  const bt = BED_TYPES.find(b => b.v === type) || BED_TYPES[1];
  const names = (state.plate.filaments || []).filter(Boolean).slice(0, 8);
  const temps = [];
  for (const n of names) temps.push(tempOf(await filamentDoc(n), bt.k));
  const ok = temps.filter(t => t != null && t > 0);
  return ok.length ? Math.max(...ok) : null;
}

async function renderBedType() {
  const sel = $("#sel-bedtype");
  if (!sel) return;
  const cur = BED_TYPES.some(b => b.v === state.bedType) ? state.bedType : "Cool Plate";
  sel.textContent = "";
  for (const b of BED_TYPES) {
    const t = await bedTempFor(b.v);
    bedTempCache[b.v] = t;
    const o = el("option", null, `${b.zh}${t != null ? " · " + t + "℃" : ""}`);
    o.value = b.v;
    sel.append(o);
  }
  sel.value = cur;
  state.bedType = cur;
  const t = bedTempCache[cur];
  const hint = $("#bedtype-hint");
  if (hint) hint.textContent = `本次热床 ${t != null ? t + "℃" : "按耗材配置"}`;
}

// 料盘挂着的耗材自动带进切片槽位（用户手选过的槽不动）
function syncFilamentsFromSpools() {
  const slots = (state.spools && state.spools.slots) || [];
  const list = state.plate.filaments || [];
  let changed = false;
  for (let i = 0; i < list.length; i++) {
    const sp = slots[i] && slots[i].spool;
    if (!sp || !sp.filament || state.filManual[i]) continue;
    if (list[i] !== sp.filament) { list[i] = sp.filament; changed = true; }
  }
  return changed;
}

function renderFilaments() {
  const box = $("#filaments");
  box.textContent = "";
  const list = state.plate.filaments;
  if (!list.length) list.push("");
  list.forEach((name, i) => {
    const row = el("div", "row");
    row.style.marginBottom = "4px";
    const sw = el("i", "swatch");
    sw.style.background = PALETTE[i % PALETTE.length];
    const sel = el("select");
    const none = el("option", null, "（不指定）");
    none.value = "";
    sel.append(none);
    state.profiles.filament.forEach(p => {
      const o = el("option", null, p.name);
      o.value = p.name;
      sel.append(o);
    });
    sel.value = name || "";
    sel.onchange = () => {
      list[i] = sel.value;
      state.filManual[i] = true;   // 手选过了，料盘绑定不再覆盖
      updateCheck();
      void renderBedType();        // 换耗材 = 换热床温度
      invalidateSliceSchema();     // 收缩率/温度跟着换了，覆盖面板与摘要要重取
      save();
    };
    const del = el("button", "ghost", "×");
    del.style.padding = "0 6px";
    del.onclick = () => { list.splice(i, 1); if (!list.length) list.push(""); renderFilaments(); renderObjs(); updateCheck(); invalidateSliceSchema(); save(); };
    const tag = el("span", "hint", "T" + (i + 1));
    tag.title = `喷嘴 ${nozzleOf(i)} mm`;
    const slots = (state.spools && state.spools.slots) || [];
    const sp = slots[i] && slots[i].spool;
    if (sp) {
      tag.textContent = `T${i + 1} · 剩${Number(sp.remaining_m).toFixed(0)}m`;
      tag.title = `${sp.name} · 剩 ${sp.remaining_m} / ${sp.total_m} m`;
      tag.style.color = sp.remaining_m < sp.total_m * 0.15 ? "var(--warn)" : "";
    }
    const nz = el("span", "tag", nozzleOf(i) + "mm");
    nz.style.flex = "none";
    row.append(sw, tag, sel, nz, del);
    box.append(row);
  });
  const add = el("button", "ghost", "＋ 加耗材");
  add.style.width = "100%";
  add.onclick = () => {
    if (list.length >= 8) return toast("最多 8 个耗材槽", true);
    list.push(state.profiles.filament[0]?.name || "");
    renderFilaments();
    renderObjs();
    updateCheck();
  };
  box.append(add);

  const bed = bedOf();
  const multi = state.plate.filaments.length > 1;
  const used = new Set(state.plate.objects.map(o => o.extruder || 1));
  let hint = "";
  if (multi && !bed.mmu && (bed.extruders || 1) < 2) {
    hint = "⚠ 这台机器是单挤出机（没开 MMU），多耗材可能切不了。";
  } else if (bed.mmu) {
    hint = `单挤出机 + MMU：${state.plate.filaments.length} 个槽位，靠换料实现多色。`;
  } else if ((bed.extruders || 1) > 1) {
    hint = `${bed.extruders} 个独立工具头，每个槽位对应一个喷头。`;
  }
  const over = [...used].filter(t => t > state.plate.filaments.length);
  if (over.length) hint += ` ⚠ 有对象指定了 T${over.join("/T")}，但没有对应的耗材槽。`;
  // 料盘上挂的耗材和槽位选的对不上时说出来，否则切出来温度/材料会跟想的不一样。
  const slotsAll = (state.spools && state.spools.slots) || [];
  const bad = [];
  list.forEach((fn, i) => {
    const sp = slotsAll[i] && slotsAll[i].spool;
    if (sp && sp.filament && fn && sp.filament !== fn) bad.push(`T${i} 料盘是 ${sp.filament}，槽位选了 ${fn}`);
  });
  if (bad.length) hint += ` ⚠ ${bad.join("；")}`;
  $("#mmu-hint").textContent = hint;
  void renderBedType();
}

// ---------------------------------------------------------------- 撤销 / 重做
// 摆盘是「编辑器」，误删/误拖/误缩都得能退回去。用整盘快照（对象数组很小）。
const undoStack = [];
const redoStack = [];
const HIST_MAX = 120;
let histSnap = null;   // 上一个已提交的快照（JSON 串）

function _snap() {
  return JSON.stringify({ o: state.plate.objects, sel: state.sel });
}

function _applySnap(s) {
  const d = JSON.parse(s);
  state.plate.objects = d.o || [];
  state.sel = (typeof d.sel === "number" && d.sel >= 0 && d.sel < state.plate.objects.length) ? d.sel : -1;
  plate.setObjects(state.plate.objects, state.sel);
  renderObjs();
  updateCheck();
  save();
}

// 任何改动摆盘的操作做完后调用：与上一快照不同才入栈，并清空重做栈。
function commit() {
  const s = _snap();
  if (s === histSnap) return;
  if (histSnap !== null) {
    undoStack.push(histSnap);
    if (undoStack.length > HIST_MAX) undoStack.shift();
  }
  histSnap = s;
  redoStack.length = 0;
  updateUndoBtns();
}

// 加载/切机器后重置基线：此时的对象算「初始状态」，不可撤销到更早。
function resetHistory() {
  undoStack.length = 0;
  redoStack.length = 0;
  histSnap = _snap();
  updateUndoBtns();
}

function undo() {
  if (!undoStack.length) return;
  redoStack.push(_snap());
  const s = undoStack.pop();
  _applySnap(s);
  histSnap = s;
  updateUndoBtns();
  toast("已撤销");
}

function redo() {
  if (!redoStack.length) return;
  undoStack.push(_snap());
  const s = redoStack.pop();
  _applySnap(s);
  histSnap = s;
  updateUndoBtns();
  toast("已重做");
}

function updateUndoBtns() {
  const u = $("#btn-undo"), r = $("#btn-redo");
  if (u) u.disabled = !undoStack.length;
  if (r) r.disabled = !redoStack.length;
}

function syncPlate() {
  plate.setObjects(state.plate.objects, state.sel);
  renderObjs();
  updateCheck();
  save();
  commit();
}

async function autoArrange(notify = true) {
  if (!state.plate.objects.length) return;
  const bed = bedOf();
  try {
    const r = await api("/plate/arrange", {
      method: "POST",
      body: {
        bed: { w: bed.w, d: bed.d, margin: 3 },
        objects: state.plate.objects.map(o => ({
          file: o.file, part: o.part, rot: o.rot, scale: o.scale, m: o.m,
          extruder: o.extruder || 1,
        })),
        filaments: state.plate.filaments,   // 收缩率会放大模型，排布按放大后的占地
        overrides: state.overrides,
      },
    });
    r.objects.forEach((p, i) => {
      if (state.plate.objects[i]) {
        state.plate.objects[i].x = p.x;
        state.plate.objects[i].y = p.y;
      }
    });
    syncPlate();
    if (notify) toast("已自动摆盘");
  } catch (e) { toast(e.message, true); }
}

let checkTimer = 0;
let fitSeq = 0;
function updateCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(checkPlate, 250);
}

// 放不下时给一个出口：求最大统一缩放，让所有对象都进热床且不重叠。
async function updateFitHint(warns) {
  const box = $("#plate-fit");
  if (!box) return;
  box.textContent = "";
  if (!warns.length) return;
  const seq = ++fitSeq;
  const bed = bedOf();
  const snapshot = state.plate.objects.map(o => ({
    file: o.file, part: o.part, name: o.name, x: o.x, y: o.y,
    rot: o.rot, scale: o.scale, m: o.m, extruder: o.extruder || 1,
  }));
  try {
    // 带上耗材/覆盖：收缩率会把模型放大，缩放比例得按放大后的占地算
    const f = await api("/plate/fit", {
      method: "POST",
      body: {
        bed: { w: bed.w, d: bed.d, margin: 3 },
        objects: snapshot,
        filaments: state.plate.filaments,
        overrides: state.overrides,
      },
    });
    if (seq !== fitSeq) return;   // 期间又有新的检查，丢弃过期结果
    if (f.all_fit && f.factor < 0.999) {
      const pct = (f.factor * 100).toFixed(1);
      const btn = el("button", "primary", `缩放到 ${pct}% 全部放下`);
      btn.style.width = "100%";
      btn.onclick = () => applyFit(f);
      const hint = el("div", "hint", "统一缩放后所有对象都能进热床、不重叠（会改变打印尺寸）");
      hint.style.marginTop = "4px";
      box.append(btn, hint);
    } else if (!f.all_fit) {
      box.append(el("div", "hint", "⚠ 即使缩到最小也放不下，请减少对象或换更大热床"));
    } else {
      box.append(el("div", "hint", "尺寸都放得下，点「自动摆盘」重新排布即可"));
    }
  } catch { /* fit 失败不影响主流程 */ }
}

function applyFit(f) {
  if (f.objects.length !== state.plate.objects.length) return;
  state.plate.objects.forEach((o, i) => {
    o.scale = +(o.scale * f.factor).toFixed(4);
    o.x = f.objects[i].x;
    o.y = f.objects[i].y;
  });
  syncPlate();
  toast(`已缩放到 ${(f.factor * 100).toFixed(1)}%，全部放下`);
}

async function checkPlate() {
  const bed = bedOf();
  if (!state.plate.objects.length) {
    $("#plate-warns").textContent = "";
    $("#plate-ok").textContent = "还没有对象";
    const fb = $("#plate-fit"); if (fb) fb.textContent = "";
    fitSeq++;
    return;
  }
  try {
    const r = await api("/plate/check", {
      method: "POST",
      body: {
        bed,
        objects: state.plate.objects.map(o => ({
          file: o.file, part: o.part, name: o.name, x: o.x, y: o.y, rot: o.rot, scale: o.scale, m: o.m,
          extruder: o.extruder || 1,
        })),
        filaments: state.plate.filaments,   // 收缩率补偿要算进边界检查
        overrides: state.overrides,
      },
    });
    const warns = r.warnings || [];
    // 对象用的工具头和「真装了料盘的槽」对不上时提醒：耗材配置（含热床温度）会按空槽的走。
    const slots = (state.spools && state.spools.slots) || [];
    const loaded = slots.map((s, i) => (s && s.spool && s.spool.filament) ? i + 1 : 0).filter(Boolean);
    const usedT = [...new Set(state.plate.objects.map(o => o.extruder || 1))];
    const dry = usedT.filter(t => !loaded.includes(t) && state.plate.filaments[t - 1]);
    if (loaded.length && dry.length) {
      warns.push(`对象用的是 T${dry.join("/T")}，但料盘装在 T${loaded.join("/T")}，切片会用 T${dry[0]} 的耗材配置（热床温度也可能不对）`);
    }
    // 收缩率补偿会放大模型（填 90% 就是放大 11%），切片页的覆盖优先于 profile。
    // 服务端已经按放大后的尺寸做边界检查了，这里只把「这次尺寸会被改」说清楚。
    for (const t of usedT) {
      const fd = await filamentDoc(state.plate.filaments[t - 1]);
      if (!fd) continue;
      const xy = pctNum(state.overrides.filament_shrink ?? fd.filament_shrink);
      const z = pctNum(state.overrides.filament_shrinkage_compensation_z
                       ?? fd.filament_shrinkage_compensation_z);
      if (Math.abs(xy - 100) > 1e-6) {
        warns.push(`T${t} 收缩率 XY ${xy}%：切片尺寸会放大到 ${(100 / xy * 100).toFixed(1)}%（检查已按放大后算）`);
      }
      if (Math.abs(z - 100) > 1e-6) {
        warns.push(`T${t} 收缩率 Z ${z}%：高度会放大到 ${(100 / z * 100).toFixed(1)}%，层数和打印时间都会跟着变`);
      }
    }
    const w = $("#plate-warns");
    w.textContent = "";
    warns.forEach(t => w.append(el("div", null, "⚠ " + t)));
    $("#plate-ok").textContent = warns.length ? "" : "✓ 没有发现问题";
    $("#bedstat").textContent = `${bed.w}×${bed.d}mm · ${state.plate.objects.length} 个对象`;
    updateFitHint(warns);
  } catch (e) {
    $("#plate-ok").textContent = "";
    $("#plate-warns").textContent = "检查失败：" + e.message;
    const fb = $("#plate-fit"); if (fb) fb.textContent = "";
    fitSeq++;
  }
}

// ---------------------------------------------------------------- 配置选择

async function loadProfiles() {
  Object.keys(filDocCache).forEach(k => delete filDocCache[k]);
  state.profiles = await api("/profiles");
  const fill = (sel, list, cur) => {
    sel.textContent = "";
    list.forEach(p => {
      const o = el("option", null, p.name + (p.source === "user" ? " *" : ""));
      o.value = p.name;
      sel.append(o);
    });
    if (cur && list.some(p => p.name === cur)) sel.value = cur;
  };
  fill($("#sel-machine"), state.profiles.machine, state.plate.machine);
  fill($("#sel-process"), state.profiles.process, state.plate.process);
  if (!state.plate.machine) state.plate.machine = state.profiles.machine[0]?.name || "";
  if (!state.plate.process) state.plate.process = state.profiles.process[0]?.name || "";
  $("#sel-machine").value = state.plate.machine;
  $("#sel-process").value = state.plate.process;
  renderFilaments();
  await loadBed();
}

async function loadBed() {
  if (!state.plate.machine) return;
  try {
    const bed = await api(`/profiles/machine/${encodeURIComponent(state.plate.machine)}/bed`);
    state.plate.bed = bed;
    plate.setBed(bed);
    // 机器有 N 个工具头就先把 N 个耗材槽摆出来（MMU 机器 extruders=1，用户可再加）
    let need = Math.max(1, Math.min(8, bed.extruders || 1));
    if (bed.mmu) {
      // MMU（ACE 4 槽）：槽位要按 MMU 的槽数铺开，不能只铺 1 个，
      // 否则挂在 T3 的料盘根本不进切片槽位（耗材/温度全都不对）。
      if (state.aceSlots == null) {
        try {
          const live = await api("/printer/live");
          state.aceSlots = (live?.ace?.slots || []).length;
          state.prLive = live;
        } catch { state.aceSlots = 0; }
      }
      const usedSlots = ((state.spools && state.spools.slots) || []).filter(s => s && s.spool).length;
      need = Math.max(need, Math.min(8, state.aceSlots || Math.max(4, usedSlots)));
    }
    const list = state.plate.filaments;
    while (list.length < need) list.push("");
    if (!list.length) list.push("");
    // 默认耗材要真的填上：空槽会让 Orca 用「无耗材」切，预览/摆盘都容易看着像没生效。
    // 优先用料盘上绑定的切片配置，其次用第一份耗材配置。
    const slots = (state.spools && state.spools.slots) || [];
    const fallback = state.profiles.filament[0]?.name || "";
    for (let i = 0; i < list.length; i++) {
      if (list[i]) continue;
      const bound = slots[i]?.spool?.filament;
      list[i] = bound || fallback;
    }
    // 已存下来的旧值（比如以前的 generic-pla）也要让位给料盘绑定，否则「料盘挂了 JAYO，
    // 切出来还是 Generic PLA」。
    syncFilamentsFromSpools();
    plate.setObjects(state.plate.objects, state.sel);
    renderFilaments();
    renderObjs();
    checkPlate();
  } catch (e) { toast("读机器参数失败：" + e.message, true); }
}

function toolCount() {
  return Math.max(1, state.plate.filaments.length, bedOf().extruders || 1);
}

function nozzleOf(slot) {
  const nz = bedOf().nozzles || [];
  return nz[Math.min(slot, nz.length - 1)] ?? "0.4";
}

// ---------------------------------------------------------------- 切片

function renderSliceSummary() {
  const box = $("#slice-summary");
  box.textContent = "";
  const bed = bedOf();
  const tools = state.plate.filaments
    .map((f, i) => f ? `T${i + 1} ${f}` : null).filter(Boolean);
  const ovKeys = Object.keys(state.overrides);
  const zh = (k) => {
    const d = state.sliceData?.params?.[k];
    if (!d) return k;
    const name = d.zh || d.label || k;
    return d.cat_zh ? `${d.cat_zh}/${name}` : name;
  };
  const ovText = ovKeys.length
    ? `${ovKeys.length} 项 · ${ovKeys.slice(0, 5).map(zh).join("、")}${ovKeys.length > 5 ? "…" : ""}`
    : "—";
  const rows = [
    ["机器", state.plate.machine || "—"],
    ["工艺", state.plate.process || "—"],
    ["耗材", tools.join(" · ") || "—"],
    ["对象", state.plate.objects.length + " 个"],
    ["热床", `${bed.w}×${bed.d}mm`],
    ["热床板", `${state.bedType}${bedTempCache[state.bedType] != null ? " · " + bedTempCache[state.bedType] + "℃" : ""}`],
  ];
  // 收缩补偿会实打实改尺寸（100/90 = 放大 11%），切片前得让人看到「现在是多少」。
  const shrink = (state.slotShrink || []).map((s, i) => {
    if (!s) return null;
    const xyOn = Math.abs(s.xy - 100) > 1e-6;
    const zOn = Math.abs(s.z - 100) > 1e-6;
    if (!xyOn && !zOn) return null;
    const parts = [];
    if (xyOn) parts.push(`XY ${s.xy}% → ×${(100 / s.xy).toFixed(3)}`);
    if (zOn) parts.push(`Z ${s.z}% → ×${(100 / s.z).toFixed(3)}`);
    return `T${i + 1} ${parts.join(" · ")}`;
  }).filter(Boolean);
  if (shrink.length) rows.push(["收缩补偿", shrink.join("；")]);
  rows.push(["覆盖", ovText]);
  rows.forEach(([k, v]) => {
    box.append(el("div", "k", k), el("div", null, v));
  });

  // 护栏：支撑类参数改了但「开启支撑」仍是关——Orca 不会生成任何支撑，
  // 这类静默失效看起来就像切片器没听参数（实际是总开关没开）。
  const doc = state.sliceData ? { ...state.sliceData.doc, ...state.overrides } : null;
  if (doc && !asBool(doc.enable_support) && ovKeys.some(k => k.startsWith("support_"))) {
    const w = el("div", "warn",
      "支撑总开关「开启支撑」(enable_support) 是关，support_* 参数不会产生任何支撑。");
    box.append(w);
  }
}

// 切片页「参数覆盖」的元数据：合并 工艺 + 每个耗材 + 机器 三份 schema。
// 只加载工艺 profile 的话，耗材级参数（收缩率、温度、回抽……）在覆盖面板里
// 根本不存在 —— 搜「收缩」什么都搜不到，而后端 overrides 一直是按 key 路由的，
// 缺的只是元数据。合并顺序：工艺先（组顺序以它为准），耗材、机器补在后面。
async function loadSliceSchema() {
  const get = (kind, name) => name
    ? api(`/profiles/${kind}/${encodeURIComponent(name)}`).catch(() => null)
    : Promise.resolve(null);
  const [mach, proc, ...slots] = await Promise.all([
    get("machine", state.plate.machine),
    get("process", state.plate.process),
    ...state.plate.filaments.map(n => get("filament", n)),
  ]);
  const docs = [proc, ...slots, mach].filter(Boolean);
  if (!docs.length) throw new Error("读取配置失败");

  const params = {};
  const groups = new Map();
  const doc = {};
  for (const d of docs) {
    for (const [k, v] of Object.entries(d.params || {})) if (!(k in params)) params[k] = v;
    for (const g of d.groups || []) {
      const arr = groups.get(g.cat) || [];
      for (const k of g.keys || []) if (!arr.includes(k)) arr.push(k);
      groups.set(g.cat, arr);
    }
    Object.assign(doc, d.doc || {});
  }
  state.sliceData = {
    params,
    groups: [...groups].map(([cat, keys]) => ({ cat, keys })),
    doc,
  };
  state.sliceDoc = { ...doc };
  // 每个槽自己的收缩率（100 之外才值得显示）
  state.slotShrink = slots.map((d, i) => {
    const f = (d && d.doc) || {};
    return {
      name: state.plate.filaments[i],
      xy: pctNum(f.filament_shrink),
      z: pctNum(f.filament_shrinkage_compensation_z),
    };
  });
}

async function renderSlice() {
  renderSliceSummary();
  if (!state.sliceData) {
    try {
      await loadSliceSchema();
      renderSliceSummary();   // 参数元数据到位后重画（覆盖项要用中文名 + 支撑护栏）
    } catch (e) { toast(e.message, true); }
  }
  paintOverrides();
}

function paintOverrides() {
  const box = $("#ov-form");
  if (!state.sliceData) { box.textContent = ""; return; }
  const doc = { ...state.sliceData.doc, ...state.overrides };
  renderForm(box, state.sliceData, doc, {
    mode: state.mode,
    search: $("#ov-search").value,
    onEdit: (k, v) => {
      state.overrides[k] = doc[k];
      renderSliceSummary();
      save();
    },
  });
}

async function startSlice() {
  if (!state.plate.objects.length) return toast("摆盘是空的", true);
  const bed = bedOf();
  const payload = {
    machine: state.plate.machine,
    process: state.plate.process,
    filaments: state.plate.filaments.filter(Boolean),
    // 热床温度由 curr_bed_type 决定（Orca 默认 Cool Plate=35℃），必须显式带上
    overrides: { ...state.overrides, curr_bed_type: state.bedType || "Cool Plate" },
    objects: state.plate.objects.map(o => ({
      file: o.file, part: o.part, name: o.name, x: o.x, y: o.y, z: o.z || 0,
      rot: o.rot, scale: o.scale, extruder: o.extruder || 1, m: o.m,
    })),
  };
  void bed;
  const btn = $("#btn-slice");
  btn.disabled = true;
  setStatus("提交中…", "");
  try {
    const r = await api("/plate/slice", { method: "POST", body: payload });
    state.job = { id: r.job_id, status: r.status };
    save();
    setStatus("排队中", "run");
    pollJob(r.job_id);
  } catch (e) {
    setStatus("失败", "err");
    toast(e.message, true);
    btn.disabled = false;
  }
}

function setStatus(text, cls) {
  const b = $("#slice-status");
  b.textContent = text;
  b.className = "badge" + (cls ? " " + cls : "");
}

let pollTimer = 0;
async function pollJob(id) {
  clearTimeout(pollTimer);
  try {
    const job = await api(`/jobs/${id}`);
    state.job = job;
    const st = job.status;
    const done = st === "done", fail = st === "failed";
    setStatus({ queued: "排队中", running: "切片中", done: "完成", failed: "失败" }[st] || st,
      done ? "ok" : fail ? "err" : "run");
    $("#slice-bar").style.width = done ? "100%" : st === "running" ? "55%" : st === "queued" ? "12%" : "0";
    $("#slice-time").textContent = job.duration ? fmtTime(job.duration) : "";
    if (done) {
      $("#btn-slice").disabled = false;
      const s = job.summary || {};
      $("#slice-time").textContent = `${s.layers || "?"} 层 · ${fmtTime(s.sliced_time || job.duration)} · ${fmtBytes(s.gcode_bytes)}`;
      toast("切片完成");
      await loadHealth();
      go("preview");
      return;
    }
    if (fail) {
      $("#btn-slice").disabled = false;
      $("#slice-time").textContent = "";
      toast("切片失败：" + (job.error || "") + sliceFailHint(job.error || ""), true);
      showLog(id);
      return;
    }
  } catch (e) {
    setStatus("查询失败", "err");
  }
  pollTimer = setTimeout(() => pollJob(id), 1200);
}

// Orca 2.4.2 的引擎在「把模型立到接近垂直（实测 88°~90°）」的某些姿态下会抛出
// 通用错误 "found slicing or export error for partplate 1"，同一份网格用 Orca 自带
// transform、甚至纯 STL 输入复现，把角度微调 1° 或打开支撑就能过——所以这里给个提示，
// 别让人以为是摆盘/切片参数写错了。
function sliceFailHint(err) {
  if (/slicing or export error|Failed slicing the model/i.test(err))
    return "（若刚把模型绕 X/Y 转到接近立起来 88°~90°，这是 Orca 引擎的已知误报："
      + "把该角度微调 1°，或打开支撑再切一次）";
  return "";
}

async function showLog(id) {
  const pre = $("#slice-log");
  pre.style.display = "block";
  try {
    const r = await api(`/jobs/${id}/log`);
    pre.textContent = (r.log || "(空)").slice(-6000);
    pre.scrollTop = pre.scrollHeight;
  } catch (e) { pre.textContent = e.message; }
}

// ---------------------------------------------------------------- 预览

async function loadRecentJobs() {
  const sel = $("#pv-jobs");
  let jobs = [];
  try {
    const r = await api("/jobs?limit=20");
    jobs = (r.jobs || []).filter(j => j.status === "done");
  } catch { /* 服务不稳时不影响预览 */ }
  const cur = state.job?.id || jobs[0]?.id;
  if (!jobs.length) { sel.style.display = "none"; return jobs; }
  sel.textContent = "";
  jobs.forEach(j => {
    const s = j.summary || {};
    const o = el("option", null,
      `${j.id.slice(0, 6)} · ${j.model_name} · ${s.layers || "?"}层`);
    o.value = j.id;
    sel.append(o);
  });
  if (cur) sel.value = cur;
  sel.style.display = "";
  return jobs;
}

async function renderPreview() {
  await loadRecentJobs();
  if (!state.job?.id) {
    const sel = $("#pv-jobs");
    if (sel.value) {
      try { state.job = await api(`/jobs/${sel.value}`); } catch { /* ignore */ }
    }
  }
  const id = state.job?.id;
  if (!id) {
    $("#pv-job").textContent = "还没有任务";
    $("#pvframe").src = "about:blank";
    return;
  }
  $("#pv-job").textContent = id;
  if (state.job.status !== "done") {
    try { state.job = await api(`/jobs/${id}`); } catch { /* ignore */ }
  }
  const s = state.job.summary || {};
  $("#pv-sum").textContent = [
    s.layers ? `${s.layers} 层` : "",
    s.sliced_time ? fmtTime(s.sliced_time) : "",
    s.gcode_bytes ? fmtBytes(s.gcode_bytes) : "",
    s.preview?.segments ? `${(s.preview.segments / 1000).toFixed(0)}k 段` : "",
  ].filter(Boolean).join(" · ");
  const frame = $("#pvframe");
  const want = `/viewer/?job=${id}`;
  if (!frame.src.endsWith(want)) frame.src = want;
  /* 「独立打开」：飞牛OS 等内嵌 WebView 里，iframe 内的 WebGL 画布
     可能渲染正确但合不上屏（画布能 readPixels 到内容，屏幕却是空的）。
     在独立窗口/新标签里打开可以绕开 iframe 合成路径。 */
  const openBtn = $("#btn-pv-open");
  if (openBtn && !openBtn._bound) {
    openBtn._bound = true;
    openBtn.addEventListener("click", () => {
      const j = (state.job && state.job.id) || state.jobId;
      if (j) window.open(`/viewer/?job=${j}`, "_blank");
    });
  }
  const g = $("#pv-gcode");
  if (s.gcode_bytes) { g.href = `/jobs/${id}/gcode`; g.style.display = "inline"; }
  else g.style.display = "none";
}

// ---------------------------------------------------------------- 打印机

async function renderPrint() {
  loadPrinterConfig();
  renderJobCard();
  pollLive();
}

async function loadPrinterConfig() {
  try {
    const p = await api("/printer");
    $("#pr-url").value = p.url || "";
    $("#pr-key").value = p.api_key === "***" ? "" : (p.api_key || "");
    $("#pr-key").placeholder = p.api_key === "***" ? "已保存（留空不改）" : "没设就留空";
    $("#pr-name").value = p.name || "";
  } catch (e) { /* ignore */ }
}

function renderJobCard() {
  const job = state.job;
  const box = $("#print-summary");
  box.textContent = "";
  const ok = job && job.status === "done";
  $("#print-none").style.display = ok ? "none" : "block";
  $("#btn-upload-print").disabled = !ok;
  $("#btn-upload-start").disabled = !ok;
  if (!ok) return;
  const s = job.summary || {};
  [
    ["任务", job.id],
    ["模型", job.model_name],
    ["层数", (s.layers || "?") + " 层"],
    ["预计耗时", fmtTime(s.sliced_time || 0)],
    ["gcode", fmtBytes(s.gcode_bytes || 0)],
  ].forEach(([k, v]) => box.append(el("div", "k", k), el("div", null, v)));
  // 顺带估一下用量、看看料够不够（顺手把料盘账本用起来）
  const need = el("div", "k", "用量");
  const val = el("div", null, "估算中…");
  box.append(need, val);
  api(`/jobs/${job.id}/usage`).then(u => {
    const parts = Object.entries(u.slots || {}).map(([t, s]) => {
      const short = s.spool && s.remaining_m != null && s.remaining_m < s.m;
      return `T${Number(t) + 1} ${s.m.toFixed(2)} m`
        + (s.spool ? `（${s.spool}${short ? " ⚠不够" : ""}）` : "（没装料盘）");
    });
    val.textContent = `共 ${u.total_m.toFixed(2)} m · ` + (parts.join(" · ") || "单色");
    val.style.color = parts.some(p => p.includes("⚠")) ? "var(--warn)" : "";
  }).catch(() => { val.textContent = "—"; });
}

async function savePrinter() {
  const body = {
    url: $("#pr-url").value.trim(),
    name: $("#pr-name").value.trim(),
  };
  const key = $("#pr-key").value.trim();
  if (key) body.api_key = key;
  try {
    await api("/printer", { method: "PUT", body });
    toast("已保存");
    renderPrint();
  } catch (e) { toast(e.message, true); }
}

async function testPrinter() {
  $("#pr-state").textContent = "连接中…";
  try {
    const r = await api("/printer/test", { method: "POST" });
    $("#pr-state").textContent =
      `✓ ${r.state || "ok"} · 喷嘴 ${r.nozzle ?? "?"}℃ / 热床 ${r.bed ?? "?"}℃ · ${r.software || ""}`;
  } catch (e) {
    $("#pr-state").textContent = "✗ " + e.message;
  }
}

async function sendToPrinter(start) {
  const job = state.job;
  if (!job || job.status !== "done") return;
  if (start && !confirm(`确认在打印机上开始打印「${job.model_name}」？\n这会立刻加热并动起来。`)) return;
  const btns = [$("#btn-upload-print"), $("#btn-upload-start")];
  btns.forEach(b => (b.disabled = true));
  $("#print-result").textContent = start ? "上传并启动…" : "上传中…";
  try {
    const r = await api(`/jobs/${job.id}/print`, { method: "POST", body: { start } });
    $("#print-result").textContent = start
      ? `✓ 已上传并开始打印：${r.uploaded?.path}`
      : `✓ 已上传到打印机：${r.uploaded?.path}（没有开始打印）`;
    toast(start ? "已开始打印" : "文件已上传");
  } catch (e) {
    $("#print-result").textContent = "✗ " + e.message;
    toast(e.message, true);
  } finally {
    btns.forEach(b => (b.disabled = false));
  }
}

// ---------------------------------------------------------------- 实时状态 / 控制

let liveTimer = 0;
let liveBusy = false;
let tempSig = "";

const STATE_ZH = {
  ready: "就绪", standby: "待机", printing: "打印中", paused: "已暂停",
  complete: "已完成", cancelled: "已取消", error: "错误", shutdown: "停机",
  startup: "启动中",
};

function startLive() {
  stopLive();
  pollLive();
  liveTimer = setInterval(pollLive, 2000);
}
function stopLive() {
  if (liveTimer) clearInterval(liveTimer);
  liveTimer = 0;
}

async function pollLive() {
  if (liveBusy || state.view !== "print") return;
  if ($("#pl-auto") && !$("#pl-auto").checked) return;
  liveBusy = true;
  try {
    paintLive(await api("/printer/live"));
  } catch (e) {
    const b = $("#pl-state");
    b.className = "badge err";
    b.textContent = "离线";
    $("#pl-msg").textContent = e.message;
  } finally {
    liveBusy = false;
  }
}

function paintLive(d) {
  state.prLive = d;
  // 打印刚结束（完成/取消/报错）→ 自动和 Moonraker 对账，把用量记到料盘上
  const prev = state.lastPrintState;
  if (prev && (prev === "printing" || prev === "paused") &&
      ["complete", "cancelled", "error", "shutdown", "standby", "ready"].includes(d.state)) {
    syncSpools(true);
  }
  state.lastPrintState = d.state;
  const badge = $("#pl-state");
  badge.textContent = STATE_ZH[d.state] || d.state || "?";
  const bad = d.state === "error" || d.state === "shutdown" || d.state === "cancelled";
  badge.className = "badge " + (bad ? "err" : (d.state === "printing" || d.state === "paused" ? "run" : "ok"));
  $("#pl-msg").textContent = d.state_message || "";

  $("#pl-file").textContent = d.filename || "— 没在打印 —";
  $("#pl-percent").textContent = (d.progress || 0).toFixed(1) + "%";
  $("#pl-bar").style.width = Math.max(0, Math.min(100, d.progress || 0)) + "%";
  $("#pl-elapsed").textContent = fmtTime(d.print_duration || 0);
  const cur = d.layer && d.layer.current, tot = d.layer && d.layer.total;
  $("#pl-layer").textContent = (cur != null && tot) ? `${cur} / ${tot}` : "—";
  $("#pl-eta").textContent = (() => {
    const p = d.progress || 0;
    // Klipper 不给剩余时间，用「已用时间 / 已完成比例」推一个，2% 以下噪声太大就不显示
    if (d.state === "printing" && p > 2 && d.print_duration > 60) {
      return "剩 " + fmtTime(Math.max(0, d.print_duration * (100 - p) / p));
    }
    return d.state === "paused" ? "已暂停" : "—";
  })();
  $("#pl-filament").textContent = d.filament_used_mm ? (d.filament_used_mm / 1000).toFixed(2) + " m" : "—";
  $("#pl-factor").textContent = `${Math.round(d.speed_factor)}% / ${Math.round(d.extrude_factor)}%`;
  $("#pl-pos").textContent = (d.position || []).map(v => Number(v).toFixed(1)).join(" / ");
  $("#jog-hint").textContent = d.homed_axes
    ? `已归零：${d.homed_axes.split("").join(" ")}`
    : "还没归零 —— XY 移动前先点「归零 XY」。";
  paintTemps(d);
}

function paintTemps(d) {
  const cards = [];
  (d.extruders || []).forEach((e, i) => cards.push({
    key: "e" + i, label: i === 0 ? "喷嘴" : "喷嘴 T" + i,
    temp: e.temp, target: e.target,
  }));
  if (d.bed) cards.push({ key: "bed", label: "热床", temp: d.bed.temp, target: d.bed.target });
  if (d.chamber) cards.push({ key: "chamber", label: "腔体 / ACE", temp: d.chamber.temp, target: d.chamber.target });

  const sig = cards.map(c => c.key).join(",");
  const box = $("#pl-temps");
  if (sig !== tempSig) {
    tempSig = sig;
    box.textContent = "";
    cards.forEach(c => {
      const card = el("div", "tcard");
      card.dataset.key = c.key;
      card.append(el("div", "tl", c.label));
      const big = el("div", "tbig");
      big.append(el("b", null, "—"), el("span", "hint", "℃"));
      card.append(big);
      const row = el("div", "row");
      const inp = document.createElement("input");
      inp.type = "number";
      inp.step = "5";
      inp.min = "0";
      inp.className = "tset";
      inp.value = String(Math.round(c.target || 0));
      const setBtn = el("button", "ghost", "设定");
      setBtn.onclick = () => setTempsFromInputs();
      const off = el("button", "ghost", "关闭");
      off.onclick = () => { inp.value = "0"; setTempsFromInputs(); };
      row.append(inp, setBtn, off);
      card.append(row);
      box.append(card);
    });
  }
  cards.forEach(c => {
    const card = box.querySelector(`.tcard[data-key="${c.key}"]`);
    if (!card) return;
    const b = card.querySelector(".tbig b");
    if (b) b.textContent = Number(c.temp || 0).toFixed(1);
    const inp = card.querySelector("input.tset");
    if (inp && document.activeElement !== inp) inp.value = String(Math.round(c.target || 0));
    card.classList.toggle("hot", Number(c.temp || 0) > 45);
  });
}

async function setTempsFromInputs() {
  const extruders = [];
  let bed = null;
  document.querySelectorAll("#pl-temps .tcard").forEach(c => {
    const v = parseFloat(c.querySelector("input.tset")?.value || "0") || 0;
    if (c.dataset.key === "bed") bed = v;
    else if (/^e\d+$/.test(c.dataset.key)) extruders[parseInt(c.dataset.key.slice(1), 10)] = v;
  });
  try {
    await api("/printer/temps", { method: "POST", body: { bed, extruders } });
    toast("温度已设定");
    setTimeout(pollLive, 500);
  } catch (e) { toast(e.message, true); }
}

async function jog(axis, sign) {
  const step = parseFloat($("#jog-step").value) || 10;
  const feed = parseFloat($("#jog-feed").value) || 3000;
  // 挤出量跟步长无关，固定小幅，免得一按就是几十毫米
  const delta = axis === "E" ? sign * 5 : sign * step;
  try {
    await api("/printer/jog", { method: "POST", body: { axis, delta, feedrate: feed } });
    if (axis === "E") toast(`${sign > 0 ? "挤出" : "回抽"} 5 mm`);
  } catch (e) { toast(e.message, true); }
  setTimeout(pollLive, 400);
}

const ACT_ZH = {
  home: "归零", home_xy: "归零 XY", pause: "暂停", resume: "继续",
  cancel: "取消打印", firmware_restart: "固件重启", emergency_stop: "急停",
};

async function printerAction(name) {
  if (name === "cancel" && !confirm("取消当前打印？打印头会移到停靠位。")) return;
  if (name === "emergency_stop" && !confirm("急停会立刻切断电机使能，确认？")) return;
  if (name === "firmware_restart" && !confirm("重启 Klipper 固件？当前打印会中断。")) return;
  try {
    await api("/printer/action", { method: "POST", body: { action: name } });
    toast("已发送：" + (ACT_ZH[name] || name));
  } catch (e) { toast(e.message, true); }
  setTimeout(pollLive, 800);
}

function gcodeLog(text, cls) {
  const box = $("#gcode-log");
  const line = el("div", cls, text);
  box.append(line);
  while (box.childElementCount > 200) box.firstElementChild.remove();
  box.scrollTop = box.scrollHeight;
}

async function sendGcode() {
  const script = $("#gcode-in").value.trim();
  if (!script) return;
  const hint = $("#gcode-hint");
  hint.textContent = "发送中…";
  gcodeLog("> " + script.replace(/\n/g, " · "), "gl-send");
  try {
    await api("/printer/gcode", { method: "POST", body: { script } });
    hint.textContent = "已发送";
    gcodeLog("✓ ok", "gl-ok");
  } catch (e) {
    hint.textContent = "失败";
    gcodeLog("✗ " + e.message, "gl-err");
  }
  setTimeout(pollLive, 500);
}

// ---------------------------------------------------------------- 耗材 / 料盘

const SLOT_MAX = 8;

async function renderSpool() {
  let st;
  try {
    st = await api("/spools");
  } catch (e) { toast(e.message, true); return; }
  state.spools = st;
  // 换了料盘绑定 → 摆盘页的槽位跟着变（手动选过的槽不动）
  if (syncFilamentsFromSpools()) { save(); renderFilaments(); }
  let live = null;
  try { live = await api("/printer/live"); } catch (e) { live = null; }
  state.prLive = live || state.prLive;

  const total = st.total_m || 0;
  $("#spool-total").textContent = st.spools.length
    ? `${st.spools.length} 卷 · 剩余 ${total.toFixed(1)} m` : "";
  if (live && live.ace && live.ace.enabled) {
    const dr = live.ace.dryer;
    $("#mmu-live").textContent = `ACE 在线 · 当前槽 T${live.ace.current ?? "?"}`
      + ` · 箱温 ${live.ace.temp ?? "?"}℃ / 湿度 ${live.ace.humidity ?? "?"}%`
      + (dr && dr.status === "drying" ? ` · 烘干中，剩 ${Math.round((dr.remain_s || 0) / 60)} 分` : "");
  } else {
    $("#mmu-live").textContent = live ? "没检测到 MMU（ACE）" : "打印机离线";
  }
  renderSlots(st, live);
  renderSpoolList(st);
  renderSpoolRecords(st);
}

function renderSlots(st, live) {
  const box = $("#slots");
  box.textContent = "";
  const aceSlots = (live && live.ace && live.ace.slots) || [];
  const used = (st.slots || []).filter(s => s.spool).map(s => s.slot);
  const n = Math.max(
    aceSlots.length,
    used.length ? Math.max(...used) + 1 : 0,
    bedOf().extruders || 1,
    1,
  );
  for (let i = 0; i < Math.min(n, 16); i++) {
    const spool = (st.slots[i] && st.slots[i].spool) || null;
    const ace = aceSlots.find(s => s.index === i);
    const card = el("div", "slotcard" + (live && live.ace && live.ace.current === i ? " cur" : ""));
    const head = el("div", "row");
    head.append(el("b", null, "T" + i));
    if (ace) {
      const b = el("span", "badge " + (ace.status === "ready" ? "ok" : ""), ace.status === "ready" ? "有料" : "空");
      head.append(b);
    }
    const sw = el("i", "swatch");
    sw.style.background = (spool && spool.color) || (ace && ace.color) || "#444";
    head.append(sw);
    head.append(el("div", "spacer"));
    head.append(el("span", "hint", spool ? spool.material || "" : ""));
    card.append(head);

    const sel = el("select");
    const none = el("option", null, "（空槽）");
    none.value = "";
    sel.append(none);
    (st.spools || []).forEach(s => {
      const o = el("option", null, s.name + (s.slot != null && s.slot !== i ? ` (T${s.slot})` : ""));
      o.value = s.id;
      sel.append(o);
    });
    sel.value = spool ? spool.id : "";
    sel.onchange = async () => {
      try {
        if (!sel.value) {
          if (spool) await api(`/spools/${spool.id}/assign`, { method: "POST", body: { slot: null } });
        } else {
          await api(`/spools/${sel.value}/assign`, { method: "POST", body: { slot: i } });
        }
        renderSpool();
      } catch (e) { toast(e.message, true); renderSpool(); }
    };
    card.append(sel);

    if (spool) {
      const pct = spool.total_m > 0 ? Math.max(0, Math.min(100, (spool.remaining_m / spool.total_m) * 100)) : 0;
      const bar = el("div", "bar" + (pct < 15 ? " low" : ""));
      const fill = el("i");
      fill.style.width = pct + "%";
      bar.append(fill);
      card.append(bar);
      card.append(el("div", "hint", `剩 ${Number(spool.remaining_m).toFixed(1)} / ${spool.total_m} m · ${spool.name}`));
    } else {
      card.append(el("div", "hint", "没装料盘（切片仍可用，但不会记账）"));
    }
    box.append(card);
  }
}

function renderSpoolList(st) {
  const box = $("#spools");
  box.textContent = "";
  $("#spools-empty").style.display = st.spools.length ? "none" : "block";
  st.spools.forEach(s => {
    const row = el("div", "spoolrow");
    const sw = el("i", "swatch");
    sw.style.background = s.color || "#666";
    const nm = el("div", "nm");
    nm.append(el("b", null, s.name), el("span", "hint", [s.material, s.vendor].filter(Boolean).join(" · ")));
    const pct = s.total_m > 0 ? Math.max(0, Math.min(100, (s.remaining_m / s.total_m) * 100)) : 0;
    const mid = el("div", "mid");
    const bar = el("div", "bar" + (pct < 15 ? " low" : ""));
    const fill = el("i");
    fill.style.width = pct + "%";
    bar.append(fill);
    mid.append(bar, el("div", "hint", `剩 ${Number(s.remaining_m).toFixed(1)} / ${s.total_m} m`));
    const slot = el("select");
    const none = el("option", null, "未装");
    none.value = "";
    slot.append(none);
    for (let i = 0; i < SLOT_MAX; i++) {
      const o = el("option", null, "T" + i);
      o.value = String(i);
      slot.append(o);
    }
    slot.value = s.slot == null ? "" : String(s.slot);
    slot.onchange = async () => {
      try {
        await api(`/spools/${s.id}/assign`, {
          method: "POST",
          body: { slot: slot.value === "" ? null : parseInt(slot.value, 10) },
        });
        renderSpool();
      } catch (e) { toast(e.message, true); }
    };
    const edit = el("button", "ghost", "编辑");
    edit.onclick = () => openSpoolModal(s);
    const acts = el("div", "acts");
    acts.append(slot, edit);
    row.append(sw, nm, mid, acts);
    box.append(row);
  });
}

function fmtWhen(ts) {
  return (ts || "").replace("T", " ");
}

function renderSpoolRecords(st) {
  const box = $("#spool-records");
  box.textContent = "";
  const recs = st.records || [];
  if (!recs.length) {
    box.append(el("div", "hint", "还没有打印记录。打印结束后点「与打印机对账」，会从 Moonraker 历史里补记账。"));
    return;
  }
  recs.forEach(r => {
    const row = el("div", "rec");
    const head = el("div", "row");
    head.append(el("b", null, r.filename || "(未知文件)"));
    head.append(el("div", "spacer"));
    head.append(el("span", "hint", fmtWhen(r.time)));
    row.append(head);
    const detail = (r.deduct || []).map(d =>
      `${d.name || d.spool} (T${d.slot}) −${Number(d.m).toFixed(2)} m → ${Number(d.after_m).toFixed(1)} m`
    ).join(" · ");
    row.append(el("div", "hint",
      `用量 ${Number(r.total_m).toFixed(2)} m` + (r.job_id ? ` · 任务 ${r.job_id}` : "") + (detail ? ` · ${detail}` : " · 没有装料盘，没扣减")));
    box.append(row);
  });
}

let editingSpool = null;

function openSpoolModal(spool, preset) {
  editingSpool = spool || null;
  const p = preset || {};
  $("#spool-modal").classList.add("on");
  $("#spool-modal-title").textContent = spool ? "编辑料盘" : "新建料盘";
  $("#btn-spool-del").style.display = spool ? "inline-block" : "none";

  const slotSel = $("#sp-slot");
  slotSel.textContent = "";
  const none = el("option", null, "未装");
  none.value = "";
  slotSel.append(none);
  for (let i = 0; i < SLOT_MAX; i++) {
    const o = el("option", null, "T" + i);
    o.value = String(i);
    slotSel.append(o);
  }
  slotSel.value = spool && spool.slot != null ? String(spool.slot) : "";

  const filSel = $("#sp-filament");
  filSel.textContent = "";
  const noFil = el("option", null, "（不绑定）");
  noFil.value = "";
  filSel.append(noFil);
  (state.profiles.filament || []).forEach(f => {
    const o = el("option", null, f.name);
    o.value = f.name;
    filSel.append(o);
  });
  filSel.value = (spool && spool.filament) || p.filament || "";

  $("#sp-name").value = (spool && spool.name) || p.name || "";
  $("#sp-material").value = (spool && spool.material) || p.material || "PLA";
  $("#sp-vendor").value = (spool && spool.vendor) || p.vendor || "";
  $("#sp-color").value = ((spool && spool.color) || p.color || "#e8e8e8").slice(0, 7);
  $("#sp-total").value = (spool && spool.total_m) || 330;
  $("#sp-remaining").value = spool ? spool.remaining_m : 330;
  $("#sp-diameter").value = (spool && spool.diameter) || 1.75;
  $("#sp-note").value = (spool && spool.note) || "";
}

function closeSpoolModal() {
  $("#spool-modal").classList.remove("on");
  editingSpool = null;
}

async function saveSpool() {
  const body = {
    name: $("#sp-name").value.trim(),
    material: $("#sp-material").value.trim(),
    vendor: $("#sp-vendor").value.trim(),
    color: $("#sp-color").value,
    total_m: parseFloat($("#sp-total").value) || 0,
    remaining_m: parseFloat($("#sp-remaining").value) || 0,
    diameter: parseFloat($("#sp-diameter").value) || 1.75,
    filament: $("#sp-filament").value,
    note: $("#sp-note").value.trim(),
    slot: $("#sp-slot").value === "" ? null : parseInt($("#sp-slot").value, 10),
  };
  if (!body.name) return toast("给料盘起个名字", true);
  try {
    if (editingSpool) await api(`/spools/${editingSpool.id}`, { method: "PUT", body });
    else await api("/spools", { method: "POST", body });
    closeSpoolModal();
    toast("已保存");
    renderSpool();
  } catch (e) { toast(e.message, true); }
}

async function deleteSpool() {
  if (!editingSpool) return;
  if (!confirm(`删除料盘「${editingSpool.name}」？历史记录会保留。`)) return;
  try {
    await api(`/spools/${editingSpool.id}`, { method: "DELETE" });
    closeSpoolModal();
    toast("已删除");
    renderSpool();
  } catch (e) { toast(e.message, true); }
}

async function syncSpools(silent) {
  if (!silent) $("#btn-spool-sync").disabled = true;
  try {
    const r = await api("/spools/sync", { method: "POST", body: {} });
    if (r.count) toast(`已记 ${r.count} 次打印用量，料盘剩余已更新`);
    else if (!silent) toast("没有新的打印记录");
    if (r.count) {
      try { state.spools = await api("/spools"); } catch { /* ignore */ }
      if (state.view === "spool") renderSpool();
      if (state.view === "plate") renderFilaments();
    }
  } catch (e) {
    if (!silent) toast(e.message, true);
  } finally {
    if (!silent) $("#btn-spool-sync").disabled = false;
  }
}

// ---------------------------------------------------------------- 配置编辑

async function renderCfgList() {
  document.querySelectorAll("#cfg-tabs button").forEach(b =>
    b.classList.toggle("on", b.dataset.kind === state.cfg.kind));
  if (!state.profiles.machine) await loadProfiles();
  const box = $("#cfg-items");
  box.textContent = "";
  const list = state.profiles[state.cfg.kind] || [];
  if (!list.length) box.append(el("div", "hint", "（空）"));
  list.forEach(p => {
    const it = el("div", "item" + (p.name === state.cfg.name ? " on" : ""));
    it.append(el("div", "nm", p.name));
    if (p.source === "user") it.append(el("span", "tag user", "改过"));
    else it.append(el("span", "tag", "自带"));
    it.onclick = () => openProfile(state.cfg.kind, p.name);
    box.append(it);
  });
}

async function openProfile(kind, name) {
  if (state.cfg.dirty && !confirm("当前有未保存的修改，放弃？")) return;
  try {
    const d = await api(`/profiles/${kind}/${encodeURIComponent(name)}`);
    state.cfg = { kind, name, data: d, doc: { ...d.doc }, dirty: false, mode: state.cfg.mode };
    $("#cfg-head").style.display = "flex";
    cfgEmpty().style.display = "none";
    $("#cfg-title").textContent = name;
    $("#cfg-source").textContent = d.source === "user" ? "用户配置" : "自带（保存后另存为用户配置）";
    $("#btn-cfg-save").disabled = true;
    document.querySelectorAll("#cfg-mode button").forEach(b =>
      b.classList.toggle("on", b.dataset.mode === state.cfg.mode));
    paintCfg();
    renderCfgList();
  } catch (e) { toast(e.message, true); }
}

function paintCfg() {
  const box = $("#cfg-body");
  if (!state.cfg.data) return;
  const cov = state.cfg.data.coverage || {};
  $("#cfg-title").textContent = state.cfg.name;
  renderForm(box, state.cfg.data, state.cfg.doc, {
    mode: state.cfg.mode,
    search: $("#cfg-search").value,
    showMeta: false,
    onEdit: () => {
      state.cfg.dirty = true;
      $("#btn-cfg-save").disabled = false;
      $("#cfg-title").textContent = state.cfg.name + " *";
    },
  });
  const head = el("div", "hint");
  head.style.marginBottom = "8px";
  head.textContent = `共 ${cov.total ?? "?"} 个参数，有说明 ${cov.with_tip ?? 0} 个，有可选值 ${cov.with_vals ?? 0} 个`;
  box.prepend(head);
}

async function saveCfg() {
  const { kind, name, doc } = state.cfg;
  try {
    const r = await api(`/profiles/${kind}/${encodeURIComponent(name)}`,
      { method: "PUT", body: { doc } });
    toast("已保存" + (r.warnings?.length ? "：" + r.warnings.join("；") : ""));
    state.cfg.dirty = false;
    $("#btn-cfg-save").disabled = true;
    $("#cfg-title").textContent = name;
    await loadProfiles();
    renderCfgList();
    state.cfg.source = "user";
    $("#cfg-source").textContent = "用户配置";
    invalidateSliceSchema();   // 收缩率/温度改了，切片页的摘要与覆盖面板要重取
  } catch (e) { toast(e.message, true); }
}

async function cloneCfg() {
  const to = prompt("另存为新名字：", state.cfg.name + "-副本");
  if (!to) return;
  try {
    await api(`/profiles/${state.cfg.kind}/${encodeURIComponent(state.cfg.name)}/clone`,
      { method: "POST", body: { to } });
    await loadProfiles();
    renderCfgList();
    openProfile(state.cfg.kind, to);
    toast("已另存为 " + to);
  } catch (e) { toast(e.message, true); }
}

async function deleteCfg() {
  if (!confirm(`删除用户配置 ${state.cfg.name}？`)) return;
  try {
    await api(`/profiles/${state.cfg.kind}/${encodeURIComponent(state.cfg.name)}`, { method: "DELETE" });
    state.cfg.name = null;
    state.cfg.data = null;
    state.cfg.dirty = false;
    $("#cfg-head").style.display = "none";
    $("#cfg-body").textContent = "";
    cfgEmpty().style.display = "flex";
    await loadProfiles();
    renderCfgList();
    toast("已删除");
  } catch (e) { toast(e.message, true); }
}

function exportCfg() {
  const url = `/profiles/${state.cfg.kind}/${encodeURIComponent(state.cfg.name)}/export`;
  const a = document.createElement("a");
  a.href = url;
  a.download = `${state.cfg.kind}-${state.cfg.name}.json`;
  a.click();
}

async function importCfg(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api(`/profiles/${state.cfg.kind}/import`, { method: "POST", form: fd });
    await loadProfiles();
    renderCfgList();
    openProfile(state.cfg.kind, r.name);
    toast(`已导入 ${r.name}（${Object.keys(r.params || {}).length} 个参数）`);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 绑定

function bind() {
  document.querySelectorAll("nav.steps button, [data-step]").forEach(b => {
    if (b.tagName === "BUTTON" && b.dataset.step) b.onclick = () => go(b.dataset.step);
  });

  $("#btn-upload").onclick = () => $("#upfile").click();
  $("#upfile").onchange = async (e) => {
    const files = [...e.target.files];
    for (const f of files) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await api("/models", { method: "POST", form: fd });
        toast(`已上传 ${r.file}`);
      } catch (err) { toast(`${f.name}: ${err.message}`, true); }
    }
    e.target.value = "";
    loadModels();
  };
  $("#btn-refresh-models").onclick = () => loadModels();

  $("#btn-undo").onclick = () => undo();
  $("#btn-redo").onclick = () => redo();
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
    if (typing || state.view !== "plate") return;   // 输入框里走浏览器原生撤销
    if (!(e.ctrlKey || e.metaKey)) return;
    const k = (e.key || "").toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
    else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); redo(); }
  });
  $("#btn-arrange").onclick = () => autoArrange();
  $("#btn-center").onclick = () => {
    if (state.sel < 0) return toast("先选一个对象", true);
    const bed = bedOf();
    const o = state.plate.objects[state.sel];
    o.x = (bed.x0 || 0) + bed.w / 2;
    o.y = (bed.y0 || 0) + bed.d / 2;
    syncPlate();
  };
  // 旋转轴：X/Y 把模型立起来/翻过去，Z 是原来的转盘方向。Shift+点 = 90°。
  const rotStep = (sign) => (e) => rotate(sign * (e.shiftKey ? 90 : 15));
  $("#btn-rot-l").onclick = rotStep(-1);
  $("#btn-rot-r").onclick = rotStep(1);
  document.querySelectorAll("#axseg button").forEach(b => {
    b.onclick = () => {
      state.rotAxis = b.dataset.axis;
      plate.setRotAxis(state.rotAxis);
      document.querySelectorAll("#axseg button").forEach(x => x.classList.toggle("on", x === b));
    };
  });
  $("#btn-dup").onclick = () => {
    if (state.sel < 0) return toast("先选一个对象", true);
    const o = { ...state.plate.objects[state.sel], uid: Math.random().toString(36).slice(2, 8) };
    o.x += 10;
    o.y += 10;
    state.plate.objects.push(o);
    syncPlate();
  };
  $("#btn-del").onclick = () => {
    if (state.sel < 0) return toast("先选一个对象", true);
    state.plate.objects.splice(state.sel, 1);
    state.sel = -1;
    syncPlate();
  };
  $("#btn-clear").onclick = () => {
    if (!state.plate.objects.length) return;
    if (!confirm("清空摆盘？")) return;
    state.plate.objects = [];
    state.sel = -1;
    syncPlate();
  };
  document.querySelectorAll("#tmodes button").forEach(b => {
    b.onclick = () => {
      document.querySelectorAll("#tmodes button").forEach(x => x.classList.toggle("on", x === b));
      plate.setTransformMode(b.dataset.tmode);
      $("#layhint").classList.remove("on");
    };
  });
  $("#btn-layface").onclick = () => {
    if (!plate.beginLayFace()) return toast("先在画布上选中一个对象", true);
    toast("点击模型的某个面，该面将旋转到朝下贴床");
  };
  $("#btn-resetview").onclick = () => plate.resetView();
  plate.api.onMode = (m) => {
    $("#layhint").classList.toggle("on", m === "layface");
    if (m !== "layface") {
      document.querySelectorAll("#tmodes button").forEach(x => x.classList.toggle("on", x.dataset.tmode === m));
    }
  };

  $("#sel-machine").onchange = async () => {
    state.plate.machine = $("#sel-machine").value;
    invalidateSliceSchema();
    await loadBed();
    save();
  };
  $("#sel-process").onchange = () => {
    state.plate.process = $("#sel-process").value;
    state.sliceData = null;
    state.sliceDoc = null;
    state.overrides = {};
    save();
  };
  $("#btn-toslice").onclick = () => go("slice");
  $("#sel-bedtype").onchange = () => {
    state.bedType = $("#sel-bedtype").value;
    const t = bedTempCache[state.bedType];
    $("#bedtype-hint").textContent = `本次热床 ${t != null ? t + "℃" : "按耗材配置"}`;
    save();
    renderSliceSummary();
  };

  $("#ov-search").oninput = () => paintOverrides();
  document.querySelectorAll("#ov-mode button").forEach(b => {
    b.onclick = () => {
      state.mode = b.dataset.mode;
      document.querySelectorAll("#ov-mode button").forEach(x => x.classList.toggle("on", x === b));
      paintOverrides();
    };
  });
  $("#btn-ov-clear").onclick = () => {
    state.overrides = {};
    paintOverrides();
    renderSliceSummary();
    save();
    toast("已清空覆盖");
  };
  $("#btn-slice").onclick = () => startSlice();
  $("#btn-tolog").onclick = () => (state.job?.id ? showLog(state.job.id) : toast("还没有任务", true));

  $("#btn-pv-reload").onclick = () => {
    $("#pvframe").src = "about:blank";
    setTimeout(renderPreview, 60);
  };
  $("#pv-jobs").onchange = async (e) => {
    try {
      state.job = await api(`/jobs/${e.target.value}`);
      save();
      renderPreview();
    } catch (err) { toast(err.message, true); }
  };
  $("#btn-topprint").onclick = () => go("print");

  $("#btn-pr-save").onclick = () => savePrinter();
  $("#btn-pr-test").onclick = () => testPrinter();
  $("#btn-upload-print").onclick = () => sendToPrinter(false);
  $("#btn-upload-start").onclick = () => sendToPrinter(true);

  // 打印控制
  document.querySelectorAll("[data-jog]").forEach(b => {
    b.onclick = () => {
      const [axis, sign] = b.dataset.jog.split(",");
      jog(axis, sign === "+" ? 1 : -1);
    };
  });
  document.querySelectorAll("[data-act]").forEach(b => {
    b.onclick = () => printerAction(b.dataset.act);
  });
  $("#btn-pl-refresh").onclick = () => pollLive();
  $("#pl-auto").onchange = () => { if ($("#pl-auto").checked) startLive(); else stopLive(); };
  $("#btn-gcode-send").onclick = () => sendGcode();
  $("#gcode-in").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); sendGcode(); }
  });
  $("#btn-gcode-clear").onclick = () => { $("#gcode-log").textContent = ""; };

  // 耗材 / 料盘
  $("#btn-spool-new").onclick = () => openSpoolModal(null);
  $("#btn-spool-cancel").onclick = () => closeSpoolModal();
  $("#btn-spool-save").onclick = () => saveSpool();
  $("#btn-spool-del").onclick = () => deleteSpool();
  $("#btn-spool-sync").onclick = () => syncSpools();
  $("#spool-modal").onclick = (e) => { if (e.target === $("#spool-modal")) closeSpoolModal(); };

  document.querySelectorAll("#cfg-tabs button").forEach(b => {
    b.onclick = () => {
      state.cfg.kind = b.dataset.kind;
      renderCfgList();
    };
  });
  $("#cfg-search").oninput = () => paintCfg();
  document.querySelectorAll("#cfg-mode button").forEach(b => {
    b.onclick = () => {
      state.cfg.mode = b.dataset.mode;
      document.querySelectorAll("#cfg-mode button").forEach(x => x.classList.toggle("on", x === b));
      paintCfg();
    };
  });
  $("#btn-cfg-save").onclick = () => saveCfg();
  $("#btn-cfg-clone").onclick = () => cloneCfg();
  $("#btn-cfg-export").onclick = () => exportCfg();
  $("#btn-cfg-delete").onclick = () => deleteCfg();
  $("#btn-cfg-import").onclick = () => $("#cfg-import-file").click();
  $("#cfg-import-file").onchange = (e) => {
    if (e.target.files[0]) importCfg(e.target.files[0]);
    e.target.value = "";
  };

  window.addEventListener("beforeunload", (e) => {
    if (state.cfg.dirty) { e.preventDefault(); e.returnValue = ""; }
  });
}

// 角度显示：-0 / 179.99999 这种取整到 0.1°，免得输入框里一长串小数
function fmtDeg(v) {
  if (!Number.isFinite(v)) return "0";
  const r = Math.round(v * 10) / 10;
  return String(Object.is(r, -0) ? 0 : r);
}

// 绕当前选中的轴（X/Y/Z）增量旋转
function rotate(deg) {
  if (state.sel < 0) return toast("先选一个对象", true);
  plate.rotateAxis(state.sel, state.rotAxis || "z", deg);
  syncPlate();
}

// ---------------------------------------------------------------- 启动

async function boot() {
  bind();
  restore();
  await loadHealth();
  try { state.spools = await api("/spools"); } catch { /* 没连上就继续 */ }
  await loadProfiles();
  await loadModels();
  plate.setObjects(state.plate.objects, state.sel);
  renderObjs();
  checkPlate();
  resetHistory();
  go("models");
  if (state.job?.id) {
    try {
      state.job = await api(`/jobs/${state.job.id}`);
    } catch {
      // 任务被删了（或产物已清理）：别每轮启动都去请求一个 404
      state.job = null;
      save();
    }
  }
  setInterval(loadHealth, 20000);
}

boot().catch(e => toast("启动失败：" + e.message, true));
