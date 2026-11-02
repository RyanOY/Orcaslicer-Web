# 功能清单与验证记录（v0.11.6 / 2026-11-01）

这份文档回答两个问题：**现在实现了什么**、**凭什么说它是好的**。
每一项都尽量给出可复现的验证方式（job id、接口、实测数字），而不是只写「已完成」。

> 判据说明：切片结果是否「对」，一律用 gcode 里的 `EXCLUDE_OBJECT_DEFINE NAME=... CENTER=... POLYGON=...`
> 做交叉验证 —— 它是 OrcaSlicer 自己写出来的「这个对象到底占哪块地」，
> 比看渲染图可靠（本项目两次严重 bug 都是靠它抓出来的）。

---

## 1. 切片内核

| 项 | 实现 | 验证 |
| --- | --- | --- |
| 容器内 headless 切片 | `app/slicer.py` 调 `/opt/orca/bin/orca-slicer --slice 0 --outputdir` | 无 `DISPLAY` 也能跑；立方体 0.4 s |
| 关闭自动排版 | `--arrange 0`（0.8.1 新增） | 摆 (250,250) 的部件，gcode 多边形落在 x[177.5,322.5]，与请求一致 |
| 参数覆盖 | overrides 合并进 `process/machine/filament` JSON 后 `--load-settings` | `layer_height=0.28` → 100 层变 72 层 |
| 布尔覆盖 | 前端 `asBool()` 解析 `"0"/"1"`（`Boolean("0")` 恒真，0.8.2 修），后端 `_coerce` 统一成 `"0"/"1"` | `enable_support: true` → process.json `"1"`、gcode 99 段 `;TYPE:Support` |
| 覆盖项回显 | 切片页摘要列出改过的参数中文名（`支撑/开启支撑`） | 改 `support_*` 而 `enable_support` 关时红字告警 |
| 多耗材槽位 | `--load-filaments` 收分号分隔列表，顺序=槽位号 | 4 个料时 gcode 出现 `T0`/`T3` 换刀（job `cfceac80e156`） |
| 热床温度 / 板型 | `overrides.curr_bed_type` 写进 process JSON；摆盘页可选板型并按耗材配置算出温度 | `Cool Plate` → `M140 S48`（jayo-pla，job `308bc1bc6b79`）；`High Temp Plate` → `S55`（job `bc1c47ccc504`）；`Hot Plate`/`Smooth PEI Plate` 是非法值，会退回 35℃ |
| profile 类型规范化 | `profiles.clean()` + `slicer._flake()` | 温度写成 `[48]` 也不再报 `invalid json array` |
| 任务队列 | `JobManager`，`SLICER_MAX_CONCURRENCY=1`，超时 900 s | `/health` 的 `queue` 字段 |
| 重启恢复 | `jobs._restore()` 扫 `/data/jobs/*/job.json` | 容器重启后历史任务仍在列表里 |

**OrcaSlicer 2.4.2 的三个硬性事实**（踩过才知道，写进代码注释了）：
配置文件里**所有标量都是字符串**，数字/布尔会被静默忽略或直接拒载；
CLI 不接受任意 `--配置项=值`（报 Invalid option 后 exit 254）；
`--export-3mf` 会段错误。

## 2. 模型库

| 项 | 实现 | 验证 |
| --- | --- | --- |
| 列表/上传/删除 | `app/models.py`，`MAX_UPLOAD` 限制 | `/models` 返回 `models/dir/extra_dirs` |
| 3MF 多子对象 | `mesh._load_3mf()` 按 `<build><item>` 拆，保留各自变换 | Dyson 3MF → 2 个部件：205.5×210.8×60（3932 面）、145×145×32.7（2886 面） |
| 轮廓缩略图 | 服务端算了凸包给前端画剪影 | 模型卡片上的形状 |
| 网格二进制流 | `GET /models/{name}/mesh?part=<id>`，uint32 头 + float32 顶点 + uint32 面，gzip | 前端 3D 摆盘渲染与面拾取都用它 |
| 索引缓存 | `INDEX_VERSION=4`，版本变即失效 | 改动 `_load_3mf` 后必须 +1，否则旧缓存喂错轮廓 |

## 3. 摆盘（3D）

| 项 | 实现 | 验证 |
| --- | --- | --- |
| 三维渲染 | `web/plate3d.js`，WebGL2，正交/透视轨道相机，热床网格 + 模型网格 | 首屏热床与网格都出图（0.8.0 修过一次 buildBed 早于 GL 初始化的白屏） |
| 首帧鲁棒性 | 画布 0 尺寸重试、网格懒上传（`ensureUpload`）、上下文丢失/恢复重建、网格失败重试 | 0.8.3 修「第一次进摆盘不显示模型，来回切一下才出来」 |
| 排障接口 | `window.__plate.debug()` / `probe()`（重画一帧并统计非背景像素） | 摆盘空白时先看 `probe().lit` |
| 操控 | 单指拖对象、双指转视角/捏合缩放、点选高亮 | 浏览器实测 |
| 三轴旋转 | 工具栏 X/Y/Z 选轴 + `⟲15°`/`⟳15°`（Shift=90°）；「旋转」模式下 Z 按方位角拖、X/Y 上下拖；侧栏 `RX°/RY°/RZ°` 可直接填。角度是权威状态，朝向按 `Rz·Ry·Rx` 合成（`rot` + `m` 给后端） | `tests/rot3_math.mjs`、`tests/plate3d_smoke.mjs`；rx=45 姿态切片通过（见 changelog 0.11.4） |
| 缩放 | 缩放模式拖拽 1:1 跟手，30° 步进与三轴旋转共用同一套朝向存储 | 拖拽时 3D 与实际值同步 |
| 选底面贴床 | 点面 → 算该面法线到 -Z 的旋转矩阵 `m`，贯穿摆盘→切片 | 斜放的对象切片后底面确实贴床 |
| 自动摆盘 | `plate.arrange()` 货架式装箱，支持任意朝向足迹 | `POST /plate/arrange` |
| 一键缩放放下 | `plate.compute_fit()` 二分求统一缩放 | Dyson 双部件 350 床 → 96.7%，零重叠，切片 290 层通过（job `4f37720fbaf5`） |
| 撤销/重做 | 整盘快照栈，所有变更点 commit | 按钮 + Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y |
| 检查 | 越界 / 重叠 / 贴边出警告 | `POST /plate/check` |

**已知坑**：`arrange` 排序后必须按对象 uid 回写，不能按下标——否则多部件的位置会张冠李戴（0.8.0 修）。
把模型绕 X 转到 **88°~90°**（接近完全立起来）时，Orca 2.4.2 引擎会抛通用的
`found slicing or export error`，同一姿态用 Orca 自带 transform、甚至纯 STL 喂进去同样失败
（=引擎问题，不是摆盘/3MF 的错）；角度微调 1° 或打开支撑即可。前端已在该错误上附提示。
`fit` 对单件超过床的模型要单独判断缩放上限，否则会算出一个放不下的 factor。

## 4. 配置（机器/工艺/耗材）

| 项 | 实现 | 验证 |
| --- | --- | --- |
| 774 参数表单 | `app/schema/params.json` + `web/form.js`，按类型渲染 | `GET /schema` 的 `params` 数 |
| 常用/全部/搜索 | 前端两档 + 关键字过滤 | — |
| 三层存储 | 镜像内置 `/app/profiles` → 用户层 `/data/profiles`（优先） | `entries()` 标 `baked`/`user` |
| 克隆 / 删除 / 恢复内置 | `POST /profiles/{kind}/{name}/clone` 等 | 删除用户层文件即回落到内置 |
| 导入/导出 | 吃 SimplyPrint `export-full` 壳、Orca preset、扁平 JSON | 导入过 SimplyPrint 的机器与耗材配置 |
| 类型规范化 | `_orca_scalar()`：标量→字符串、数组元素→字符串、`None` 丢弃 | 保存后 `cool_plate_temp` 是 `["48"]` 而不是 `[48]` |
| 耗材收缩率 XY / Z | 耗材配置里独立「收缩率」分组，两个数字输入（50~150%，100=不补偿）；`plate.shrink_of()` 在切片前把系数乘到顶点上；切片页「参数覆盖」合并了耗材 schema，也能临时改 | XY 90% → 尺寸 ×1.1126；Z 90% → 高度 ×1.1120、层数 240→267（`tests/shrink_test.py`） |
| 收缩率算进摆盘 | `/plate/arrange`·`fit`·`check` 接受 `filaments[]`+`overrides{}`，按放大后尺寸排布/查越界/给缩放比例 | 90% 耗材让 330mm 宽的对象报「超出热床」；fit factor 1.000→0.938（`tests/shrink_bounds.py`） |

**踩过的坑**：带 `inherits` 的原始 profile CLI 不认（要先 `tools/flatten_profiles.py`）；
`""` / `[]` / `[""]` 会让 CLI 抛 `Assigning from an empty vector` 然后 abort；
编辑器保存数字数组会让 CLI 整份 preset 拒载。

## 5. 层预览（viewer）

| 项 | 实现 | 验证 |
| --- | --- | --- |
| gcode → 逐层二进制 | `app/gcode.py`，格式 v7：每段 24 B（按段交错） | 500 层 / 51 万段，解析 ~6 s（含运动学回放） |
| 分层索引 | `preview/index.json`：每层 z、段数、字节偏移、按类型区间 | `GET /jobs/{id}/preview` |
| 按需取层 | `GET /jobs/{id}/preview/{layer}?types=0,3` | 只拉外墙时全模型 8.33 MB → 0.38 MB |
| Range 流式 + gzip | `GET /jobs/{id}/preview/bin` | 大模型边下边画 |
| 走线类型着色 | 类型码与 Orca 的 ExtrusionRole 对齐，v5 把悬垂墙/底面/内部桥接/支撑界面/缝隙填充/裙边单列 | 图例可点隐藏 |
| 6 种附加着色 | 挤出机/速度/风扇/温度/加速度/抖动，去重成 attrs 表（每段 1 个 Uint16 下标） | 7 万段 → 295 种组合，切依据只重建颜色表（<1 ms） |
| 真实速度/加速度着色 | Klipper lookahead 回放（前向→反向→前向梯形规划 + `square_corner_velocity` 拐角限速）算出每段实际速度与有效加速度，uint16 落盘（v7 的 20/22 列） | 指令 120 mm/s 的外墙实际 40–120 起伏；相邻段速度跳变超出加速度能力的比例 0.00%；速度不超指令值 |
| 体积管 | 八棱柱，截面压扁成「宽=挤出线宽×1.15、高=层高」的扁丝（顶面在层顶、底面在层底），法线光照 + 层底压暗当层缝；端帽球冠、拐角圆弧；段数预算按「当前视图真正要画的层」算（细管 160 万段 / 球冠 20 万段） | 0.16 层高 50 万段的工程不再被迫退回纯线条 |
| 明暗三档 | 顶栏滑杆 平涂/标准/强明暗，`uSK`/`uAO`/`uSeam`/`uEdge` 四个 uniform 下发，选择存 `localStorage`；默认 线条 + 强明暗 | 俯视平均亮度 96.5 / 83.1 / 60.8 |
| 屏幕线模式 | 半宽 = 物理线宽 × pxPerMm（钳位放到 [0.45, 400] px），明暗按线宽淡入 | 25 mm 处实心区背景 0.0% |
| 拐点角点（0.10.4） | 每个拐点只算**一个**角点：`o_σ = σ·b·(hw·min(L,3)·k)`，A 的退出端点和 B 的进入端点读到同一组 float → 屏幕上同一个点，缝和压边从几何上消失。`k=1` 即满宽真斜接；`k<1` 只在碎段上把角点沿角平分线等比回缩（代价是一道窄颈，不会露缝）。截断三条约束：单头 `0.9·len` 两条 + 两头进退之和 `<0.98·len`（真实翻面条件），每次只收紧绝对值大的一头，三遍收敛。按坐标**同层内**配对 | 20.7 万段：接缝两侧角点不重合 740→**5**、重影 1.79%→**0.58%**、自交 9 段、86.7% 拐点满宽、91.9 万笔画像素 13 个针眼；25.9 万段工程 704→**0**、重影 2.29%→0.53%；v5 老格式（无面积数据）也走拐点，48.9 万个拐点 0 退化；建表 0.19–0.36 s（一次性，仅在放大到线宽 ≥1.8 px 时） |
| 折线渲染（0.11.0） | 预览按**折线**组装后建模，不再逐 G1 画四边形：同层同类型且首尾严格相接的段合成一条 Polyline，段内做「微小点合并 + 形状保真」化简（点距 < max(1.15×线宽, 1.6 px) 且弦偏差 ≤ 0.6 px 且转角 <25° 才合并），关节用一次算出的斜接角点（太尖则退化为斜切楔形补角）。边角不再被 `0.9·段长` 截断 | 34.6 万段的密集工程 L150：斜接被截（掐腰）31%→0；关节处两侧读到同一组 float，缺口/重影构造上为 0；线与线之间亚像素保底 1.25 px，缩小后不再断成虚线；整层仍一次 draw call |
| 折线回退（0.11.1） | 顶点超上限时不再「把空当成画过」：清空 `ribbonKey` + 记 `ribbonSkip`，下一帧走实例化；`ribbonN<=0` 一律回退。超量时先按 ≈6.4 顶点/段估量、提高合并力度 `coarse` 再建一次 | 25.9 万段累积连画 2 帧：836k→0 顶点 修成 868k/868k；50.3 万段累积/全部第 2 帧起空白 → 2.76M/3.02M 稳定 |
| 屏幕空间等宽折线（0.11.2） | 角点只存「法线角 + ±半线宽」，线宽由顶点着色器在**屏幕空间**展开：把中心点和中心点+一个半线宽都投影，取差值方向（丢掉长度）再乘回 `半线宽×uProj/w` 的像素数。每个顶点离中心线恒为半个线宽（像素），与视角/深度/朝向无关；拐点两侧仍是同一组数，接缝不缺口不重叠 | 25.9 万段单层、dist=120：平视 el=0.02 发丝线（≤2 px）1.4%→**0.4%**；平视+正对墙 az=4.82 6.3%→**1.5%**；12 个方位角走线像素 +1.0%；俯视线宽分布 p5/p50/p95 = 5/5/7 px 不变 |
| 折线归属规则 | `G0`、`G1` 无 E 或 ΔE=0 的空移、`E` 回抽（ΔE<0）、换层/换类型 = 路径分隔符；只有 ΔE>0 的点进入当前折线；与上一点重合（<1 µm）的点跳过但不打断折线 | `index.json` 新增 `path_count` / `retract_splits` / `dedupe_skipped`，每层 `paths`；25.9 万段工程：11581 条折线、2350 次回抽切分 |
| 播放/滑层 | 逐层推进，跳过空层 | — |

**一致性校验**：预览线段与 gcode 逐点对比偏差 0.00%（两套独立实现交叉验证，见 `docs/spike-results.md`）。

## 6. 打印

| 项 | 实现 | 验证 |
| --- | --- | --- |
| Moonraker 配置 | `GET/PUT /printer`，配置文件落 `/data/printer.json`，API Key 返回时打码 | — |
| 连通性测试 | `POST /printer/test` | 对 Voron 的 7125 实测通 |
| 上传 gcode | `POST /jobs/{id}/print` | 上传成功返回远端路径 |
| 上传并开始 | 同接口 `start=true` | 需要用户在 UI 上二次确认；同时记下 `pending` 供打印后对账 |
| 打印进度 | `GET /printer/live` 轮询（2 s），进度条 + 层数 + 已用耗材 + 倍率 | 待机态实测 `standby / 21.2℃ / 0%`；打印态靠 Moonraker 的 `virtual_sdcard.progress` |
| 温度控制 | `POST /printer/temps`（`M140`/`M104 T<i>`） | 实测 `{bed:0,extruders:[0]}` 返回 ok |
| 手动移动 | `POST /printer/jog`，X/Y/Z 用 `G91→G1→G90`，E 用 `M83`；单次 ≤100 mm | 未归零时 UI 会提示先 G28 |
| G-code 控制台 | `POST /printer/gcode`，前端保留输出、Ctrl+Enter 发送 | 实测 `M115` 返回 ok |
| 打印动作 | 暂停/继续/取消/归零/固件重启/急停，全部二次确认 | `/printer/print/*`、`emergency_stop`、`firmware_restart` |
| 打印历史 | `GET /printer/history` 裁剪 Moonraker `/server/history/list` | 实测 200 条历史（90 completed）可读 |

## 6.1 耗材 / 料盘

| 项 | 实现 | 验证 |
| --- | --- | --- |
| 料盘 CRUD | `app/spools.py` + `GET/POST/PUT/DELETE /spools`，落 `/data/spools.json`（原子写） | `tests/spool_test.py` 覆盖创建/互斥/扣减/坏文件恢复 |
| 槽位绑定 | 一槽一卷（`slot: 0..7`），换卷自动摘下原卷 | 单测 + UI 实测（装 T3 后旧卷 `slot=None`） |
| 绑定参与切片 | `syncFilamentsFromSpools()` 把该槽料盘绑定的耗材配置填进切片槽位（手选过的槽不覆盖）；MMU 机器按 ACE 槽数铺满槽位 | 浏览器实测：4 槽、T4=jayo-pla、热床板 Cool · 48℃ |
| MMU 实时状态 | `ace_instance_0.slots` + `ace.current_index` + 烘干剩余 | 实测 ACE：T3 有料、箱温 58℃、湿度 7%、烘干剩 10617 s |
| 用量统计 | `app/usage.py` 扫 gcode 净挤出，按 `T` 拆分 | 42 个历史任务对比 Orca 头信息，单色误差 <0.1%（`tests/spool_test.py`） |
| 打印记录 | `POST /spools/sync`：Moonraker 历史 → 文件名后缀接回 job → 比例分摊 → 扣减 | 实测：历史 `plate(1)-f5e9f1041caf.gcode` → job `f5e9f1041caf`，扣 T3 |
| 去重 | 按 Moonraker `job_id` 记在记录里，重复 sync 不重复扣 | 连调两次，第二次 `count=0` |
| 打印前料量校验 | `GET /jobs/{id}/usage` → 打印页显示「共 24.27 m · T1（没装料盘）」 | 实测切片后 164 层任务返回 24.267 m |
| 自动对账 | 打印状态从 printing/paused 变为结束态时前端自动调 sync | 逻辑在 `paintLive()` 的状态迁移判断里 |

**口径说明**：多色 gcode 里换料会有一大串回抽/挤料，Orca 的 `filament used [mm]`
按它自己的算法计入，和我们「净挤出」的绝对值能差 40%。所以记账一律
`真实总量（Moonraker）× 我们的 per-tool 比例`；比例本身在多色文件上也有 ±12% 的偏差
（因为 Orca 的 per-tool 里含该工具头的换料挤料），属于已知限制，写进单测断言。

## 7. 前端工作台

5 步流程（`web/index.html`）：**1 模型 → 2 摆盘 → 3 切片 → 4 预览 → 5 打印**，外加「耗材」页与「配置」页。

- 状态都在内存里，刷新会丢摆盘（未做本地持久化）——目前可以从模型页重新加回来。
- 预览用 iframe 嵌 `viewer/`，与工作台解耦，单独也能开。
- 手机优先：禁止双击缩放、`touch-action: none`、工具栏横滑。

## 8. 端到端回归样例（截至 0.8.1）

| job | 场景 | 结果 |
| --- | --- | --- |
| `cfceac80e156` | Dyson 双部件 + 4 耗材 + T1/T4 双色 | 285 层；两部件多边形 195.3×200.3 / 137.8×137.7；位置与摆盘一致；有 T0/T3 换刀 |
| `be5050072589`、`e746b4add249` | 用户真实操作（修复后） | 各 300 层；部件 205.5×210.8（30 点）/ 145×145（66 点），形状确实不同 |
| `4f37720fbaf5` | fit 缩放到 96.7% 后切片 | 290 层，零重叠 |
| `7ca4f9b691`（修复前） | 同一盘，修复前 | 两部件多边形都是 268.2×96.6 —— 这就是「两个一模一样的部件」 |

## 9. 部署形态（2026-10-30 收尾后）

| 项 | 状态 |
| --- | --- |
| 单容器部署（Web + API + 切片） | `orcaweb3d`，8039→8000，镜像 `orca-slicer-node:0.8.1` |
| compose 管理 | ✅ `restart: unless-stopped`，NAS 重启自动拉起 |
| 代码 = 镜像 | ✅ 用 `docker commit` 从运行中的容器固化，重建不回退 |
| 数据持久化 | `/data` → `slicer-node/data06`（42 个任务、gcode、用户 profile）；模型只读挂 `/models` |
| 旧版本清理 | ✅ 0.7.5 及更早的 26 个镜像标签 + 32 个历史容器已删；`orcaweb3d-pre081` 暂留作回滚 |
| 重启验证 | ✅ `docker restart` 后 health 正常、42 个历史任务仍在、UI/预览页 200 |
| 端到端回归 | ✅ 新容器切 Dyson 双部件：205.5×210.8 与 145×145 两个不同轮廓，位置与请求 1:1（115,115 / 270,250），300 层 rc=0（验证任务已删） |

## 10. 缺口（按优先级）

1. **摆盘状态不持久**：刷新页面丢摆放，也没法保存「一盘」以后再用。
2. **多耗材的可视化**：对象上不显示 T0/T1，只能看侧栏文字，容易用错料。
3. **擦嘴塔位置**：只能靠用户摆盘留出空间，或手动传 `wipe_tower_x/y`。
4. **打印进度**：没有订阅 Moonraker 的 websocket。
5. **`/data/tmp` 清理**：`SLICER_JOB_TTL=0`，临时目录只增不减。
6. **镜像未回灌**：0.8.1 是热补丁，容器重建会丢（源码有同步，重建镜像后可恢复）。
