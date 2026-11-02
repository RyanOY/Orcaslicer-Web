// 摆盘 3D 的「无浏览器」冒烟测试：用最小 DOM 桩把 createPlate3D 建起来，
// 走一遍 app.js 会用到的那几个接口（anglesOf / setAngles / rotateAxis / resetOrientation）。
// 注意：不验证渲染（没有 WebGL），只验证状态与朝向数据是对的。
//   node tests/plate3d_smoke.mjs
const listeners = {};
const canvas = {
  width: 0, height: 0, clientWidth: 800, clientHeight: 600, style: {},
  addEventListener: () => {},
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
  getContext: () => null,        // 没有 WebGL → 走「不支持」的兜底分支
};
globalThis.document = { addEventListener: () => {}, hidden: false };
globalThis.window = { addEventListener: () => {}, devicePixelRatio: 1 };
globalThis.ResizeObserver = undefined;

const { createPlate3D } = await import("../web/plate3d.js");
const plate = createPlate3D(canvas, {});

const obj = { file: "", part: "", name: "obj", x: 100, y: 100, z: 0, rot: 0, rx: 0, ry: 0, rz: 0,
              scale: 1, extruder: 1 };
plate.setBed({ w: 350, d: 350 });
plate.setObjects([obj], 0);

let fails = 0;
const ok = (name, cond, extra) => {
  if (!cond) { fails++; console.log("FAIL", name, extra === undefined ? "" : JSON.stringify(extra)); }
  else console.log("ok  ", name);
};

ok("初始角度", JSON.stringify(plate.anglesOf(0)) === '{"rx":0,"ry":0,"rz":0}', plate.anglesOf(0));

plate.rotateAxis(0, "x", 90);
ok("X 转 90° 后角度", plate.anglesOf(0).rx === 90, plate.anglesOf(0));
ok("X 转 90° 写入 m（rot 仍 0）", obj.rot === 0 && Array.isArray(obj.m) && obj.m.length === 9, obj);
ok("m = Rx(90)", Math.abs(obj.m[4]) < 1e-12 && Math.abs(obj.m[5] + 1) < 1e-12 &&
  Math.abs(obj.m[7] - 1) < 1e-12, obj.m);

plate.rotateAxis(0, "z", 15);
ok("再绕 Z 转 15°", plate.anglesOf(0).rz === 15 && plate.anglesOf(0).rx === 90, plate.anglesOf(0));
ok("rot 跟随 rz", obj.rot === 15, obj.rot);

const got = plate.setAngles(0, { rx: 0, ry: 0, rz: 0 });
ok("三轴全 0 → 复位（m 清空）", got.rz === 0 && obj.m === null && obj.rot === 0, obj);

plate.setRotAxis("y");
ok("旋转轴切换", plate.getRotAxis() === "y");
plate.rotateAxis(0, plate.getRotAxis(), 45);
ok("Y 轴 45° 只动 ry", plate.anglesOf(0).ry === 45 && plate.anglesOf(0).rx === 0, plate.anglesOf(0));

plate.resetOrientation(0);
ok("复位朝向", JSON.stringify(plate.anglesOf(0)) === '{"rx":0,"ry":0,"rz":0}', plate.anglesOf(0));

// 调试接口应能带出三轴角度（排障时靠它看状态）
const dbg = plate.debug();
ok("debug 输出 angles", dbg.objects[0] && dbg.objects[0].angles && dbg.objects[0].angles.ry === 0, dbg.objects);

console.log(fails ? `\n${fails} 项失败` : "\n全部通过");
process.exit(fails ? 1 : 0);
