# OrcaWeb — 手机端的 3D 打印切片工作流

> 在飞牛 NAS 上跑 OrcaSlicer CLI 做切片，整个 Cura/Orca 式的「选模型 → 摆盘 → 切片 → 层预览 → 发打印」
> 工作流放在手机浏览器里。

**当前版本：`slicer-node` v0.11.6**（`GET /health` 的 `version` 字段） · 最后更新 2026-11-01

---

## 0. 仓库里有什么

```
orca-web-slicer/
├── README.md                  ← 你正在看的这份（总览）
├── docs/
│   ├── api-contract.md        HTTP 接口规范（as-built，逐条对照实现写）
│   ├── feature-status.md      功能清单 + 每项怎么验证的
│   ├── changelog.md           版本时间线与每一版踩的坑
│   ├── design-notes.md        设计决策记录（含否决的方案）
│   ├── spike-plan.md          动工前要测的 4 个未知数
│   └── spike-results.md       实测结论（headless 可跑、切片耗时、格式）
├── slicer-node/               ← 全部运行代码（FastAPI 后端 + Web 前端 + 层预览渲染）
│   ├── app/                   FastAPI 路由与切片/摆盘/预览/profile/打印/料盘模块
│   ├── web/                   工作台前端（选模型→摆盘→切片→预览→打印）
│   ├── viewer/                层预览渲染器（WebGL2，独立页面）
│   ├── profiles/              内置机器/工艺/耗材配置
│   ├── tests/                 端到端与单测（fixtures 只放小模型）
│   ├── Dockerfile             arm64/aarch64 构建（ubuntu 24.04 + OrcaSlicer CLI）
│   ├── Dockerfile.delta       只改代码时叠一层的小镜像
│   ├── docker-compose.yml     8039:8000，数据挂 ./data06 → /data
│   └── Makefile               appimage / build / up / test
└── tools/                     一次性维护脚本（profile 展平、版本号 bump 等）
```

**不入库的东西**（`.gitignore` 已排除，需要时自己生成）：

| 路径 | 为什么不入库 | 怎么来 |
| --- | --- | --- |
| `slicer-node/vendor/OrcaSlicer.AppImage` | 130 MB 上游二进制 | `cd slicer-node && make appimage`（自动下载 2.4.2 aarch64） |
| `slicer-node/data06/` | 运行时数据（任务/gcode/模型/profile，2 GB 级） | 首次启动自动创建 |
| `slicer-node/tmp/` | 调试产物 | — |
| `tests/fixtures/vase-120k.stl` | 5.8 MB 大模型，只给性能测试用 | 用任意 10 万面级 STL 替代即可 |

代码默认的 Moonraker 地址是示例值（`192.168.71.33:7125`），换机器在页面「打印机」里改一次即可，
配置落在 `/data/printers.json`。

---

## 1. 这个项目要解决什么

用户没有电脑，只有 Android 手机；打印机是 Voron 2.4（Klipper + Moonraker）；NAS 是飞牛 fnOS
（`FN EVO4-R`，ARM64 A55×8，7.7 GB）。

缺的那一环是**切片**：OrcaSlicer 没有安卓版，飞牛上也装不了图形版。于是模型能下载、能打印，
但没法切片。OrcaWeb 补的就是这一环，并且把 Orca 里最常用的那几个操作（摆盘、参数覆盖、层预览）
做到手机上能用。

## 2. 现在能做什么（一句话）

手机浏览器打开 `http://<NAS>:8039/` → 选模型（STL/3MF/OBJ，3MF 按子部件拆开）→ 3D 摆盘
（拖动/旋转/缩放/选底面贴床/自动摆盘/一键缩放放下/撤销重做）→ 选机器+工艺+多耗材并给每个对象指定
挤出机 → 切片（可临时覆盖 774 个参数里的任意一个）→ 逐层预览（9 种着色依据、体积管、播放）
→ 上传到 Moonraker 并启动打印 → 在同一个页面看温度/进度、点动、发 gcode；打印完料盘自动扣重。

## 3. 架构（和最初设想的差别）

最初设想是「fpk 纯前端 + 独立切片节点」。实际落地时前端和节点合并成了一个容器：
**slicer-node 自己就把 Web UI 和 HTTP API 一起发出来**，少一跳、少一处配置。

```
   手机浏览器
       │ HTTP
       ▼
┌──────────────────────────────┐
│ 容器 orcaweb3d  :8039→8000   │
│  FastAPI (app/main.py)       │
│   ├ /web  + /viewer  ← 前端  │
│   ├ /api 全部路由            │
│   └ 起子进程跑 OrcaSlicer CLI │
│      (headless，无需 X)       │
└───────┬──────────────┬───────┘
        │              │
   /models         /data (data06)
   模型库          任务/gcode/预览产物
                     │
                     ▼
              OrcaSlicer 2.4.2 (aarch64 AppImage, 容器内)
```

- **切片服务无状态**：字节进（3MF + profile JSON），产物出（gcode + 预览二进制）。任务记录落 `/data/jobs/<id>/`。
- **不需要 X / Xvfb**：Orca AppImage 的 CLI 在 headless 下原生可跑（实测，见 `docs/spike-results.md`）。
- 以后想把切片挪到一台 x86 PC，只需把镜像按 amd64 重建，前端配置里换个地址。

## 4. 功能清单（截至 0.9.0）

### 4.1 切片内核
| 能力 | 状态 | 说明 |
| --- | --- | --- |
| OrcaSlicer 2.4.2 CLI，aarch64，headless | ✅ | 容器化（飞牛 glibc 2.36 < 官方要求 2.38） |
| 单文件切片 `/slice` | ✅ | STL/OBJ/3MF，兼容最早接口 |
| 摆盘切片 `/plate/slice` | ✅ | 多对象 + 每对象独立挤出机 + 多耗材 |
| 参数覆盖（任意 774 项） | ✅ | 合并进 `--load-settings` 的 JSON（不能拼 CLI 参数） |
| 任务队列 / 超时 / 重启恢复 | ✅ | `SLICER_MAX_CONCURRENCY=1`，单任务峰值 ~1.5 G |
| 日志与错误回传 | ✅ | `/jobs/{id}/log`，失败带 `error_string` |

### 4.2 模型库
| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 列表 / 上传 / 删除 | ✅ | 上传落 `/data/models`；NAS 目录 `/vol1/1000/orca/model` 只读挂到 `/models` 当额外来源 |
| 3MF 多子对象拆分 | ✅ | 每个 `<item>` 一个可独立摆放的部件（带各自变换） |
| 缩略轮廓 + 尺寸/面数 | ✅ | 服务端算凸包，`INDEX_VERSION=4` 缓存失效 |
| 二进制网格流 `/models/{n}/mesh` | ✅ | gzip，给 3D 摆盘渲染 + 面拾取 |

### 4.3 摆盘（3D）
| 能力 | 状态 | 说明 |
| --- | --- | --- |
| WebGL2 真三维渲染热床 + 模型 | ✅ | 双指转视角、捏合缩放、点选对象 |
| 移动 / 旋转（三轴 + 15° 步进）/ 缩放 | ✅ | 拖拽跟手，1:1 比例 |
| 选择底面贴床 | ✅ | 点一个面 → 该面转到朝下，生成 `m` 矩阵 |
| 自动摆盘 `/plate/arrange` | ✅ | 货架式（shelf packing），带间隙 |
| 一键缩放到全部放下 `/plate/fit` | ✅ | 二分求最大统一缩放（Dyson 双部件 → 96.7%） |
| 撤销 / 重做 | ✅ | 整盘快照栈，按钮 + Ctrl+Z / Ctrl+Shift+Z |
| 检查（越界/重叠/贴边） | ✅ | `/plate/check` 出警告，放不下时给「缩放到 N%」出口 |

### 4.4 配置（机器 / 工艺 / 耗材）
| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 774 个参数的逐项表单 | ✅ | schema 驱动，常用/全部两档，支持搜索 |
| 新建 / 克隆 / 删除 / 恢复内置 | ✅ | 用户层写 `/data/profiles/`，优先于镜像内置 |
| 导入 / 导出 JSON | ✅ | 吃 SimplyPrint 的 `export-full` 两层壳，自动展平 |
| 类型规范化 | ✅ | 0.8.1 起保存即转成 Orca 认的字符串形态 |

### 4.5 层预览（viewer）
| 能力 | 状态 | 说明 |
| --- | --- | --- |
| gcode → 逐层线段二进制 | ✅ | 格式 v5，每段 20 B（位置16 + 类型1 + 截面1 + 属性下标2） |
| 逐层按需加载 / Range / gzip / 抽层概览 | ✅ | 全模型概览 8.33 MB → 0.38 MB |
| 走线类型着色 + 图例过滤 | ✅ | 与 Orca 的 ExtrusionRole 对齐，含悬垂墙/底面/内部桥接/支撑界面/缝隙填充等 |
| 6 种附加着色依据 | ✅ | 挤出机 / 速度 / 风扇 / 喷嘴温度 / 加速度 / 抖动（属性去重表，切依据 <1 ms） |
| 真实速度/加速度 | ✅ | Klipper lookahead 回放（拐角掉速、长直段冲满），逐段 uint16（v7） |
| 体积管渲染 | ✅ | 八棱柱压扁成「宽=挤出线宽、高=层高」的扁丝 + 法线光照 + 层缝压暗，真实挤出宽度（墙体搭接） |
| 层滑动、播放、跳层缓冲 | ✅ | 手机端 500 层秒开 |

### 4.6 打印
| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Moonraker 配置 + 连通性测试 | ✅ | `/printer`、`/printer/test` |
| 上传 gcode / 上传并开始 | ✅ | `/jobs/{id}/print`，`start=true` 需二次确认 |
| 实时状态 | ✅ | 2 s 轮询 `/printer/live`：进度条、层数、已用耗材、温度、位置、倍率、ACE 槽位/烘干 |
| 温度控制 | ✅ | 喷嘴（多挤出机）/热床/腔体，设定或关闭 |
| 手动移动 | ✅ | X/Y/Z 点动（1/10/50 mm）+ 挤出/回抽 5 mm，E 走相对模式 |
| 打印动作 | ✅ | 暂停/继续/取消/归零/固件重启/急停（都二次确认） |
| G-code 控制台 | ✅ | 发任意脚本，输出留在页面上 |
| 打印前料量校验 | ✅ | 按工具头估算用量，并标出对应料盘「⚠不够」/「没装料盘」 |

### 4.7 耗材 / 料盘（0.9.0）
| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 料盘账本 | ✅ | 总量/剩余/颜色/材料/绑定切片耗材；`/data/spools.json`，原子写 |
| MMU / 槽位管理 | ✅ | T0..T7 一槽一卷；槽位状态直接从 Moonraker 读 ACE 实时数据 |
| 打印记录关联 | ✅ | `POST /spools/sync`：Moonraker 历史 → 文件名后缀接回任务 → 按工具头比例扣减 |
| 用量扫描 | ✅ | 自己扫 gcode（净挤出、按 T 拆分），单色与 Orca 误差 <0.1% |
| 自动对账 | ✅ | 打印结束（printing/paused → 结束态）时前端自动同步一次 |
| 手工校正 | ✅ | 给「剩余/已用/增减」三种口径 |

## 5. 部署与运维

```bash
# 生产实例
容器   orcaweb3d    镜像 orca-slicer-node:0.11.6  端口 8039→8000
       restart: unless-stopped，NAS 重启会自动拉起
数据   ./slicer-node/data06 → /data（任务/gcode/用户 profile）
模型   /models 可选：把本机模型库只读挂进来（compose 里已写死一个示例路径，按需改）
管理   cd slicer-node && docker compose ps | logs -f | restart
```

从零起一套（arm64 / x86 同一套 Dockerfile）：

```bash
cd slicer-node
make appimage            # 下 OrcaSlicer 2.4.2 AppImage 到 vendor/
make build               # docker build → orca-slicer-node:0.11.6
docker compose up -d     # http://<host>:8039/
```

镜像与代码同源：`docker commit` 出来的快照就是最终交付物，重建容器不会回退代码。

热补丁流程（这台机器没外网，重建镜像要 vendor AppImage + apt，所以改代码走这条）：

```bash
chmod 644 app/xxx.py                     # 必须！docker cp 会继承宿主机权限
docker cp app/xxx.py orcaweb3d:/app/app/xxx.py
docker restart orcaweb3d
curl -s localhost:8039/health | head -c 120   # 看 version 是否变了
```

**坑**：`edit_file` 之类工具写出来的文件是 `700`，cp 进容器后 uid 984 的进程读不了，
容器会以 `PermissionError: /app/app/main.py` 直接退出。改完一定先 `chmod 644`。

**改完记得固化**：`docker cp` 只改容器，容器一重建就全丢。验证没问题后执行
`docker commit orcaweb3d orca-slicer-node:<新版本号>`，再把 compose 里的 `image:` 改过去。

## 6. 快速开始（本机调试）

```bash
cd slicer-node
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8020   # 需要容器里的 Orca 环境
curl -s localhost:8020/health | python3 -m json.tool
```

完整构建见 `slicer-node/README.md`（`make appimage` → `make build`）。

## 7. 目录

| 路径 | 内容 |
| --- | --- |
| `docs/feature-status.md` | **功能清单 + 每项怎么验证的**（本文档的细版） |
| `docs/changelog.md` | 版本时间线与每一版踩的坑 |
| `docs/api-contract.md` | HTTP 接口规范（as-built） |
| `docs/design-notes.md` | 设计决策记录（为什么这么做、否决了什么） |
| `docs/spike-plan.md` / `docs/spike-results.md` | 动工前的 4 个实测问题与结论 |
| `slicer-node/app/` | FastAPI + 切片/摆盘/预览/profile 各模块（`moonraker.py` 打印控制、`spools.py` 料盘账本、`usage.py` gcode 用量） |
| `slicer-node/web/` | 工作台前端（5 步流程 + 耗材页 + 3D 摆盘 `plate3d.js`） |
| `slicer-node/tests/spool_test.py` | 用量扫描 + 料盘账本单测（`python3 tests/spool_test.py`） |
| `slicer-node/viewer/` | 层预览渲染器（独立页面，iframe 嵌入） |
| `slicer-node/profiles/` | 内置机器/工艺/耗材配置 |
| `tools/flatten_profiles.py` | 把带 `inherits` 的 Orca profile 展平 |

## 8. 已知限制

- **一次只能切一个**（`SLICER_MAX_CONCURRENCY=1`），单任务峰值内存约 1.5 G，NAS 只有 7.7 G。
- `/data/tmp` 的切片临时目录从不清理（`SLICER_JOB_TTL=0`），会缓慢长大（当前 200 MB 级）。
- **多耗材 + 摆满整床**时 Orca 的擦嘴塔可能放不下而报 `gcode path conflicts ... WipeTower`；
  0.8.1 关掉自动排版后盘子按用户摆的走，一般不再触发；真遇到可以用 `wipe_tower_x/y` 手动指定塔位。
- 摆盘没有碰撞检测后的自动避让（只有检查与提示），也没有「按床面贴齐底部」之外的自动朝向。
- **多色用量口径**：gcode 里的换料回抽/挤料，Orca 的 `filament used [mm]` 和我们的
  「净挤出」绝对值能差 40%（比例基本一致）。所以料盘记账用的是
  `Moonraker 实际总用量 × 我们算的工具头比例`，不要拿 `/jobs/{id}/usage` 的米数当唯一真相。
- 料盘槽位是「一卷一槽」，没有做「同一卷被两个工具头共用」的自动拆分（物理上也不会这么干）。
- 打印控制是 **2 秒轮询**，不是 websocket；急停/取消这类动作本身仍然是 HTTP 直接下发的。
- 预览的「速度」是 gcode 指令速度（`G1 F ÷ 60`）；`实际速度`/`实际加速度` 是按 Klipper
  lookahead 回放算的近似值（不含输入整形、丝料压力滞后、挤出机流量限制）。
- 没有用户体系：内网直接用，不要暴露到公网。
- 以后改了代码：`docker cp` + `restart` 立刻生效，但那只是容器内的改动，要保住就得再 `docker commit`（或重建镜像，需 vendor AppImage + 外网 apt）。

## 9. 下一步

1. 用量统计：每层耗时/耗材按对象拆分（现在只有整盘汇总，按工具头的已经有了）。
2. 打印订阅：把 2 s 轮询换成 Moonraker websocket，顺带做打印完成的推送通知。
3. 多耗材摆盘的耗材可视化：按 T0/T1 给对象上色，减少「切出来才发现用错料」。
4. 料盘账本的延伸：RFID/重量传感器自动校准剩余、低料预警、多卷续接。
5. 把热补丁回灌进镜像（需要先解决外网/apt 源），并给 `/data/tmp` 加定期清理。
6. 打包成 fpk，做成随 NAS 自启的服务。

## 不以目标

- ❌ 不做 OrcaSlicer 完整 GUI 的 Web 复刻（支撑绘制/接缝/颜色刷子、可变层高、布尔运算）——那是 3–6 个月的活，且永远落后上游。
- ❌ 不上架飞牛官方应用中心（需要开发者资质与审核），自用/侧载。
