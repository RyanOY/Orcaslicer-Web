// 摆盘三轴旋转的纯逻辑测试（不依赖浏览器）：
//   node tests/rot3_math.mjs
// 覆盖：欧拉角 ↔ 矩阵互转、角度作为权威状态不漂移、贴面 → 角度分解、
//       rot/m 存储与「R = Rz(rot)·m」的服务端约定一致。
import { anglesOf, applyAngles, eulerOf } from "../web/plate3d.js";

const D = Math.PI / 180;
const rotZ = (d) => [Math.cos(d * D), -Math.sin(d * D), 0, Math.sin(d * D), Math.cos(d * D), 0, 0, 0, 1];
const rotX = (d) => [1, 0, 0, 0, Math.cos(d * D), -Math.sin(d * D), 0, Math.sin(d * D), Math.cos(d * D)];
const rotY = (d) => [Math.cos(d * D), 0, Math.sin(d * D), 0, 1, 0, -Math.sin(d * D), 0, Math.cos(d * D)];
const mul = (a, b) => {
  const o = new Array(9);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++)
    o[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c];
  return o;
};
// 服务端等价实现（app/mesh.py + app/plate.py 的 _rot_matrix）
const serverR = (o) => (o.m ? mul(rotZ(o.rot || 0), o.m) : rotZ(o.rot || 0));
const same = (a, b, eps = 1e-9) => a.every((v, i) => Math.abs(v - b[i]) < eps);
const expectR = (e) => mul(mul(rotZ(e.rz), rotY(e.ry)), rotX(e.rx));

let fails = 0;
function ok(name, cond, extra) {
  if (!cond) { fails++; console.log("FAIL", name, extra === undefined ? "" : extra); }
  else console.log("ok  ", name);
}

// 1. 新对象从 0 开始
const o = { rot: 0, m: null };
ok("初始角度全 0", JSON.stringify(anglesOf(o)) === JSON.stringify({ rx: 0, ry: 0, rz: 0 }));

// 2. 绕 X 累加 15°×6，角度不漂、矩阵= Rx(90)
for (let i = 0; i < 6; i++) {
  const e = anglesOf(o); e.rx += 15; applyAngles(o, e, null);
}
ok("RX 累加到 90", anglesOf(o).rx === 90, JSON.stringify(anglesOf(o)));
ok("RX=90 时 R=Rx(90)", same(serverR(o), rotX(90)), JSON.stringify(serverR(o)));
ok("RX=90 时 m 对称存储 (rot=0)", o.rot === 0 && o.m !== null);

// 3. 再绕 Z 转 30°：RZ 单独记账，RX 仍是 90
{
  const e = anglesOf(o); e.rz += 30; applyAngles(o, e, null);
  const a = anglesOf(o);
  ok("RX 稳定 90 / RZ=30", a.rx === 90 && a.rz === 30, JSON.stringify(a));
  ok("矩阵 = Rz30·Rx90", same(serverR(o), expectR(a)));
}

// 4. 三轴混合：R = Rz(rz)·Ry(ry)·Rx(rx)
{
  const e = { rx: 12.5, ry: -37.5, rz: 200 };
  applyAngles(o, e, null);
  ok("三轴混合矩阵一致", same(serverR(o), expectR(e)));
  ok("角度原样保存（不重建）", JSON.stringify(anglesOf(o)) === JSON.stringify({ rx: 12.5, ry: 322.5, rz: 200 }));
}

// 5. 全部归零 → m 清空、回到纯 Z 分支（旧数据/快路径）
{
  applyAngles(o, { rx: 0, ry: 0, rz: 0 }, null);
  ok("归零后 m=null、rot=0", o.m === null && o.rot === 0, JSON.stringify(o));
}

// 6. 贴面：把 +X 转到朝下，反解出的角度再合成应还原同一个矩阵
{
  const axis = [0, 1, 0], ang = Math.PI / 2;   // cross((1,0,0),(0,0,-1)) 归一化后就是 +Y
  const c = Math.cos(ang), s = Math.sin(ang), t = 1 - c;
  const [x, y, z] = axis;
  const M = [t * x * x + c, t * x * y - s * z, t * x * z + s * y,
             t * x * y + s * z, t * y * y + c, t * y * z - s * x,
             t * x * z - s * y, t * y * z + s * x, t * z * z + c];
  const p = { rot: 0, m: null };
  applyAngles(p, eulerOf(M), null);
  ok("贴面矩阵还原", same(serverR(p), M), JSON.stringify(serverR(p)));
  const a = anglesOf(p);
  ok("贴面后角度可直接编辑（RX/RY/RZ 有值）", a.rx === 0 && a.ry === 90 && a.rz === 0, JSON.stringify(a));
}

// 7. 老数据（只有 rot，或 rot+贴面 m）第一次读角度 = 分解结果，且矩阵不变
{
  const legacy = { rot: 25, m: rotX(90) };
  const before = serverR(legacy);
  const a = anglesOf(legacy);
  applyAngles(legacy, a, null);
  ok("老数据角度化不改变朝向", same(serverR(legacy), before, 1e-9));
}

// 8. 万向锁不炸：ry=±90 时仍可编辑
{
  const p = { rot: 0, m: null };
  applyAngles(p, { rx: 33, ry: 90, rz: 0 }, null);
  const a = anglesOf(p);
  ok("RY=90 可存可读", a.ry === 90 && a.rx === 33, JSON.stringify(a));
  applyAngles(p, { rx: 10, ry: 90, rz: 45 }, null);
  ok("RY=90 时 RZ 仍生效", same(serverR(p), expectR({ rx: 10, ry: 90, rz: 45 })));
}

console.log(fails ? `\n${fails} 项失败` : "\n全部通过");
process.exit(fails ? 1 : 0);
