// 2D 摆盘画布：热床俯视图 + 拖拽对象。
// 为什么不直接 3D：摆盘要的是「别撞、别出床、朝向对不对」，俯视图最直观，
// 手机上单指拖动也顺；切片结果的三维效果放在第 4 步用 WebGL 看。

export const PALETTE = ["#ff7d38", "#4d80ba", "#4caf7d", "#c86bd8", "#e8b23a", "#7ec8e3",
  "#e0564f", "#9ad34f"];

export function createPlate(canvas, opts) {
  const ctx = canvas.getContext("2d");
  const api = {
    bed: { w: 350, d: 350, x0: 0, y0: 0, circular: false },
    objects: [],
    sel: -1,
    scale: 1,
    ox: 0,
    oy: 0,
    onChange: null,
    onSelect: null,
  };

  let dragging = null;
  let dpr = 1;

  function resize() {
    dpr = window.devicePixelRatio || 1;
    const r = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    fit();
    draw();
  }

  function fit() {
    const cw = canvas.width / dpr;
    const ch = canvas.height / dpr;
    const pad = 22;
    const s = Math.min((cw - pad * 2) / api.bed.w, (ch - pad * 2) / api.bed.d);
    api.scale = s;
    api.ox = (cw - api.bed.w * s) / 2;
    api.oy = (ch - api.bed.d * s) / 2;
  }

  function toScreen(x, y) {
    const { x0, y0, d } = api.bed;
    return [api.ox + (x - x0) * api.scale, api.oy + (d + y0 - y) * api.scale];
  }

  function toWorld(sx, sy) {
    return [
      (sx - api.ox) / api.scale + api.bed.x0,
      (api.bed.d + api.bed.y0) - (sy - api.oy) / api.scale,
    ];
  }

  function placedHull(o) {
    const sh = o.hull || [];
    const bb = o.bbox || [0, 0, 0, 1, 1, 1];
    const cx = (bb[0] + bb[3]) / 2;
    const cy = (bb[1] + bb[4]) / 2;
    const a = (o.rot || 0) * Math.PI / 180;
    const ca = Math.cos(a), sa = Math.sin(a);
    const s = o.scale || 1;
    return sh.map(([x, y]) => {
      const u = (x - cx) * s, v = (y - cy) * s;
      return [u * ca - v * sa + o.x, u * sa + v * ca + o.y];
    });
  }

  function draw() {
    const cw = canvas.width / dpr;
    const ch = canvas.height / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    ctx.fillStyle = "#10141a";
    ctx.fillRect(0, 0, cw, ch);

    // 热床
    const { w, d, x0, y0, circular } = api.bed;
    const [bx, by] = toScreen(x0, y0 + d);
    ctx.save();
    ctx.beginPath();
    if (circular) {
      ctx.arc(bx + (w * api.scale) / 2, by + (d * api.scale) / 2,
        (Math.min(w, d) * api.scale) / 2, 0, Math.PI * 2);
    } else {
      ctx.rect(bx, by, w * api.scale, d * api.scale);
    }
    ctx.fillStyle = "#181d24";
    ctx.fill();
    ctx.strokeStyle = "#3a434f";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.clip();

    // 网格（10mm）
    ctx.strokeStyle = "rgba(255,255,255,.045)";
    ctx.lineWidth = 1;
    for (let gx = Math.ceil(x0 / 10) * 10; gx <= x0 + w; gx += 10) {
      const [sx] = toScreen(gx, 0);
      ctx.beginPath(); ctx.moveTo(sx, by); ctx.lineTo(sx, by + d * api.scale); ctx.stroke();
    }
    for (let gy = Math.ceil(y0 / 10) * 10; gy <= y0 + d; gy += 10) {
      const [, sy] = toScreen(0, gy);
      ctx.beginPath(); ctx.moveTo(bx, sy); ctx.lineTo(bx + w * api.scale, sy); ctx.stroke();
    }
    ctx.restore();

    // 原点标记
    const [ox, oy] = toScreen(x0, y0);
    ctx.fillStyle = "#5b6675";
    ctx.font = "10px ui-monospace, monospace";
    ctx.fillText("0,0", ox + 3, oy - 4);

    // 对象
    api.objects.forEach((o, i) => {
      const poly = placedHull(o);
      if (!poly.length) return;
      const col = PALETTE[((o.extruder || 1) - 1) % PALETTE.length];
      ctx.beginPath();
      poly.forEach(([x, y], k) => {
        const [sx, sy] = toScreen(x, y);
        if (k === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
      });
      ctx.closePath();
      ctx.fillStyle = hexA(col, i === api.sel ? 0.42 : 0.26);
      ctx.fill();
      ctx.strokeStyle = i === api.sel ? "#fff" : hexA(col, 0.85);
      ctx.lineWidth = i === api.sel ? 2 : 1.2;
      ctx.stroke();

      // 名字 + 中心点
      const bb = o.bbox || [0, 0, 0, 1, 1, 1];
      const [cx, cy] = toScreen(o.x, o.y);
      ctx.fillStyle = "#fff";
      ctx.font = "11px -apple-system, sans-serif";
      ctx.textAlign = "center";
      const nm = (o.file || "").replace(/\.(stl|3mf|obj)$/i, "");
      ctx.fillText(nm.length > 14 ? nm.slice(0, 13) + "…" : nm, cx, cy + 4);
      ctx.textAlign = "left";
      if (i === api.sel) {
        ctx.beginPath();
        ctx.arc(cx, cy, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = "#fff";
        ctx.fill();
      }
      void bb;
    });
  }

  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  function hit(sx, sy) {
    for (let i = api.objects.length - 1; i >= 0; i--) {
      const poly = placedHull(api.objects[i]);
      if (poly.length && pointInPoly(poly, sx, sy)) return i;
    }
    return -1;
  }

  function pointInPoly(poly, sx, sy) {
    // 屏幕坐标下算，省一次转换
    const pts = poly.map(([x, y]) => toScreen(x, y));
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i], [xj, yj] = pts[j];
      if ((yi > sy) !== (yj > sy) && sx < ((xj - xi) * (sy - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function localXY(ev) {
    const r = canvas.getBoundingClientRect();
    const t = ev.touches ? ev.touches[0] : ev;
    return [t.clientX - r.left, t.clientY - r.top];
  }

  function onDown(ev) {
    const [sx, sy] = localXY(ev);
    const i = hit(sx, sy);
    if (i !== api.sel) {
      api.sel = i;
      api.onSelect && api.onSelect(i);
      draw();
    }
    if (i >= 0) {
      try { canvas.setPointerCapture(ev.pointerId); } catch { /* 老浏览器 */ }
      const [wx, wy] = toWorld(sx, sy);
      dragging = { i, dx: api.objects[i].x - wx, dy: api.objects[i].y - wy, moved: false };
      ev.preventDefault();
    }
  }

  function onMove(ev) {
    if (!dragging) return;
    const [sx, sy] = localXY(ev);
    const [wx, wy] = toWorld(sx, sy);
    const o = api.objects[dragging.i];
    if (!o) return;
    o.x = Math.round((wx + dragging.dx) * 10) / 10;
    o.y = Math.round((wy + dragging.dy) * 10) / 10;
    dragging.moved = true;
    draw();
    ev.preventDefault();
  }

  function onUp() {
    if (dragging && dragging.moved) {
      api.onChange && api.onChange(api.objects[dragging.i], dragging.i);
    }
    dragging = null;
  }

  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("pointerleave", onUp);

  const ro = new ResizeObserver(() => resize());
  ro.observe(canvas);
  resize();

  return {
    api,
    setBed(bed) {
      api.bed = {
        w: bed.w || 200, d: bed.d || 200,
        x0: bed.x0 || 0, y0: bed.y0 || 0,
        circular: !!bed.circular,
      };
      fit();
      draw();
    },
    setObjects(list, sel) {
      api.objects = list;
      if (sel != null) api.sel = sel;
      draw();
    },
    select(i) {
      api.sel = i;
      draw();
    },
    redraw: draw,
    refresh() { resize(); },
  };
}
