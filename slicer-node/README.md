# slicer-node

OrcaSlicer 的 HTTP 外壳。实际做的是**切片 + 摆盘 + 预览数据 + 配置管理 + 打印转发**，并顺带把前端页面（`web/` 工作台、`viewer/` 层预览）一起发出来 —— 所以它其实是一个单体服务。

**版本 v0.11.6**（`GET /health` 的 `version`）。功能清单见 [`../docs/feature-status.md`](../docs/feature-status.md)，版本时间线见 [`../docs/changelog.md`](../docs/changelog.md)。

## 为什么不是「AppImage 装到 NAS 上」

实测结论（见 `../docs/spike-results.md`）：官方 aarch64 包是 Ubuntu 24.04 构建，
需要 glibc ≥ 2.38；飞牛 fnOS 是 Debian 12（glibc 2.36）。**宿主机原生跑不通**，
只能容器化。所以这个目录的交付形态就是镜像。

## 快速开始

```sh
make appimage        # 下 OrcaSlicer 2.4.2 AppImage 到 vendor/（走 ghfast 加速）
make build           # 构建镜像（默认 BASE_IMAGE=ubuntu:24.04）
docker compose up -d # 起服务，映射 8020
make test            # 打 /health
sh tests/smoke.sh    # 端到端：提交 → 轮询 → 下载 gcode → 校验层数
```

无外网环境下构建：`make build BASE_IMAGE=ubuntu-base:24.04`，
前提是本地已有 `docker import` 进去的 ubuntu base（见 `../docs/spike-results.md` §0）。

> **注意**：本机 docker 拉不到 Docker Hub（`registry-1.docker.io` 超时），
> 当前镜像是用 `docker import` 出来的 `ubuntu-base:24.04` 打底的。
>
> 只改了 `app/` `viewer/` `web/` `profiles/` 时，不必重打全量镜像，可叠一层：
>
> ```sh
> chmod -R a+rX app viewer web profiles     # COPY 会保留源文件权限，700 会让 slicer 读不到
> docker build -f Dockerfile.delta --build-arg BASE=orca-slicer-node:<上一版> -t orca-slicer-node:<新版> .
> ```
>
> （`chmod` 不能放进镜像里做：本机存储驱动上会 `Operation not permitted`。）

## HTTP 接口

> **v0.8.1 起完整、准确的接口规范在 [`../docs/api-contract.md`](../docs/api-contract.md)**，
> 下面是速查表。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 版本、orca 版本、可用 profile、队列状态、schema/模型数 |
| GET | `/schema` | 774 个参数的元数据（类型/单位/单位换算/中英文说明） |
| GET | `/profiles` | 三个类别的 profile 名单 |
| GET/PUT/DELETE | `/profiles/{kind}/{name}` | 读/写/删单个 profile（用户层优先） |
| POST | `/profiles/{kind}/{name}/clone` | 另存为 |
| GET | `/profiles/{kind}/{name}/export` · POST `/profiles/{kind}/import` | 导出 / 导入 JSON |
| GET | `/profiles/{kind}/{name}/bed` | 从机器配置解析热床尺寸/形状/挤出机 |
| GET/POST | `/models` | 模型清单 / 上传 |
| DELETE | `/models/{name}` | 删模型 |
| GET | `/models/{name}/info` | 尺寸、面数、凸包；3MF 附子部件列表 |
| GET | `/models/{name}/mesh?part=` | 网格二进制（uint32 顶点数+面数 · float32×3 顶点 · uint32×3 面，小端；gzip） |
| POST | `/plate/arrange` | 多对象自动摆盘（返回每对象 x/y） |
| POST | `/plate/fit` | 求最大统一缩放让所有对象放下 |
| POST | `/plate/check` | 摆盘检查（越界/重叠/贴边） |
| POST | `/plate/slice` | JSON 提交整盘切片，202 + `job_id` |
| GET | `/jobs` | 任务列表 + 队列统计 |
| GET | `/jobs/{id}` | 状态 + 汇总（层数、耗时、字节数、错误） |
| GET | `/jobs/{id}/gcode` | 下载 gcode |
| GET | `/jobs/{id}/preview` | 层表 JSON（层高、段数、字节偏移、每层 bbox、类型桶 ranges、着色依据表） |
| GET | `/jobs/{id}/preview/{layer}` | 单层线段二进制，头里带 `X-Segments` / `X-Z` / `X-Pos-Bytes` |
| GET | `/jobs/{id}/preview/bin` | 全模型精简二进制（Range 请求 + gzip） |
| POST | `/jobs/{id}/preview` | 重建预览 |
| GET | `/jobs/{id}/log` | orca 自己的日志（排错用） |
| DELETE | `/jobs/{id}` | 删任务目录（跑着的不许删） |
| GET/PUT | `/printer` | Moonraker 配置（api_key 打码返回） |
| POST | `/printer/test` | 连通性测试 |
| POST | `/jobs/{id}/print` | 上传 gcode，`start=true` 才真的开打 |
| GET | `/viewer/?job=<id>` | 自带的 WebGL 预览页（也可独立打开） |
| GET | `/` | 工作台前端（五步流程，见 `web/`） |

`POST /plate/slice` 的字段：

```json
{
  "machine": "voron24-350",
  "process": "020-standard",
  "filaments": ["generic-pla"],
  "overrides": {"layer_height": 0.28, "sparse_infill_density": "40%"},
  "objects": [
    {"file": "benchy.stl", "x": 100, "y": 100, "z": 0,
     "rot": 0, "scale": 1.0, "extruder": 1,
     "m": [1,0,0, 0,1,0, 0,0,1]}
  ]
}
```

- `overrides` 的 key 用下划线（`layer_height`），节点把它**合并进 process JSON**，值全部转成字符串（Orca 只认字符串形态的标量）。**不要**拼成 `--layer-height=0.28` 这类 CLI 参数 —— CLI 会报 `Invalid option` 然后 exit 254，这正是 0.7.5 之前切片失败的根因。
- `objects[].m` 是 3×3 行主序旋转矩阵（摆盘「选择底面」产生的贴面矩阵）。`rot` 仍是绕 Z 角度，最终旋转 `R = rot_z(rot) @ m`，保证网格最小 z 落床面。缺省为单位阵。
- 多 `extruder` 时 `filaments` 按序号取。

响应里的 `summary` 直接来自 orca 的 `result.json` 加我们算的 `layers` / `gcode_bytes`：

```json
{"return_code":0,"error_string":"Success.","layer_height":0.2,
 "sliced_time":6,"triangle_count":120400,
 "gcode_bytes":15639318,"layers":500,"wall_seconds":7.59,"exit_code":0}
```

`POST /slice` 的字段：

```
file      模型文件（.stl/.3mf/.step/.stp/.obj，≤300MB）
machine   profiles/machine/ 下的名字，如 voron24-350
process   profiles/process/ 下的名字，如 020-standard
filament  profiles/filament/ 下的名字，如 generic-pla（可省）
overrides JSON 字符串，如 {"layer_height":"0.28","sparse_infill_density":"40%"}
```

`overrides` 的 key 用下划线（`layer_height`），同样合并进 JSON 并字符串化（不拼 CLI 参数）。
实测有效：`layer_height=0.28` 把 100 层变成 72 层。

响应里的 `summary` 直接来自 orca 的 `result.json` 加我们算的 `layers` / `gcode_bytes`：

```json
{"return_code":0,"error_string":"Success.","layer_height":0.2,
 "sliced_time":6,"triangle_count":120400,
 "gcode_bytes":15639318,"layers":500,"wall_seconds":7.59,"exit_code":0}
```

## 层预览（手机端渲染的原料）

切片成功后顺手把 gcode 拆成逐层线段，落在 `preview/` 下：

```
index.json     v6：层表 + 每类区间 + 每层长度/时间/耗材 + 总量 + area_step
                  + attrs[]：去重后的「工艺属性」组合表
segments.bin   每段 20 字节，**按段交错**（v6；v5 是平面分组，v4 是 18 字节）：
               偏移 0  Float32 × 4   x1,y1,x2,y2
               偏移 16 Uint8 × 1     type   类型码
               偏移 17 Uint8 × 1     area   挤出截面积 ÷ 0.002 mm²（量化）
               偏移 18 Uint16 × 1    attr   下标 → index.attrs[i]
```

层内顺序仍是**按类型桶排好**的（`index.layers[i].ranges[type] = [start, count]`），
所以过滤某几类只要 `seek` 到对应偏移，整层也能按 20 字节定长直接上传 GPU。
v6 之前是「16n 位置 + n 类型 + n 截面 + 2n 属性」四段分开存，客户端要先在 CPU 侧
重排才能画，首屏和大模型滚动时都在重复这件活。

`attrs[i]` 是这条走线切片那一刻的机器状态：

```
[extruder, speed_mm_s, fan_pct, nozzle_temp_c, accel_mm_s2, jerk_mm_s]
```

取值都来自 gcode 本身（工具头运动段落里 M104/M106/M204/M205/T 与 G1 F 的状态机，
换工具 / 换耗材时按当前 T 重新挂温度），**不是**按类型猜的常量。整模型去重后通常
只有几百种组合（本次 240 层 / 7 万段 → 295 种），所以每段只多 2 字节，
索引里多一张小表。`index.attr_avail` 标明这六项里哪些真有数据（比如单挤出机就没有 tool）。

`area` 是**真实挤出量**：解析时 `E增量 × 丝材截面积 ÷ 该段长度`。
前端再除以层高就是这一段的实际线宽 —— 外墙 0.35、稀疏填充 0.40、内部桥接
0.585 这些差异都是 gcode 里算出来的，不是按类型拍脑袋给的常数。
体积预览再乘 1.15 的搭接余量（gcode 的 E 常比标称线宽小 10%，1:1 画会漏缝）。

> 兼容：viewer 先看 `index.version`，再退一步用「这一层字节数 == n×20 / n×18 / n×16?」
> 判断每段有几个字段。v4（无 attr）和更早的任务照样能画，只是「挤出机/速度/风扇/
> 温度/加速度/抖动」这些依据会置灰、显示（无）。

### 配色与 OrcaSlicer 一致

类型码、颜色、中文名都对齐 OrcaSlicer（`libvgcode` 的
`DEFAULT_EXTRUSION_ROLES_COLORS`）：

| 码 | 类型 | 颜色 | 来源 |
| --- | --- | --- | --- |
| 0 | 外墙 outer_wall | `#FF7D38` | ExternalPerimeter |
| 1 | 内墙 inner_wall | `#FFE64D` | Perimeter |
| 2 | 稀疏填充 sparse_infill | `#B03029` | InternalInfill |
| 3 | 实心填充 solid_infill | `#9654CC` | SolidInfill |
| 4 | 支撑 support | `#00FF00` | SupportMaterial |
| 5 | 桥接 bridge | `#4D80BA` | BridgeInfill |
| 6 | 顶面 top_surface | `#F04040` | TopSolidInfill |
| 7 | 其他 other | `#E6B3B3` | None |

### 时间和耗材

优先吃 gcode 自带的统计（Orca 写在文件**尾部**，Prusa 在头部）：

```
; filament used [mm] = 28673.34
; filament used [g] = 85.52
; estimated printing time (normal mode) = 2h 36m 14s
```

拿不到就退化：耗材用 E 轴正增量累加，再不行按 `长度 × 0.084 mm²` 估；
时间按 60 mm/s 估。`totals.time_source` / `totals.filament_source` 会标明来源，
UI 可以说清楚「这是估的」。

每层的 `t` / `fil` 是按**该层挤出长度占比**摊出来的（Orca 的 M73 只在部分
flavor 里出现，摊比解析更稳）。实测 vase：28.67 m 丝、85.52 g、2h36m，
和 Orca 自己写的统计行完全一致。

### 三个设计选择

1. **坐标只存二维**，z 当每层的 uniform 传 —— 每段 16 字节，不是 20。
2. **层内按类型排序**（桶排序，类型码只有 0..7），索引里记
   `ranges: {"0":[0,186],"1":[186,373],...}`。于是「只看外墙」= 一段连续字节：
   前端零筛选，服务端只两次 seek。
3. **不做几何抽稀（RDP），改成抽层**。实测每层才 1–2k 段，
   真正的瓶颈是「一次看 500 层」的下载量，不是顶点数。
   索引给 `overview_stride`，超大模型（>150 万段）才用得上。

### 实测（vase：513,852 段 / 500 层）

| 场景 | 层数 | 下载 | 耗时 |
| --- | --- | --- | --- |
| 全模型（不抽层） | 500 | 8.33 MB | 0.61 s |
| 全模型 抽层 step=4 | 126 | 2.08 MB | 0.14 s |
| 抽层 + 只看墙(0,1) | 126 | 1.14 MB | 0.15 s |
| 抽层 + 只看外墙(0) | 126 | **0.38 MB** | 0.15 s |
| 当前层 + 下 24 层（拖滑块） | 25 | 0.28 MB | 0.03 s |

`index.json` 92 KB → gzip **20 KB**；二进制解析 2.3 s（单核，切片后一次性做）。

### 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/jobs/{id}/preview` | 层表；>1KB 时按 `Accept-Encoding` gzip |
| GET | `/jobs/{id}/preview/{layer}?types=0,1` | 单层二进制，可按类型过滤 |
| GET | `/jobs/{id}/preview/bin` | 整包二进制，**支持 Range** |
| POST | `/jobs/{id}/preview` | 手动（重）建预览 |
| GET | `/viewer/?job=<id>` | 自带 WebGL 预览页（见下） |

`preview_status` 四态：`off | pending | ready | failed`。
`status=done` 之后还有约 2 s 预览才 `ready`，客户端要等这个字段。

## 预览页（viewer/，仿 OrcaSlicer）

`http://<host>:8020/viewer/?job=<id>`，单文件、无构建、无第三方库。

- **两套渲染路径，按缩放自动切**：
  - **体积管**（近景，默认）：每段一条八棱柱，**截面压扁成「宽 = 挤出宽度、高 = 层高」的扁丝**
    （真实的挤出丝就是扁的：顶面在层顶、底面在层底），半径 = 挤出宽度 ÷ 2。
    法向朝外做 Lambert + 高光 + 朝下变暗，层底 22% 的范围再压暗一点当「层缝」——
    竖直外壁上所有层的侧法线完全一样，不画层缝的话一整面墙是一条纯色，
    层高 0.16 时看着就是糊成一片。段两端各沿轴多伸一个半径，拐角不露缝。
  - **屏幕线**（远景 / 段数过大）：实例化四边形按像素宽度画，中心亮两侧暗（假横截面），
    线宽不足 2 设备像素时管子的明暗会糊在亚像素里，此时切回屏幕线反而更干净。
  - 段数预算按**当前视图真正要画的段数**算（单层 = 当前层 + 下面两层，累积 = 到当前层）：
    细管 160 万段、球冠 20 万段。以前用的是整份文件的段数（45 万门槛），
    0.16 层高同尺寸模型的 50 万段任务会被直接判死、永远只能用线条。
  - 顶栏「体积」按钮可手动循环 `自动 → 强制体积 → 强制线条`。
- 每层按类型连续排列，一个连续区间一次 draw call（vase 每层 4 次）。
- **只提交要画的层**：`visibleByteRange()` 把每个实例区间裁到当前模式可见的层
  （单层 = 当前层 + 下面两层，累积 = 0..当前层，全部 = 整份），
  50 万段的任务在单层视图里只提交约 4300 个实例，不再是 50 万个全靠顶点着色器丢弃。
- **底板**：按模型包围盒生成网格 + 边框 + XY 轴，网格不写深度。
- **三种显示**：单层 / 累积（由下往上渐隐）/ 全部（不透明，看到的就是实体外壳，
  和 OrcaSlicer 的「全部层」一致）。
- **右侧层条**：柱状图高度 = 每层耗时，白色游标指示当前层，可直接拖。
- **图例**：Orca 配色 + 中文名 + 每类长度/耗材；点一行即时隐藏该类
  （数据已全量在本地，切换不发请求）。
- **着色依据**（顶栏下拉，9 种）：
  - **走线类型**：Orca 的 extrusion role 配色，可逐类开关。
  - **挤出机** `T0/T1`、**速度** `G1 F÷60`（指令上限）、**风扇** `M106 S÷255`、
    **喷嘴温度** `M104`、**加速度** `M204 S / SET_VELOCITY_LIMIT`、
    **抖动** `M205 J / SQUARE_CORNER_VELOCITY`：都按段取值。
  - **实际速度 / 实际加速度**：解析时按 Klipper lookahead 回放整条路径
    （`max_accel` + `square_corner_velocity`，前向/反向/再前向梯形规划），
    算出每段真会跑的速度与有效加速度 —— 转角掉速、长直段才冲到指令速度。
    这两列逐段不同、拼不出小表，所以走独立的 uint16 通道（v7 每段 24 B）。
  - 取值种类少（≤6，如温度、加速度、抖动）时走**离散色 + 图例行**；
    种类多（速度）时走**连续渐变 + 刻度条**；只有一种取值时整块用该颜色的纯色。
  - 图例底部一行小字写明该依据的数据来源，免得猜。
  - 老任务（没有属性表）时这些项自动置灰。
- **底栏**：层号 / Z / 本层耗时与耗材 / 总用时 / 总耗材。
- **交互**：单指旋转、双指缩放平移、滚轮缩放、双击复位；
  拖动大模型时自动降采样，松手恢复全质量。

自检（headless Chromium + SwiftShader，无需人眼）：

```sh
python3 tests/ui_check.py http://127.0.0.1:8020 <job-id>
```

会检查：无 JS 错误、底栏数据、图例行数、四种窗口尺寸下无溢出/重叠、
调色板像素占比（验证几何真的画出来了、类型开关真的生效）。

着色依据单独测（9 种逐个切，检查图例/色种/亮像素，置灰项跳过）：

```sh
python3 tests/ui_schemes.py http://127.0.0.1:8020 <job-id>
python3 tests/ui_backward.py http://127.0.0.1:8020 <老任务 preview 目录>
```

后者用 Playwright 请求拦截把磁盘上的旧任务假装成可访问任务，验证 v4 索引在新前端下
不报错、能画、属性项全部置灰。

## profile 约定

`profiles/{machine,process,filament}/*.json` 必须是**已展开、已清洗**的扁平 JSON：

- **不能有 `inherits`** —— CLI 不解析，会 `return -17`。用 `../tools/flatten_profiles.py` 展：
  ```sh
  python3 ../tools/flatten_profiles.py --profiles-dir <orca>/resources/profiles \
    --vendor Voron --name "Voron 2.4 350 0.4 nozzle" -o profiles/machine/voron24-350.json
  ```
- **不能有 `""` / `[]` / `[""]`** —— 会 `ConfigOptionVector::set_at(): Assigning from an empty vector` 直接 abort。
  节点读入时会再清一遍（`app/profiles.py:clean`），但源头干净更好。
- SimplyPrint 的 export-full（`{profile:{}, settings:{}}` 两层壳）会自动拆壳，可以直接放进来。
  但 **machine 的 export-full 不能直接放**：机器的设置藏在 `profile.variants[]` 里（每个喷嘴一份），
  套壳文件里没有顶层 `settings`，直接拷进来会被当成一堆无关字段。用工具拆：
  ```sh
  python3 tools/import_simplyprint.py "orcaslicer-machine-Voron 2.4 350-export-full-*.json" \
      --kind machine --nozzle 0.4 --name voron24-350-sp
  python3 tools/import_simplyprint.py "orcaslicer-filament-*.json" \
      --kind filament --name jayo-pla-sp
  ```
  两个坑（都踩过）：
  1. 容器里跑的是 uid 1000，`docker cp` 进去的文件继承宿主权限（600）→ `/profiles/...` 读不了，
     接口 500 `PermissionError`。要 `docker exec -u 0 chmod 644`。
  2. 别把 preset 的 `from` 改成 `"User"` —— CLI 会当用户 preset 做打印机兼容校验，
     打开 3mf 直接 `rc=-16 The selected printer is not compatible with the 3mf`。保留导出里的 `"system"`。

## 目录

```
app/
  config.py       环境变量 → Settings
  profiles.py     读/清洗/校验/规范化 profile（用户层 /data/profiles 优先）
  schema.py       + schema/params.json  774 个参数的元数据（类型/单位/中英说明）
  slicer.py       拼命令（--arrange 0 / --load-settings / --load-filaments）、跑子进程、解析 result.json
  jobs.py         队列 + N worker，任务落 /data/jobs/<id>/job.json（重启可恢复）
  mesh.py         STL/OBJ/3MF 解析、按 <item> 拆子部件、凸包轮廓、网格二进制流
  models.py       模型库（列表/上传/删除/索引缓存）
  plate.py        摆盘几何：足迹变换、shelf 自动摆放、拼 3MF（网格内联）、覆盖率检查、compute_fit
  gcode.py        gcode → 逐层线段二进制（格式 v6，20 B/段按段交错，含 attrs 去重表）
  printer.py      Moonraker 上传/启动/连通性
  main.py         FastAPI 路由 + 静态前端
  cli.py          不开 HTTP 的直接切片 / 单独跑预览解析
web/              工作台前端：index.html、app.js（流程）、plate.js（2D 遗留）、
                  plate3d.js（3D 摆盘）、form.js（参数表单）、style.css
viewer/           层预览渲染器（WebGL2，可独立打开 /viewer/?job=…）
profiles/         内置三类别配置
tools/            build_param_schema.py、import_simplyprint.py、_patch_mesh.py
tests/            smoke.sh、validate_preview.py、render_preview.py、ui_check.py、
                  ui_schemes.py、ui_backward.py、flow_test.py、multi_probe*.py、t3d_check.py
vendor/           OrcaSlicer.AppImage（不进 git）
data06/           当前容器的 /data（任务、gcode、预览产物、用户 profile）
```

## CLI 用法（容器内，排错很方便）

```sh
docker compose exec slicer-node python -m app.cli profiles
docker compose exec slicer-node python -m app.cli slice \
  -m /data/jobs/xxx/model.stl -M voron24-350 -P 020-standard -F generic-pla -o /tmp/o.gcode
```

## 已知限制

- 并发默认 1。单任务峰值内存约 1.5 GB（12 万面/500 层），7.7 GB 机器开 2 个还行，3 个就悬。
- 任务记录已落盘（`job.json`，重启恢复），但**产物不做清理**：`SLICER_JOB_TTL=0`
  + `/data/tmp` 的切片临时目录从不删，会缓慢长大。
- 只切 0 号盘（`--slice 0`），多盘工程取第一盘；摆盘由前端合成单盘 3MF 后提交。
- 3MF 里的网格必须**内联**进主 `3dmodel.model`：Orca 2.4.2 解析 `<component>` 时按 `objectid`
  在全局 id 空间查找、忽略 `p:path`，各文件的 `id="1"` 会互相覆盖（0.8.1 修的「两个一样的部件」）。
- 模型超床只警告不阻止（`/plate/check` 给 warning + 建议缩放比例）。
- 预览的「速度」是 gcode 指令速度；「实际速度 / 实际加速度」是 Klipper lookahead
  回放的近似（不含 input shaper、挤出机流量限制、丝料压力滞后）。
- 版本号目前写在 `main.py` 的 `APP_VERSION`，不会自动跟镜像 tag 同步。
