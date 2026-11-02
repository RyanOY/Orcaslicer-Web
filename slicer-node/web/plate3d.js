// 3D 摆盘画布（WebGL2）：热床 + 真三维网格 + 轨道相机 + 拾取/变换/贴面。
//
// 交互约定：
//   · 单指拖空白 → 旋转视角（轨道）；双指 → 捏合缩放 + 平移视角
//   · 鼠标：左键拖对象 = 移动/旋转/缩放（按当前变换模式），左键拖空白 = 旋转视角，
//           右键 / 中键 / Shift+左键 拖 = 平移视角，滚轮 = 缩放
//   · 点选对象 → 选中（显示包围盒线框 + 底座高亮）
//   · 「选择底面」：进入贴面模式后，点模型某个面，该面即旋转到朝下贴床
//   · 「旋转」模式下绕哪个轴转由工具栏的 X/Y/Z 决定：Z 按方位角拖（转盘），
//     X/Y 上下拖（把模型立起来/翻过去）；侧栏的 RX°/RY°/RZ° 也能直接填角度
//
// 朝向约定：对象方向 = Rz(rot) @ m，其中 m 是三维旋转矩阵（列向量约定，行主序 9 元组），
// 缺省即单位阵（仅绕 Z 旋转，与旧版一致）。
// 三轴角度 (rx, ry, rz) 是权威状态，朝向由它合成：R = Rz(rz)·Ry(ry)·Rx(rx)，
// 其中 rz 写进 rot、Ry·Rx 写进 m（两者都是 0 时 m 置空，让纯 Z 旋转继续走旧的快路径）。
// 反方向（贴面/旧数据）才做一次矩阵→欧拉角分解，之后角度直接累加，
// 所以输入框里填的数字不会被分解结果悄悄改掉，按钮/拖拽也不会「越转越歪」。
// 摆放：绕包围盒中心做旋转 + 均匀缩放，再把最低点落到 z（贴床）。

export const PALETTE = ["#ff7d38", "#4d80ba", "#4caf7d", "#c86bd8", "#e8b23a", "#7ec8e3",
  "#e0564f", "#9ad34f"];

const FOVY = Math.PI / 5;             // 垂直视场角（与预览 viewer 一致）
const IDENT = [1, 0, 0, 0, 1, 0, 0, 0, 1];
const BED_Z = 0, GRID_Z = 0.03, FP_Z = 0.06;
const LIGHT = norm3([0.42, -0.55, 0.72]);

// ---------------------------------------------------------------- 向量 / 矩阵

function norm3(v) {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}
function cross3(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
function dot3(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function sub3(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }

function rotZ(deg) {
  const a = deg * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a);
  return [ca, -sa, 0, sa, ca, 0, 0, 0, 1];
}
function rotX(deg) {
  const a = deg * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a);
  return [1, 0, 0, 0, ca, -sa, 0, sa, ca];
}
function rotY(deg) {
  const a = deg * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a);
  return [ca, 0, sa, 0, 1, 0, -sa, 0, ca];
}
function normDeg(d) { return ((d % 360) + 360) % 360; }

// 3×3 旋转矩阵 → ZYX 欧拉角（度），满足 R = Rz(rz)·Ry(ry)·Rx(rx)。
// 与前端存储一致：o.rot = rz，o.m = Ry(ry)·Rx(rx)（m 为空即只有绕 Z）。
function eulerOf(R) {
  const s = Math.max(-1, Math.min(1, -R[6]));
  const ry = Math.asin(s);
  const cb = Math.cos(ry);
  let rx, rz;
  if (Math.abs(cb) > 1e-6) {
    rx = Math.atan2(R[7], R[8]);
    rz = Math.atan2(R[3], R[0]);
  } else {
    // 万向锁（俯仰 ±90°）：rz 与 rx 只能定出和/差，取 rz = 0
    rz = 0;
    rx = Math.atan2(Math.sign(s) * R[1], R[4]);
  }
  return { rx: rx * 180 / Math.PI, ry: ry * 180 / Math.PI, rz: rz * 180 / Math.PI };
}
function sameM3(a, b) {
  if (!a || !b) return !a && !b;
  for (let i = 0; i < 9; i++) if (Math.abs(a[i] - b[i]) > 1e-9) return false;
  return true;
}

// 对象朝向 = Rz(rot)·m。三轴角度 (rx,ry,rz) 是权威状态（见文件头约定），
// 这里把它落到 rot/m 上；m 有变化才清贴面凸包缓存（Z 拖拽每帧都会调到这里）。
function applyAngles(o, e, mesh) {
  const rx = normDeg(e.rx || 0), ry = normDeg(e.ry || 0), rz = normDeg(e.rz || 0);
  o.rx = rx; o.ry = ry; o.rz = rz;
  o.rot = rz;
  const mNew = (rx === 0 && ry === 0) ? null : mulM3(rotY(ry), rotX(rx));
  if (!sameM3(mNew, o.m)) {
    o.m = mNew;
    if (mesh && mesh.mhulls) mesh.mhulls.clear();
  }
}

// 读三轴角度：老数据（只有 rot，或贴面留下的 m）第一次用时反解一次并缓存成角度，
// 之后角度就是状态本身，输入框里的数字不会被分解结果悄悄改掉。
function anglesOf(o) {
  if (!o) return { rx: 0, ry: 0, rz: 0 };
  if (o.rx == null && o.ry == null && o.rz == null) {
    const m = (o.m && !isIdentity(o.m)) ? o.m : null;
    const e = eulerOf(m ? mulM3(rotZ(o.rot || 0), m) : rotZ(o.rot || 0));
    o.rx = normDeg(e.rx); o.ry = normDeg(e.ry); o.rz = normDeg(e.rz);
  }
  return { rx: o.rx || 0, ry: o.ry || 0, rz: o.rz || 0 };
}
function mulM3(a, b) {
  const o = new Array(9);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++)
    o[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c];
  return o;
}
function applyM3(M, p) {
  return [M[0] * p[0] + M[1] * p[1] + M[2] * p[2],
          M[3] * p[0] + M[4] * p[1] + M[5] * p[2],
          M[6] * p[0] + M[7] * p[1] + M[8] * p[2]];
}
function transposeM3(M) {
  return [M[0], M[3], M[6], M[1], M[4], M[7], M[2], M[5], M[8]];
}
function isIdentity(m) {
  if (!m) return true;
  return m.every((v, i) => Math.abs(v - IDENT[i]) < 1e-6);
}

function axisAngle(axis, angle) {
  const [x, y, z] = axis, c = Math.cos(angle), s = Math.sin(angle), t = 1 - c;
  return [t * x * x + c, t * x * y - s * z, t * x * z + s * y,
          t * x * y + s * z, t * y * y + c, t * y * z - s * x,
          t * x * z - s * y, t * y * z + s * x, t * z * z + c];
}

// 把局部法线 n 旋转到朝下 (-Z)，返回旋转矩阵（列向量约定，行主序）。
function alignNegZ(n) {
  const target = [0, 0, -1];
  const d = dot3(n, target);
  if (d > 0.9999) return IDENT.slice();
  if (d < -0.9999) return axisAngle([1, 0, 0], Math.PI);
  const axis = norm3(cross3(n, target));
  return axisAngle(axis, Math.acos(Math.max(-1, Math.min(1, d))));
}

// column-major mat4 助手（与 preview viewer 相同的约定）
const M4 = {
  perspective(fovy, asp, n, f) {
    const t = 1 / Math.tan(fovy / 2), d = 1 / (n - f);
    return new Float32Array([t / asp, 0, 0, 0, 0, t, 0, 0, 0, 0, (f + n) * d, -1, 0, 0, 2 * f * n * d, 0]);
  },
  mul(a, b) {
    const o = new Float32Array(16);
    for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k];
      o[i * 4 + j] = s;
    }
    return o;
  },
  lookAt(eye, at, up) {
    let zx = eye[0] - at[0], zy = eye[1] - at[1], zz = eye[2] - at[2];
    let l = Math.hypot(zx, zy, zz) || 1; zx /= l; zy /= l; zz /= l;
    let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
    l = Math.hypot(xx, xy, xz) || 1; xx /= l; xy /= l; xz /= l;
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    return new Float32Array([
      xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0,
      -(xx * eye[0] + xy * eye[1] + xz * eye[2]),
      -(yx * eye[0] + yy * eye[1] + yz * eye[2]),
      -(zx * eye[0] + zy * eye[1] + zz * eye[2]), 1]);
  },
  ident() { return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]); },
};

// 由 R（9 元组，列向量约定）+ 缩放 + 平移 拼 column-major mat4
function matModel(R, s, tx, ty, tz) {
  return new Float32Array([
    R[0] * s, R[3] * s, R[6] * s, 0,
    R[1] * s, R[4] * s, R[7] * s, 0,
    R[2] * s, R[5] * s, R[8] * s, 0,
    tx, ty, tz, 1]);
}

// ---------------------------------------------------------------- 凸包

function convexHull2d(pts) {
  // pts: 已去重、排序后的 [x,y] 数组
  if (pts.length < 3) return pts.map(p => [p[0], p[1]]);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

// ---------------------------------------------------------------- 着色器

const MESH_VS = `#version 300 es
in vec3 aPos;
uniform mat4 uVP;
uniform mat4 uModel;
out vec3 vWorld;
void main() {
  vec4 w = uModel * vec4(aPos, 1.0);
  vWorld = w.xyz;
  gl_Position = uVP * w;
}`;

const MESH_FS = `#version 300 es
precision highp float;
in vec3 vWorld;
uniform vec3 uColor;
uniform vec3 uLight;
uniform vec3 uCam;
uniform float uMode;
out vec4 o;
void main() {
  vec3 dx = dFdx(vWorld);
  vec3 dy = dFdy(vWorld);
  vec3 n = normalize(cross(dx, dy));
  vec3 v = normalize(uCam - vWorld);
  if (dot(n, v) < 0.0) n = -n;               // 双面
  if (uMode > 0.5) { o = vec4(uColor, 1.0); return; }
  float ndl = dot(n, uLight);
  float diff = max(ndl, 0.0);
  float wrap = clamp(ndl * 0.5 + 0.5, 0.0, 1.0);
  float ao = clamp(n.z * 0.5 + 0.5, 0.0, 1.0);
  vec3 h = normalize(uLight + v);
  float spec = pow(max(dot(n, h), 0.0), 24.0) * 0.25;
  float shade = 0.52 + 0.46 * diff + 0.12 * wrap;
  vec3 c = uColor * shade * (0.74 + 0.26 * ao) + vec3(spec);
  o = vec4(min(c, vec3(1.0)), 1.0);
}`;

const FLAT_VS = `#version 300 es
in vec3 aPos;
uniform mat4 uVP;
uniform mat4 uModel;
void main() { gl_Position = uVP * uModel * vec4(aPos, 1.0); }`;

const FLAT_FS = `#version 300 es
precision mediump float;
uniform vec4 uColor;
out vec4 o;
void main() { o = uColor; }`;

function shader(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
  return s;
}
function program(gl, vs, fs) {
  const p = gl.createProgram();
  gl.attachShader(p, shader(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(p, shader(gl, gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  return p;
}

function hexRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => v / 255);
}

// ---------------------------------------------------------------- 工厂

export function createPlate3D(canvas, opts) {
  opts = opts || {};
  const api = { onChange: null, onSelect: null, onLayFace: null, onMode: null, mode: "move" };

  const state = {
    bed: { w: 200, d: 200, x0: 0, y0: 0, circular: false },
    objects: [],
    sel: -1,
  };
  const cam = { az: -0.85, el: 0.85, dist: 400, target: [100, 100, 0] };
  const meshCache = new Map();   // file -> { verts, idx, nv, nf, vao, vbo, ibo, bbox, mhulls }
  const pendingMeshes = new Set();  // 正在下载的 key，避免重复请求

  let gl = null;
  let meshProg, flatProg;
  let gridBuf = null, gridN = 0, bedBuf = null, bedN = 0;
  let flatVao = null, scratchBuf = null;

  let dpr = 1;
  let lost = false;          // WebGL 上下文丢失（移动端切后台、另开 WebGL 页签都会被回收）
  let retryQueued = false;
  let view = { vp: null, eye: [0, 0, 0], up: [0, 0, 1] };
  let rotAxis = "z";         // 「旋转」当前作用的轴：x / y / z（按钮与拖拽共用）
  // 声明提前：draw() 里要用它跳过拖拽时的占地阴影，不能等事件监听那段才初始化
  let gesture = null;        // { kind: 'orbit'|'pan'|'move'|'rotate'|'scale', ... }

  // ---------------------------------------------------------------- GL 初始化

  function initGL() {
    if (gl) return true;
    if (lost) return false;          // 上下文丢失中，等 restored 再建
    try {
      gl = canvas.getContext("webgl2", { antialias: true, alpha: false, depth: true, powerPreference: "high-performance" });
    } catch (e) { gl = null; }
    if (!gl) {
      // 退路：用 2D 画个提示
      try {
        const c2 = canvas.getContext("2d");
        c2.fillStyle = "#10141a"; c2.fillRect(0, 0, canvas.width, canvas.height);
        c2.fillStyle = "#889";
        c2.font = "13px sans-serif";
        c2.fillText("此设备不支持 WebGL2，3D 摆盘不可用", 12, 24);
      } catch (_) { /* ignore */ }
      return false;
    }
    meshProg = program(gl, MESH_VS, MESH_FS);
    flatProg = program(gl, FLAT_VS, FLAT_FS);

    flatVao = gl.createVertexArray();
    scratchBuf = gl.createBuffer();

    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.enable(gl.CULL_FACE);
    gl.cullFace(gl.BACK);

    return true;
  }

  // ---------------------------------------------------------------- 网格加载

  function meshKey(o) {
    return (o.file || "") + (o.part ? "#" + o.part : "");
  }

  async function loadMesh(o) {
    const key = meshKey(o);
    if (meshCache.has(key)) return meshCache.get(key);
    const url = "/models/" + encodeURIComponent(o.file) + "/mesh" +
      (o.part ? "?part=" + encodeURIComponent(o.part) : "");
    const buf = await fetchMeshBuffer(url);
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    let off = 0;
    const nv = dv.getUint32(off, true); off += 4;
    const nf = dv.getUint32(off, true); off += 4;
    const verts = new Float32Array(nv * 3);
    for (let i = 0; i < nv * 3; i++) { verts[i] = dv.getFloat32(off, true); off += 4; }
    const idx = new Uint32Array(nf * 3);
    for (let i = 0; i < nf * 3; i++) { idx[i] = dv.getUint32(off, true); off += 4; }
    // 局部包围盒
    let b0 = [Infinity, Infinity, Infinity], b1 = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < nv; i++) {
      for (let k = 0; k < 3; k++) {
        const v = verts[i * 3 + k];
        if (v < b0[k]) b0[k] = v;
        if (v > b1[k]) b1[k] = v;
      }
    }
    const mesh = { verts, idx, nv, nf, vao: null, vbo: null, ibo: null,
                   bbox: [b0[0], b0[1], b0[2], b1[0], b1[1], b1[2]], mhulls: new Map() };
    ensureUpload(mesh);
    meshCache.set(key, mesh);
    return mesh;
  }

  // 取网格二进制。踩过的坑：某些情况下 `await r.arrayBuffer()` 会永远不返回
  // （响应 200 已到、逐块读也正常），一旦卡住模型就永远不出现。
  // 所以：流式读取 + 超时，超时再退到 XHR，最后才报错。
  function withTimeout(p, ms, what) {
    return Promise.race([
      p,
      new Promise((_, rej) => setTimeout(() => rej(new Error(what + " 超时")), ms)),
    ]);
  }

  async function readStream(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error("HTTP " + r.status);
    if (!r.body) return new Uint8Array(await r.arrayBuffer());
    const rd = r.body.getReader();
    const chunks = [];
    let total = 0;
    for (;;) {
      const { done, value } = await rd.read();
      if (done) break;
      chunks.push(value);
      total += value.length;
    }
    const out = new Uint8Array(total);
    let off = 0;
    for (const c of chunks) { out.set(c, off); off += c.length; }
    return out;
  }

  function readXHR(url, ms) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest();
      x.open("GET", url);
      x.responseType = "arraybuffer";
      x.timeout = ms;
      x.onload = () => (x.status >= 200 && x.status < 300)
        ? resolve(new Uint8Array(x.response)) : reject(new Error("HTTP " + x.status));
      x.onerror = () => reject(new Error("XHR 网络错误"));
      x.ontimeout = () => reject(new Error("XHR 超时"));
      x.send();
    });
  }

  async function fetchMeshBuffer(url) {
    try {
      return await withTimeout(readStream(url), 15000, "网格读取");
    } catch (e) {
      console.warn("[plate3d] 流式读取失败，改用 XHR", url, e && e.message);
    }
    return await readXHR(url, 20000);
  }

  // 把顶点/索引传到 GPU。上下文还没建好（画布隐藏、上下文丢失后重建）时留空，
  // 下次 draw() 会补传——否则网格会被缓存成「有数据但没有 vao」，永远画不出来。
  function ensureUpload(mesh) {
    if (!gl || mesh.vao) return;
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.verts, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
    const ibo = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.idx, gl.STATIC_DRAW);
    gl.bindVertexArray(null);
    mesh.vao = vao; mesh.vbo = vbo; mesh.ibo = ibo;
  }

  function meshFor(o) { return meshCache.get(meshKey(o)) || null; }

  // ---------------------------------------------------------------- 变换

  function fullRot(o) {
    const R = rotZ(o.rot || 0);
    return o.m && !isIdentity(o.m) ? mulM3(R, o.m) : R;
  }

  // 朝向读写统一走模块级 applyAngles / anglesOf（见文件头约定），
  // 这里只补上「顺手让这件模型的凸包缓存失效」。
  function setAnglesOn(o, e) { applyAngles(o, e, meshFor(o)); }

  function bboxCenter(o) {
    const b = o.bbox || [0, 0, 0, 1, 1, 1];
    return [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2];
  }

  function minZAfterRot(o, mesh, R) {
    const c = bboxCenter(o);
    if (mesh) {
      const v = mesh.verts;
      let mz = Infinity;
      for (let i = 0; i < mesh.nv; i++) {
        const dx = v[i * 3] - c[0], dy = v[i * 3 + 1] - c[1], dz = v[i * 3 + 2] - c[2];
        const qz = R[6] * dx + R[7] * dy + R[8] * dz;
        if (qz < mz) mz = qz;
      }
      return mz;
    }
    // 网格未就绪：用包围盒 8 角近似
    const b = o.bbox || [0, 0, 0, 1, 1, 1];
    let mz = Infinity;
    for (let i = 0; i < 8; i++) {
      const px = (i & 1) ? b[3] : b[0], py = (i & 2) ? b[4] : b[1], pz = (i & 4) ? b[5] : b[2];
      const qz = R[6] * (px - c[0]) + R[7] * (py - c[1]) + R[8] * (pz - c[2]);
      if (qz < mz) mz = qz;
    }
    return mz;
  }

  // 完整模型矩阵 + 辅助量（供绘制与拾取共用，保证一致）
  function objectModel(o) {
    const mesh = meshFor(o);
    const R = fullRot(o);
    const s = o.scale || 1;
    const c = bboxCenter(o);
    const mz = minZAfterRot(o, mesh, R) * s;
    const Rc = applyM3(R, c);
    const tx = (o.x || 0) - s * Rc[0];
    const ty = (o.y || 0) - s * Rc[1];
    const tz = (o.z || 0) - mz - s * Rc[2];
    return { R, s, t: [tx, ty, tz], mat: matModel(R, s, tx, ty, tz) };
  }

  function toLocal(p, T) {
    const invS = 1 / T.s;
    const q = [(p[0] - T.t[0]) * invS, (p[1] - T.t[1]) * invS, (p[2] - T.t[2]) * invS];
    return applyM3(transposeM3(T.R), q);
  }

  // ---------------------------------------------------------------- 占地凸包

  function getMHull(mesh, o, m) {
    const key = m.map(v => Math.round(v * 1000)).join(",");
    if (mesh.mhulls.has(key)) return mesh.mhulls.get(key);
    const c = bboxCenter(o);
    const set = new Set();
    for (let i = 0; i < mesh.nv; i++) {
      const dx = mesh.verts[i * 3] - c[0], dy = mesh.verts[i * 3 + 1] - c[1], dz = mesh.verts[i * 3 + 2] - c[2];
      const px = Math.round((m[0] * dx + m[1] * dy + m[2] * dz) * 100) / 100;
      const py = Math.round((m[3] * dx + m[4] * dy + m[5] * dz) * 100) / 100;
      set.add(px + "," + py);
    }
    const arr = [...set].map(k => { const [x, y] = k.split(","); return [parseFloat(x), parseFloat(y)]; })
      .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const hull = convexHull2d(arr);
    mesh.mhulls.set(key, hull);
    return hull;
  }

  // 对象落地后的 XY 凸包（世界坐标，含旋转缩放平移）
  function footprintWorld(o) {
    const m = o.m && !isIdentity(o.m) ? o.m : null;
    const s = o.scale || 1;
    const a = (o.rot || 0) * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a);
    let base;
    if (!m) {
      const hull = o.hull || [];
      const b = o.bbox || [0, 0, 0, 1, 1, 1];
      const cx = (b[0] + b[3]) / 2, cy = (b[1] + b[4]) / 2;
      base = hull.map(p => [(p[0] - cx) * s, (p[1] - cy) * s]);
    } else {
      const mesh = meshFor(o);
      if (!mesh) return [];
      base = getMHull(mesh, o, m).map(p => [p[0] * s, p[1] * s]);
    }
    return base.map(p => [p[0] * ca - p[1] * sa + (o.x || 0), p[0] * sa + p[1] * ca + (o.y || 0)]);
  }

  // ---------------------------------------------------------------- 拾取

  function screenRay(sx, sy) {
    const w = canvas.clientWidth || 1, h = canvas.clientHeight || 1;
    const e = view.eye;
    const fwd = norm3(sub3(cam.target, e));
    const right = norm3(cross3(fwd, [0, 0, 1]));
    const upv = cross3(right, fwd);
    const tanHalf = Math.tan(FOVY / 2);
    const nx = ((2 * sx) / w - 1) * (w / h) * tanHalf;
    const ny = (1 - (2 * sy) / h) * tanHalf;
    const d = norm3([fwd[0] + right[0] * nx + upv[0] * ny,
                     fwd[1] + right[1] * nx + upv[1] * ny,
                     fwd[2] + right[2] * nx + upv[2] * ny]);
    return { o: e, d };
  }

  function rayTri(ro, rd, mesh) {
    const v = mesh.verts, idx = mesh.idx;
    let bestT = Infinity, bestTri = -1, bestN = null;
    for (let f = 0; f < mesh.nf; f++) {
      const i0 = idx[f * 3], i1 = idx[f * 3 + 1], i2 = idx[f * 3 + 2];
      const ax = v[i0 * 3], ay = v[i0 * 3 + 1], az = v[i0 * 3 + 2];
      const bx = v[i1 * 3], by = v[i1 * 3 + 1], bz = v[i1 * 3 + 2];
      const cx = v[i2 * 3], cy = v[i2 * 3 + 1], cz = v[i2 * 3 + 2];
      const e1x = bx - ax, e1y = by - ay, e1z = bz - az;
      const e2x = cx - ax, e2y = cy - ay, e2z = cz - az;
      const px = rd[1] * e2z - rd[2] * e2y, py = rd[2] * e2x - rd[0] * e2z, pz = rd[0] * e2y - rd[1] * e2x;
      const det = e1x * px + e1y * py + e1z * pz;
      if (det > -1e-9 && det < 1e-9) continue;
      const inv = 1 / det;
      const tx = ro[0] - ax, ty = ro[1] - ay, tz = ro[2] - az;
      const u = (tx * px + ty * py + tz * pz) * inv;
      if (u < 0 || u > 1) continue;
      const qx = ty * e1z - tz * e1y, qy = tz * e1x - tx * e1z, qz = tx * e1y - ty * e1x;
      const vv = (rd[0] * qx + rd[1] * qy + rd[2] * qz) * inv;
      if (vv < 0 || u + vv > 1) continue;
      const t = (e2x * qx + e2y * qy + e2z * qz) * inv;
      if (t > 0 && t < bestT) {
        bestT = t;
        bestTri = f;
        bestN = norm3(cross3([e1x, e1y, e1z], [e2x, e2y, e2z]));
      }
    }
    if (bestTri < 0) return null;
    return { t: bestT, tri: bestTri, normal: bestN };
  }

  // 返回 { i, dist, normal } —— 世界空间距离最近的命中对象
  function pick(sx, sy) {
    const ray = screenRay(sx, sy);
    let best = null;
    for (let i = state.objects.length - 1; i >= 0; i--) {
      const o = state.objects[i];
      const mesh = meshFor(o);
      if (!mesh) continue;
      const T = objectModel(o);
      const ro = toLocal(ray.o, T);
      const rd = norm3(applyM3(transposeM3(T.R), ray.d));
      const hit = rayTri(ro, rd, mesh);
      if (!hit) continue;
      const hl = [ro[0] + rd[0] * hit.t, ro[1] + rd[1] * hit.t, ro[2] + rd[2] * hit.t];
      const hw = applyM3(T.R, [hl[0] * T.s, hl[1] * T.s, hl[2] * T.s]);
      hw[0] += T.t[0]; hw[1] += T.t[1]; hw[2] += T.t[2];
      const dist = Math.hypot(hw[0] - ray.o[0], hw[1] - ray.o[1], hw[2] - ray.o[2]);
      if (!best || dist < best.dist) best = { i, dist, normal: hit.normal };
    }
    if (best) return best;
    // 网格尚未加载：用占地凸包的屏幕投影兜底（可选中/移动，但无法贴面）
    for (let i = state.objects.length - 1; i >= 0; i--) {
      const o = state.objects[i];
      if (meshFor(o)) continue;
      const hull = footprintWorld(o);
      if (hull.length >= 3 && pointInPolyScreen(sx, sy, hull)) {
        const dist = Math.hypot((o.x || 0) - ray.o[0], (o.y || 0) - ray.o[1], (o.z || 0) - ray.o[2]);
        if (!best || dist < best.dist) best = { i, dist, normal: null };
      }
    }
    return best;
  }

  function pointInPolyScreen(sx, sy, hull) {
    const pts = hull.map(p => projectPoint([p[0], p[1], FP_Z]));
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const xi = pts[i][0], yi = pts[i][1], xj = pts[j][0], yj = pts[j][1];
      if ((yi > sy) !== (yj > sy) && sx < ((xj - xi) * (sy - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  // ---------------------------------------------------------------- 绘制

  function buildBed() {
    const b = state.bed;
    const x0 = b.x0 || 0, y0 = b.y0 || 0, w = b.w, d = b.d;
    // 床面填充
    const fill = [];
    if (b.circular) {
      const cx = x0 + w / 2, cy = y0 + d / 2, r = Math.min(w, d) / 2, n = 64;
      for (let i = 1; i < n - 1; i++) {
        const a1 = (i / n) * Math.PI * 2, a2 = ((i + 1) / n) * Math.PI * 2;
        fill.push(cx, cy, BED_Z, cx + r * Math.cos(a1), cy + r * Math.sin(a1), BED_Z,
                  cx + r * Math.cos(a2), cy + r * Math.sin(a2), BED_Z);
      }
    } else {
      fill.push(x0, y0, BED_Z, x0 + w, y0, BED_Z, x0, y0 + d, BED_Z,
                x0 + w, y0, BED_Z, x0 + w, y0 + d, BED_Z, x0, y0 + d, BED_Z);
    }
    bedN = fill.length / 3;
    if (bedBuf) gl.deleteBuffer(bedBuf);
    bedBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, bedBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(fill), gl.STATIC_DRAW);

    // 网格线
    const seg = [];
    const step = w > 300 || d > 300 ? 20 : 10;
    const gx0 = Math.ceil(x0 / step) * step, gy0 = Math.ceil(y0 / step) * step;
    for (let gx = gx0; gx <= x0 + w; gx += step) seg.push(gx, y0, GRID_Z, gx, y0 + d, GRID_Z);
    for (let gy = gy0; gy <= y0 + d; gy += step) seg.push(x0, gy, GRID_Z, x0 + w, gy, GRID_Z);
    // 边框
    if (b.circular) {
      const cx = x0 + w / 2, cy = y0 + d / 2, r = Math.min(w, d) / 2, n = 96;
      for (let i = 0; i < n; i++) {
        const a1 = (i / n) * Math.PI * 2, a2 = ((i + 1) / n) * Math.PI * 2;
        seg.push(cx + r * Math.cos(a1), cy + r * Math.sin(a1), GRID_Z,
                 cx + r * Math.cos(a2), cy + r * Math.sin(a2), GRID_Z);
      }
    } else {
      seg.push(x0, y0, GRID_Z, x0 + w, y0, GRID_Z, x0 + w, y0, GRID_Z, x0 + w, y0 + d, GRID_Z,
               x0 + w, y0 + d, GRID_Z, x0, y0 + d, GRID_Z, x0, y0 + d, GRID_Z, x0, y0, GRID_Z);
    }
    // 原点轴
    const L = Math.min(step * 2, Math.min(w, d) / 4);
    seg.push(x0, y0, GRID_Z, x0 + L, y0, GRID_Z, x0, y0, GRID_Z, x0, y0 + L, GRID_Z);
    gridN = seg.length / 3;
    if (gridBuf) gl.deleteBuffer(gridBuf);
    gridBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, gridBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(seg), gl.STATIC_DRAW);
  }

  function fitCamera() {
    const b = state.bed;
    cam.target = [(b.x0 || 0) + b.w / 2, (b.y0 || 0) + b.d / 2, 0];
    cam.az = -0.85;
    cam.el = 0.85;
    cam.dist = Math.max(b.w, b.d) * 1.6;
    draw();
  }

  function resize() {
    if (!gl) return;
    dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) { scheduleRetry(); return; }   // 画布还没布局出来（刚从隐藏切过来）
    const pw = Math.floor(w * dpr), ph = Math.floor(h * dpr);
    if (canvas.width !== pw || canvas.height !== ph) {
      canvas.width = pw; canvas.height = ph;
    }
    gl.viewport(0, 0, canvas.width, canvas.height);
  }

  // 尺寸为 0 时下一帧再试一次：切视图那一瞬间 layout 可能还没结算，
  // 不重试的话首帧就永远画不出来（表现为「进摆盘只有黑屏，来回切一下才出现」）。
  function scheduleRetry() {
    if (retryQueued) return;
    retryQueued = true;
    requestAnimationFrame(() => {
      retryQueued = false;
      if (!gl) return;
      resize();
      if (canvas.width && canvas.height) draw();
    });
  }

  function computeView() {
    const eye = [
      cam.target[0] + cam.dist * Math.cos(cam.el) * Math.cos(cam.az),
      cam.target[1] + cam.dist * Math.cos(cam.el) * Math.sin(cam.az),
      cam.target[2] + cam.dist * Math.sin(cam.el)];
    view.eye = eye;
    const aspect = (canvas.width / dpr) / Math.max(1, canvas.height / dpr);
    const proj = M4.perspective(FOVY, aspect, Math.max(cam.dist * 0.01, 0.5), cam.dist * 40);
    const v = M4.lookAt(eye, cam.target, [0, 0, 1]);
    view.vp = M4.mul(proj, v);
  }

  function draw() {
    if (!gl) { initGL(); if (!gl) return; }
    if (lost) return;
    if (!bedBuf) buildBed();       // 兜底：确保床面/网格至少建过一次（防止 setBed 早于 gl 初始化）
    resize();
    if (!canvas.width || !canvas.height) return;
    computeView();

    gl.clearColor(0.102, 0.11, 0.125, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.disable(gl.CULL_FACE);

    const vp = view.vp;
    const id = M4.ident();

    // --- 床面 + 网格 ---
    gl.useProgram(flatProg);
    gl.bindVertexArray(flatVao);
    const fu = {
      vp: gl.getUniformLocation(flatProg, "uVP"),
      model: gl.getUniformLocation(flatProg, "uModel"),
      color: gl.getUniformLocation(flatProg, "uColor"),
    };
    gl.uniformMatrix4fv(fu.vp, false, vp);
    gl.uniformMatrix4fv(fu.model, false, id);
    if (bedBuf && bedN) {
      gl.bindBuffer(gl.ARRAY_BUFFER, bedBuf);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      gl.uniform4f(fu.color, 0.145, 0.16, 0.18, 1);
      gl.drawArrays(gl.TRIANGLES, 0, bedN);
    }
    if (gridBuf && gridN) {
      gl.bindBuffer(gl.ARRAY_BUFFER, gridBuf);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      gl.uniform4f(fu.color, 1, 1, 1, 0.07);
      gl.drawArrays(gl.LINES, 0, gridN);
    }

    // --- 占地阴影（先画，被网格遮挡） ---
    // 绕 X/Y 拖拽时朝向每帧都变，重建凸包（要扫全部顶点）会让大模型掉帧；
    // 拖拽期间先不画这一件的阴影，松手后 onUp 会重画一次完整的。
    const skipShadow = gesture && gesture.kind === "rotate" && gesture.axis !== "z" ? gesture.i : -1;
    for (let i = 0; i < state.objects.length; i++) {
      if (i === skipShadow) continue;
      const o = state.objects[i];
      const hull = footprintWorld(o);
      if (hull.length < 3) continue;
      const col = PALETTE[((o.extruder || 1) - 1) % PALETTE.length];
      const rgb = hexRgb(col);
      const pts = [];
      for (let k = 1; k < hull.length - 1; k++) {
        pts.push(hull[0][0], hull[0][1], FP_Z, hull[k][0], hull[k][1], FP_Z, hull[k + 1][0], hull[k + 1][1], FP_Z);
      }
      gl.bindBuffer(gl.ARRAY_BUFFER, scratchBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(pts), gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      const sel = i === state.sel;
      gl.uniform4f(fu.color, rgb[0], rgb[1], rgb[2], sel ? 0.30 : 0.16);
      gl.drawArrays(gl.TRIANGLES, 0, pts.length / 3);
    }

    // --- 3D 网格 ---
    gl.useProgram(meshProg);
    const mu = {
      vp: gl.getUniformLocation(meshProg, "uVP"),
      model: gl.getUniformLocation(meshProg, "uModel"),
      color: gl.getUniformLocation(meshProg, "uColor"),
      light: gl.getUniformLocation(meshProg, "uLight"),
      cam: gl.getUniformLocation(meshProg, "uCam"),
      mode: gl.getUniformLocation(meshProg, "uMode"),
    };
    gl.uniformMatrix4fv(mu.vp, false, vp);
    gl.uniform3fv(mu.light, LIGHT);
    gl.uniform3fv(mu.cam, view.eye);

    for (let i = 0; i < state.objects.length; i++) {
      const o = state.objects[i];
      const mesh = meshFor(o);
      if (!mesh) continue;
      ensureUpload(mesh);
      if (!mesh.vao) continue;    // 上下文还没就绪，下一帧补
      const T = objectModel(o);
      const col = PALETTE[((o.extruder || 1) - 1) % PALETTE.length];
      const rgb = hexRgb(col);
      gl.bindVertexArray(mesh.vao);
      gl.uniformMatrix4fv(mu.model, false, T.mat);
      gl.uniform1f(mu.mode, 0);
      gl.uniform3f(mu.color, rgb[0], rgb[1], rgb[2]);
      gl.drawElements(gl.TRIANGLES, mesh.nf * 3, gl.UNSIGNED_INT, 0);
    }

    // --- 选中对象的包围盒线框 ---
    for (let i = 0; i < state.objects.length; i++) {
      if (i !== state.sel) continue;
      const o = state.objects[i];
      const lines = obbLines(o);
      if (!lines.length) continue;
      gl.useProgram(flatProg);
      gl.bindVertexArray(flatVao);
      gl.uniformMatrix4fv(fu.vp, false, vp);
      gl.uniformMatrix4fv(fu.model, false, id);
      gl.bindBuffer(gl.ARRAY_BUFFER, scratchBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(lines), gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      gl.uniform4f(fu.color, 1, 1, 1, 0.9);
      gl.drawArrays(gl.LINES, 0, lines.length / 3);
    }

    gl.bindVertexArray(null);
  }

  function obbLines(o) {
    const b = o.bbox || [0, 0, 0, 1, 1, 1];
    const T = objectModel(o);
    const corners = [];
    for (let i = 0; i < 8; i++) {
      const px = (i & 1) ? b[3] : b[0], py = (i & 2) ? b[4] : b[1], pz = (i & 4) ? b[5] : b[2];
      const w = applyM3(T.R, [px * T.s, py * T.s, pz * T.s]);
      corners.push(w[0] + T.t[0], w[1] + T.t[1], w[2] + T.t[2]);
    }
    const E = [[0, 1], [0, 2], [0, 4], [1, 3], [1, 5], [2, 3], [2, 6], [3, 7], [4, 5], [4, 6], [5, 7], [6, 7]];
    const out = [];
    for (const [a, b2] of E) out.push(corners[a * 3], corners[a * 3 + 1], corners[a * 3 + 2],
                                        corners[b2 * 3], corners[b2 * 3 + 1], corners[b2 * 3 + 2]);
    return out;
  }

  // ---------------------------------------------------------------- 交互

  const pointers = new Map();
  // gesture 已在文件上部声明（draw 要用）
  let tap = null;       // 用于贴面模式的点按判定

  function setMode(m) {
    if (api.mode === m) return;
    api.mode = m;
    api.onMode && api.onMode(m);
  }

  function localXY(ev) {
    const r = canvas.getBoundingClientRect();
    return [ev.clientX - r.left, ev.clientY - r.top];
  }

  function bedPlanePoint(ray) {
    if (Math.abs(ray.d[2]) < 1e-4) return null;
    const t = -ray.o[2] / ray.d[2];
    if (t < 0) return null;
    return [ray.o[0] + ray.d[0] * t, ray.o[1] + ray.d[1] * t];
  }

  function onDown(ev) {
    if (!gl) return;
    if (!view.vp) { resize(); computeView(); }
    canvas.setPointerCapture && canvas.setPointerCapture(ev.pointerId);
    pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY, startX: ev.clientX, startY: ev.clientY, t0: Date.now() });

    if (pointers.size === 1) {
      const [sx, sy] = localXY(ev);
      const isPan = ev.button === 2 || ev.button === 1 || ev.shiftKey;
      const hit = pick(sx, sy);

      if (ev.button === 2 || ev.button === 1) {
        gesture = { kind: "pan", x: ev.clientX, y: ev.clientY };
        ev.preventDefault();
        return;
      }

      if (api.mode === "layface") {
        tap = { x: sx, y: sy, hit };
        gesture = { kind: "orbit", x: ev.clientX, y: ev.clientY };  // 拖动仍是转视角
        return;
      }

      if (hit) {
        if (hit.i !== state.sel) {
          state.sel = hit.i;
          api.onSelect && api.onSelect(hit.i);
        }
        const o = state.objects[hit.i];
        const T = objectModel(o);
        if (api.mode === "rotate") {
          const ctr = projectPoint([o.x || 0, o.y || 0, o.z || 0]);
          gesture = { kind: "rotate", i: hit.i, axis: rotAxis, y0: ev.clientY,
                      baseAngles: anglesOf(o),
                      baseAng: Math.atan2(sy - ctr[1], sx - ctr[0]) };
        } else if (api.mode === "scale") {
          gesture = { kind: "scale", i: hit.i, baseScale: o.scale || 1, y0: ev.clientY };
        } else {
          const wp = bedPlanePoint(screenRay(sx, sy));
          gesture = { kind: "move", i: hit.i, dx: wp ? (o.x || 0) - wp[0] : 0, dy: wp ? (o.y || 0) - wp[1] : 0 };
        }
        ev.preventDefault();
      } else {
        gesture = isPan ? { kind: "pan", x: ev.clientX, y: ev.clientY }
                        : { kind: "orbit", x: ev.clientX, y: ev.clientY };
      }
    } else if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      gesture = { kind: "pinch", d: Math.hypot(a.x - b.x, a.y - b.y), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2,
                  dist0: cam.dist };
    }
  }

  function projectPoint(p) {
    const vp = view.vp;
    const x = vp[0] * p[0] + vp[4] * p[1] + vp[8] * p[2] + vp[12];
    const y = vp[1] * p[0] + vp[5] * p[1] + vp[9] * p[2] + vp[13];
    const w = vp[3] * p[0] + vp[7] * p[1] + vp[11] * p[2] + vp[15];
    if (Math.abs(w) < 1e-6) return [canvas.clientWidth / 2, canvas.clientHeight / 2];
    const ndcX = x / w, ndcY = y / w;
    return [(ndcX + 1) / 2 * canvas.clientWidth, (1 - ndcY) / 2 * canvas.clientHeight];
  }

  function pan(dx, dy) {
    const scale = (2 * cam.dist * Math.tan(FOVY / 2)) / (canvas.clientHeight || 780);
    const right = [-Math.sin(cam.az), Math.cos(cam.az), 0];
    const up = [-Math.sin(cam.el) * Math.cos(cam.az), -Math.sin(cam.el) * Math.sin(cam.az), Math.cos(cam.el)];
    cam.target[0] += (-right[0] * dx + up[0] * dy) * scale;
    cam.target[1] += (-right[1] * dx + up[1] * dy) * scale;
    cam.target[2] += (-right[2] * dx + up[2] * dy) * scale;
  }

  function onMove(ev) {
    const p = pointers.get(ev.pointerId);
    if (!p) return;
    const dx = ev.clientX - p.x, dy = ev.clientY - p.y;
    p.x = ev.clientX; p.y = ev.clientY;

    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      if (gesture && gesture.kind === "pinch") {
        cam.dist = Math.max(20, Math.min(6000, gesture.dist0 * (gesture.d / Math.max(d, 1))));
        pan(cx - gesture.cx, cy - gesture.cy);
      }
      gesture = { kind: "pinch", d, cx, cy, dist0: cam.dist };
      draw();
      return;
    }

    if (!gesture) return;
    if (gesture.kind === "orbit") {
      cam.az -= dx * 0.0075;
      cam.el = Math.max(-1.5, Math.min(1.5, cam.el + dy * 0.0075));
      draw();
    } else if (gesture.kind === "pan") {
      pan(dx, dy);
      draw();
    } else if (gesture.kind === "move") {
      const o = state.objects[gesture.i];
      if (!o) return;
      const [sx, sy] = localXY(ev);
      const wp = bedPlanePoint(screenRay(sx, sy));
      if (wp) {
        o.x = Math.round((wp[0] + gesture.dx) * 10) / 10;
        o.y = Math.round((wp[1] + gesture.dy) * 10) / 10;
        draw();
      }
    } else if (gesture.kind === "rotate") {
      const o = state.objects[gesture.i];
      if (!o) return;
      let delta;
      if (gesture.axis === "z") {
        // 绕 Z（转盘方向）：按鼠标相对对象中心的方位角走，最跟手
        const [sx, sy] = localXY(ev);
        const ctr = projectPoint([o.x || 0, o.y || 0, o.z || 0]);
        const ang = Math.atan2(sy - ctr[1], sx - ctr[0]);
        delta = (ang - gesture.baseAng) * 180 / Math.PI;
      } else {
        // 绕 X/Y（把模型立起来/歪过去）：上下拖拽，0.6°/px
        delta = (ev.clientY - gesture.y0) * 0.6;
      }
      const e = { ...gesture.baseAngles };
      e["r" + gesture.axis] += delta;
      setAnglesOn(o, e);
      draw();
    } else if (gesture.kind === "scale") {
      const o = state.objects[gesture.i];
      if (!o) return;
      const factor = 1 - (ev.clientY - gesture.y0) * 0.004;
      o.scale = Math.max(0.05, Math.min(10, gesture.baseScale * factor));
      draw();
    }
  }

  function onUp(ev) {
    pointers.delete(ev.pointerId);
    if (pointers.size < 2 && gesture && gesture.kind === "pinch") gesture = null;

    if (tap && pointers.size === 0) {
      const [sx, sy] = localXY(ev);
      const moved = Math.hypot(sx - tap.x, sy - tap.y);
      if (moved < 6) {
        // 点按：贴面
        const hit = tap.hit || pick(sx, sy);
        if (hit && hit.normal) {
          state.sel = hit.i;
          api.onSelect && api.onSelect(hit.i);
          const o = state.objects[hit.i];
          // 贴面：把「该面朝下」的矩阵反解成三轴角度存起来（凸包缓存由 applyAngles 负责失效）
          setAnglesOn(o, eulerOf(alignNegZ(hit.normal)));
          api.onChange && api.onChange(o, hit.i);
          api.onLayFace && api.onLayFace(hit.i);
        }
      }
      tap = null;
      setMode("move");
      draw();
      return;
    }
    tap = null;

    const wasGesture = gesture;
    if (pointers.size === 0 && gesture && (gesture.kind === "move" || gesture.kind === "rotate" || gesture.kind === "scale")) {
      const i = gesture.i;
      gesture = null;
      api.onChange && api.onChange(state.objects[i], i);
    }
    if (pointers.size === 0) {
      gesture = null;
      draw();     // 拖动期间可能跳过了阴影/凸包，松手补一帧完整的
    }
    void wasGesture;
  }

  function onWheel(ev) {
    ev.preventDefault();
    cam.dist = Math.max(20, Math.min(6000, cam.dist * (1 + Math.sign(ev.deltaY) * 0.12)));
    draw();
  }

  function onCtx(ev) { ev.preventDefault(); }

  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  canvas.addEventListener("contextmenu", onCtx);

  let ro = null;
  if (window.ResizeObserver) {
    ro = new ResizeObserver(() => { if (gl) { resize(); draw(); } });
    ro.observe(canvas);
  }

  // 上下文丢失是「画布还在、但一片空白」的头号成因：手机切后台、
  // 同时存在多个 WebGL 页面（比如预览页的 viewer）都可能触发。
  canvas.addEventListener("webglcontextlost", (ev) => {
    ev.preventDefault();          // 阻止默认行为才能拿到 restored
    lost = true;
  });
  canvas.addEventListener("webglcontextrestored", () => {
    lost = false;
    gl = null;                    // 旧句柄全失效，重建
    bedBuf = null; gridBuf = null; flatVao = null; scratchBuf = null;
    meshCache.forEach(m => { m.vao = null; m.vbo = null; m.ibo = null; });
    if (initGL()) { buildBed(); draw(); }
  });
  // 从后台切回来：尺寸可能变了、帧可能被丢弃，无条件重画一次
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && gl) { resize(); draw(); }
  });
  window.addEventListener("pageshow", () => { if (gl) { resize(); draw(); } });

  // ---------------------------------------------------------------- 对外 API

  let retryTimer = 0, meshRetries = 0;

  function refreshMeshes() {
    const seen = new Set();
    state.objects.forEach(o => {
      if (!o.file) return;
      const key = meshKey(o);
      if (seen.has(key)) return;
      seen.add(key);
      if (meshCache.has(key) || pendingMeshes.has(key)) return;
      pendingMeshes.add(key);
      loadMesh(o)
        .then(() => { meshRetries = 0; draw(); })
        .catch((e) => { console.warn("[plate3d] 网格加载失败", key, e && e.message); })
        .finally(() => pendingMeshes.delete(key));
    });
    draw();
    // 全都失败时不要让摆盘一直空着：隔 1.5s 自己再试，最多 3 次
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      const missing = state.objects.some(o => o.file && !meshCache.has(meshKey(o)));
      if (!missing || pendingMeshes.size || document.hidden || meshRetries >= 3) return;
      meshRetries++;
      refreshMeshes();
    }, 1500);
  }

  return {
    api,
    setBed(bed) {
      state.bed = {
        w: bed.w || 200, d: bed.d || 200,
        x0: bed.x0 || 0, y0: bed.y0 || 0,
        circular: !!bed.circular,
      };
      if (!gl) initGL();          // 首屏时画布可能还隐藏，先建好上下文再建床
      if (gl) buildBed();
      fitCamera();
    },
    setObjects(list, sel) {
      state.objects = list || [];
      state.sel = sel != null ? sel : -1;
      refreshMeshes();
    },
    select(i) { state.sel = i; draw(); },
    redraw: draw,

    // ---- 三轴旋转 ----
    setRotAxis(axis) { rotAxis = (axis === "x" || axis === "y") ? axis : "z"; },
    getRotAxis() { return rotAxis; },
    // 当前朝向拆成 (rx, ry, rz)，单位度；R = Rz(rz)·Ry(ry)·Rx(rx)
    anglesOf(i) { return anglesOf(state.objects[i]); },
    // 直接给定三轴角度（侧面数字输入用）
    setAngles(i, e) {
      const o = state.objects[i];
      if (!o) return null;
      setAnglesOn(o, e);
      draw();
      return anglesOf(o);
    },
    // 单轴增量旋转（按钮/Shift 快捷键用）：角度直接累加，数值不会漂
    rotateAxis(i, axis, deg) {
      const o = state.objects[i];
      if (!o) return null;
      if (deg) {
        const e = anglesOf(o);
        e["r" + axis] = (e["r" + axis] || 0) + deg;
        setAnglesOn(o, e);
        draw();
      }
      return anglesOf(o);
    },
    resetOrientation(i) {
      const o = state.objects[i];
      if (!o) return;
      setAnglesOn(o, { rx: 0, ry: 0, rz: 0 });
      draw();
    },
    // 不能写成 `if (gl)`：首次进入摆盘时 gl 可能还没建，会被永久跳过。
    // 顺手补拉缺的网格——首帧如果因为时序/网络没拉下来，这次进来能自愈。
    refresh() { if (!gl && !initGL()) return; resize(); refreshMeshes(); },
    setTransformMode(mode) { setMode(mode); },
    beginLayFace() {
      if (state.sel < 0) return false;
      setMode("layface");
      return true;
    },
    cancelLayFace() { setMode("move"); },
    resetView() { fitCamera(); },
    isReady() { return !!gl; },
    // 排障用：一次看清 GL 上下文、网格是否上传、画布尺寸（3D 不显示时先看这里）
    debug() {
      return {
        gl: !!gl,
        bed: !!bedBuf,
        canvas: { w: canvas.width, h: canvas.height,
                  cw: canvas.clientWidth, ch: canvas.clientHeight, dpr },
        objects: state.objects.map(o => ({ key: meshKey(o), x: o.x, y: o.y, rot: o.rot,
                                           angles: anglesOf(o), m: o.m })),
        meshes: [...meshCache.entries()].map(([k, m]) => ({ key: k, vao: !!m.vao, nv: m.nv, nf: m.nf })),
        pending: [...pendingMeshes],
      };
    },
    // 排障用：立刻重画一帧并统计像素，确认「真的画出来了」而不只是状态齐全
    probe() {
      if (!gl) return { gl: false };
      draw();
      const w = canvas.width, h = canvas.height;
      const px = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
      const bg = [Math.round(0.102 * 255), Math.round(0.11 * 255), Math.round(0.125 * 255)];
      let lit = 0, minX = w, maxX = -1, minY = h, maxY = -1;
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const i = (y * w + x) * 4;
          if (Math.abs(px[i] - bg[0]) + Math.abs(px[i + 1] - bg[1]) + Math.abs(px[i + 2] - bg[2]) > 12) {
            lit++;
            if (x < minX) minX = x; if (x > maxX) maxX = x;
            if (y < minY) minY = y; if (y > maxY) maxY = y;
          }
        }
      }
      return { gl: true, w, h, lit, ratio: +(lit / (w * h)).toFixed(4),
               bbox: maxX < 0 ? null : [minX, minY, maxX, maxY] };
    },
  };
}

// 朝向辅助函数导出：单元测试用（tests/rot3_math.mjs），浏览器只走 createPlate3D
export { anglesOf, applyAngles, eulerOf };
