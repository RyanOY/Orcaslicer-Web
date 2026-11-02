# slicer-node HTTP 接口（as-built，v0.10.4）

> 这份是**实际实现**的规范，不是早期草案。早期设想的 `/v1` 前缀、token 鉴权、
> 两步式上传、artifact ETag 等都没有实现（见文末「与早期草案的差异」）。
> 服务同时把前端（`/web`、`/viewer`）一起发出来，所以接口和页面同源、无 CORS。

- 基地址：`http://<nas>:8039`
- 编码：JSON；二进制用 `application/octet-stream`
- 时间：Unix 秒（`created_at` / `started_at` / `finished_at`）
- 错误：`HTTPException(status, "中文说明")` → `{"detail": "..."}`
- 鉴权：**无**。只在内网用，不要暴露到公网。

---

## 1. 基础

### `GET /health`
```json
{"ok": true, "version": "0.10.4", "orca": "OrcaSlicer-2.4.2",
 "orca_bin": "/opt/orca/bin/orca-slicer",
 "profiles": {"machine": ["voron24-350", "..."], "process": ["..."], "filament": ["..."]},
 "queue": {"running": 0, "pending": 0}, "schema_params": 774, "models": 12}
```

### `GET /schema`
`{"params": {key: {t, label, tip, unit, min, max, def, kinds[], zh, tip_zh}}, "order": [...]}`
`t` 取值：`bool|int|float|percent|float_or_percent|string|enum|floats|ints|percents|strings|enums|bools|point|points|pointsgroups`。
**写配置时值要按 t 转成字符串**（`ints` → `["48"]`），这是 Orca 的硬性要求。

---

## 2. Profile（机器 / 工艺 / 耗材）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/profiles` | `{kind: [name...]}` |
| GET | `/profiles/{kind}/{name}` | 单个文档（已清洗） |
| PUT | `/profiles/{kind}/{name}` | 保存到**用户层** `/data/profiles/<kind>/<name>.json`；返回 `{name, kind, path, warnings, shadowing_baked}` |
| POST | `/profiles/{kind}/{name}/clone` | body `{to: "新名字"}` |
| DELETE | `/profiles/{kind}/{name}` | 只能删用户层；内置的会 400 |
| GET | `/profiles/{kind}/{name}/export` | 原样 JSON 下载 |
| POST | `/profiles/{kind}/import` | multipart `file`，吃 SimplyPrint `export-full` / Orca preset / 扁平 JSON |
| GET | `/profiles/{kind}/{name}/bed` | 机器配置里解析出的热床：`{w,d,x0,y0,circular,h,nozzles,extruders,mmu,points}` |

`kind ∈ {machine, process, filament}`。读取优先级：用户层 > 镜像内置。

---

## 3. 模型库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/models` | `{models: [...], dir, extra_dirs}` |
| POST | `/models` | multipart `file`，201 |
| GET | `/models/{name}/info?refresh=1` | 尺寸/面数/凸包；3MF 附 `parts: [{id,name,w,d,h,tris}]` |
| GET | `/models/{name}/mesh?part=<id>` | 二进制网格：`uint32 顶点数 · uint32 面数 · float32×3×顶点 · uint32×3×面`（小端）；>2 KB 时 gzip；`Cache-Control: 86400` |
| DELETE | `/models/{name}` | |

---

## 4. 摆盘

**对象（plate object）**，四个 `/plate/*` 接口共用：

```json
{"file": "Dyson+hair+dryer.3mf", "part": "2", "name": "部件 1",
 "x": 103, "y": 105, "z": 0, "rot": 0, "scale": 0.95, "extruder": 1,
 "m": [1,0,0, 0,1,0, 0,0,1]}
```
- `x/y` 是对象足迹中心在床面上的毫米坐标；`z` 是抬升；`rot` 绕世界 Z（度，取模 360）。
- `m` 可选，9 个数，行主序 3×3 —— 「选底面贴床」算出来的姿态，先于 `rot` 应用。
- `part` 为空 = 整个文件合并网格；否则是 3MF 里某个 `<item>` 的 id。

| 方法 | 路径 | 请求 → 响应 |
| --- | --- | --- |
| POST | `/plate/arrange` | `{objects, bed, gap?, filaments?[], overrides?}` → `{objects}` 自动摆放 |
| POST | `/plate/fit` | `{objects, bed, gap?, filaments?[], overrides?}` → `{all_fit, factor, objects}` 二分求最大统一缩放 |
| POST | `/plate/check` | `{objects, bed, filaments?[], overrides?}` → `{warnings: [str]}` |
| POST | `/plate/slice` | `{machine, process, filaments[], objects[], overrides{}}` → 202 `{job_id, status, poll, plate}` |

`/plate/slice` 的 `plate` 回包：
```json
{"path": "...", "bytes": 812345, "extruders_used": [1, 4],
 "objects": [{"file": "...", "extruder": 1, "bbox": [x0,y0,z0,x1,y1,z1]}]}
```

`bed` 结构见 `/profiles/{kind}/{name}/bed`。

**收缩率补偿**：`filaments[i]` 的 `filament_shrink`（XY）和
`filament_shrinkage_compensation_z`（Z）会在拼 3MF 时直接乘到该槽对象的世界坐标上
（`plate.shrink_of()`），`overrides` 里的同名 key 优先。所以回包里的 `bbox` 已经是
**补偿后**的尺寸；Orca 自己的 `filament_shrink` 在 headless CLI 下不生效（见
`changelog.md` v0.11.5）。

摆盘的三个接口（`arrange` / `fit` / `check`）也接受 `filaments[]` + `overrides{}`：
服务端用同一套 `shrink_of()` 把 XY 系数折进对象的 `scale`、Z 系数单独记账，
所以排布间距、边界/重叠检查、一键缩放比例都按**切片后的真实尺寸**算。
不传（老客户端）就按原始尺寸，行为与 0.11.5 之前一致。

---

## 5. 切片任务

### `POST /slice`（单文件，兼容最早接口）
multipart：`file`、`machine`、`process`、`filament?`（单个）、`overrides?`（JSON 字符串）→ 202 `{job_id, status, poll}`

### 任务对象（`GET /jobs`、`GET /jobs/{id}`）
```json
{"id": "cfceac80e156", "model_name": "plate(2).3mf", "machine": "voron24-350",
 "process": "020-standard", "filament": "generic-pla,generic-pla,generic-pla,jayo-pla",
 "overrides": {...}, "status": "done|queued|running|failed|cancelled",
 "created_at": 1793328376, "started_at": ..., "finished_at": ..., "duration": 8.45,
 "error": null,
 "summary": {"return_code": 0, "error_string": null, "layer_height": 0.2,
             "layers": 285, "gcode_bytes": 12345678, "sliced_time": 480,
             "triangle_count": 6818, "warning_message": null, "preview_error": null},
 "preview_status": "pending|ready|off|failed",
 "gcode_url": "/jobs/{id}/gcode", "preview_url": "/jobs/{id}/preview"}
```
`GET /jobs?limit=N` → `{jobs: [...], stats: {...}}`

### 产物
| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/jobs/{id}/gcode` | 下载 gcode |
| GET | `/jobs/{id}/log` | `{log: "尾部 20000 字符"}` |
| GET | `/jobs/{id}/preview` | 层索引（见下），>1 KB 时 gzip，`max-age=3600` |
| GET | `/jobs/{id}/preview/bin` | 整个二进制流，支持 `Range`，`max-age=86400` |
| GET | `/jobs/{id}/preview/{layer}?types=0,3` | 单层二进制；响应头带 `X-Segments`、`X-Z` |
| POST | `/jobs/{id}/preview` | 手动（重）建预览 |
| DELETE | `/jobs/{id}` | 运行中的会 409 |

**层索引**：
```json
{"version": 7, "stride": 24, "z": [...],
 "layers": [{"z": 0.2, "count": 1434, "offset": 0,
             "ranges": {"0": [start, count], "3": [...]}, "bbox": [...]}],
 "attrs": [[tool, speed, fan, temp, accel, jerk], ...],
 "attr_ranges": {"speed": [11, 200], "vact": [0, 190.2], "aeff": [0, 5000], ...},
 "attr_avail": {"speed": true, "vact": true, "aeff": true, ...},
 "kinematics": {"accel": 5000, "scv": 12, "accel_seen": true, "scv_seen": true},
 "summary": {...}}
```

**单层二进制（v7，每段 24 字节，按段交错）**：
| 偏移（每段内） | 类型 | 内容 |
| --- | --- | --- |
| 0 | Float32×4 | x1,y1,x2,y2（毫米，gcode 原始坐标） |
| 16 | Uint8 | 走线类型码 |
| 17 | Uint8 | 挤出截面积 / AREA_STEP（量化） |
| 18 | Uint16 | attrs 下标 |
| 20 | Uint16 | 真实速度 ×100（mm/s，Klipper lookahead 回放的结果） |
| 22 | Uint16 | 该段有效加速度（mm/s²） |

层内记录仍按类型桶排序（`layers[i].ranges[type] = [start, count]`），
`?types=0,3` 时服务端按 `offset + start*stride` seek 出连续区间返回。

`attrs` 里的 `speed/accel` 是 **gcode 指令值**（G1 F、M204/`SET_VELOCITY_LIMIT`）；
偏移 20/22 的两列是**机器实际会跑的速度/加速度** —— 按 `max_accel` 与
`square_corner_velocity` 对整条路径做前向/反向梯形规划得到，
所以转角处会明显掉速、长直段才冲到指令速度。两列逐段不同、拼不出小表，
因此不进 attrs 表而是单独 uint16 通道。

老版本：v6 = 20 B/段（无后两列），v5 是「16n 位置 + n 类型 + n 截面 + 2n 属性」
平面分组，服务端读出来会**统一重排成交错布局**再返回，所以客户端只有一条读取路径；
v4 无 attrs（18 B/段），v<4 无截面（17 B/段）。前端据 `version`/`stride` 置灰对应着色依据。

---

## 6. 打印（Moonraker）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/printer` | `{url, name, upload_dir, api_key: "***", configured}` |
| PUT | `/printer` | 更新 `url/name/upload_dir/api_key`（传 `***` 表示不改 key） |
| POST | `/printer/test` | 502 = 连不上 |
| POST | `/jobs/{id}/print` | `{filename?, start?: bool}` → `{uploaded: {path}, started, start?}` |

`start=true` 才真的开始打印，UI 上有二次确认。开始打印时会记下 `pending`
（job id + 远端文件名），打印结束后 `/spools/sync` 靠它把用量接回本地任务。

### 6.1 实时状态与控制

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/printer/live` | 实时快照，见下 |
| POST | `/printer/gcode` | `{script}` → 走 `/printer/gcode/script`，上限 8000 字符 |
| POST | `/printer/temps` | `{bed?: number, extruders?: number[]}` → `M140` / `M104 T<i>` |
| POST | `/printer/jog` | `{axis: "X"/"Y"/"Z"/"E", delta, feedrate?}`，单次 ≤100 mm，E 走 `M83` 相对模式 |
| POST | `/printer/action` | `{action: pause/resume/cancel/home/home_xy/firmware_restart/emergency_stop}` |
| GET | `/printer/history?limit=10` | Moonraker `/server/history/list` 的裁剪版 |

`/printer/live` 返回（Klipper 缺哪个对象就少哪个字段，不会 500）：

```json
{
  "state": "standby", "state_message": "", "filename": "",
  "progress": 0.0, "print_duration": 0, "total_duration": 0,
  "filament_used_mm": 0, "layer": {"current": null, "total": null},
  "extruders": [{"name": "extruder", "temp": 20.4, "target": 0}],
  "bed": {"temp": 21.2, "target": 0}, "chamber": {"name": "acepro_temp_1", "temp": 58, "target": 0},
  "position": [0, 0, 0], "homed_axes": "",
  "speed_factor": 100, "extrude_factor": 100, "fan": 0,
  "ace": {"enabled": true, "current": 3, "temp": 58, "humidity": 7,
          "dryer": {"status": "drying", "target": 58, "remain_s": 10617},
          "slots": [{"index": 3, "status": "ready", "color": "#000000", "material": "Unknown"}]}
}
```

### 6.2 料盘账本

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/spools` | `{spools, slots:[{slot, spool}], records, pending, total_m}` |
| POST | `/spools` | 新建，见下 |
| PUT | `/spools/{id}` | 同字段可改；`slot: null` 卸料；`archived: true` 归档 |
| DELETE | `/spools/{id}` | 删料盘（历史记录保留） |
| POST | `/spools/{id}/assign` | `{slot: 0..7}` 或 `{slot: null}`；一个槽位只能装一卷 |
| POST | `/spools/{id}/adjust` | `{remaining_m}` / `{used_m}` / `{delta_m}` 三选一 |
| POST | `/spools/record` | `{job_id}` 手工把某任务的用量记进账本 |
| POST | `/spools/sync` | `{limit?}` 拉 Moonraker 历史，补记还没记过的打印 |
| GET | `/jobs/{id}/usage` | 这个任务按工具头的用量 + 会扣到哪个料盘 |

料盘字段：`name, material, vendor, color, diameter, density, total_m, remaining_m,
spool_weight_g, filament, note, slot`（`filament` 是绑定的切片耗材配置名）。

`/spools/sync` 的记账口径：**总量用 Moonraker 的 `print_stats/history.filament_used`**
（真实消耗），**分摊比例用我们自己扫 gcode 的 per-tool 比例**。远端文件名形如
`<model>-<12位jobid>.gcode`，靠后缀把打印记录接回本地任务（能拿到 gcode 才算得准）；
对不上就整包记到 ACE 当前槽位。重复调用不会重复扣减（按 Moonraker `job_id` 去重）。

---

## 7. 静态资源

| 路径 | 内容 |
| --- | --- |
| `/` `/web/*` | 工作台（`index.html` + `app.js` / `plate.js` / `plate3d.js` / `form.js` / `style.css` / `print.css`） |
| `/viewer/` | 层预览页（可独立打开，也可被工作台 iframe 嵌入） |

---

## 8. 与最初协议草案的差异（草案已废弃，仅留此表追溯）

| 草案 | 实际 |
| --- | --- |
| `/v1` 前缀 | 无前缀 |
| `X-Slicer-Token` 鉴权 | 无鉴权（内网） |
| 两步式上传（先 create 再 PUT 字节） | 单次 multipart |
| artifact 带 ETag / 过期清理 | 直接落盘，`SLICER_JOB_TTL=0` 不清理 |
| 独立 orca-web（fpk + BFF） | 前端由 slicer-node 自己发 |
| 任务只在内存 | 落 `/data/jobs/<id>/job.json`，重启可恢复 |

## 9. 版本演进规则

- 层预览二进制布局变更必须同时升 `index.version`（并同步 `stride`），前端按版本分支，**不能只改语义**。
- `INDEX_VERSION`（模型索引缓存）变更要 +1，否则旧缓存会喂出错误轮廓。
- 新增字段只增不改；改语义就要升版本号。
