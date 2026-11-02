# Spike 实测记录（Q1–Q4）

> 日期：2026-10-28
> 环境：fnOS / Linux 6.18.18-trim aarch64，8 核（4×A55@1.8G + 4×A76@2.4G），7.7 GB RAM
> OrcaSlicer：**v2.4.2**（`OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V2.4.2.AppImage`，135,469,576 B）

## 0. 环境搭法（重要，影响最终形态）

宿主机是 **Debian 12 / glibc 2.36**，而官方 aarch64 AppImage 是 **Ubuntu 24.04 构建，要求 glibc ≥ 2.38**
（`GLIBC_2.38 not found`、`GLIBCXX_3.4.32 not found`）。所以：

- **不能在 fnOS 宿主机上原生跑**，任何「直接装二进制到 NAS」的方案都不成立。
- 所有历史 aarch64 发布（v2.3.x 起）都是 Ubuntu2404 构建，没有更老的 AppImage 可用。
- 本次验证用 **user-namespace chroot** 复刻容器环境（无 docker 权限，无需 root）：

```sh
# 一次性准备
curl -O https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cdimage/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.5-base-arm64.tar.gz
mkdir rootfs && tar -xzf ubuntu-base-24.04.5-base-arm64.tar.gz -C rootfs

# 运行时（每次）
unshare -Urpf --mount-proc sh -c '
  mount --bind /proc rootfs/proc
  for d in null zero urandom random; do touch rootfs/dev/$d; mount --bind /dev/$d rootfs/dev/$d; done
  mkdir -p rootfs/dev/shm; mount --bind /dev/shm rootfs/dev/shm
  chroot rootfs /bin/sh /script.sh'
```

容器内需要补的运行时库（否则 `ldd` 报缺失）：
`libglu1-mesa libopengl0 libwebkit2gtk-4.1-0 libice6 libsm6`
（webkit2gtk-4.1 仅因链接存在，CLI 路径不初始化 WebView）

**结论：产品形态必须是容器（Docker/fpk 拉起），这也印证了「切片器与 UI 解耦、节点容器化」的决定。**

---

## Q1. 无 X 能否跑通？ → **能，且不需要 Xvfb**

`unset DISPLAY` 下直接：

```sh
cd /opt/orca
LC_ALL=C APPDIR=/opt/orca LD_LIBRARY_PATH=/opt/orca/lib/orca-runtime:/opt/orca/bin \
  ./bin/orca-slicer /test_cube.stl --slice 0 --use-relative-e-distances=0 --outputdir /tmp/out
```

`--help`、`--info`、`--slice 0 --outputdir` 全部正常，**没有起 X，也没有起 Xvfb**。
（曾单独装 Xvfb 做过对照，结果一致；容器里可以省掉 xvfb 与 mesa-dri。）

### 坑（都已踩过，写进契约）

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `Segmentation fault`（CLI::run 内 std::string 拷贝，0.5s 就崩） | 用了 `--export-3mf` / `--datadir` | **不要用 `--export-3mf`**，改用 `--slice 0 --outputdir DIR` |
| `run found error, return -17` | 输入是 `.drc`（Draco 便捷模型） | CLI 不支持 .drc，只收 STL/3MF/STEP |
| `return -51`：`Relative extruder addressing ... Add "G92 E0"` | 默认机型的 layer_gcode 校验 | 传 `--use-relative-e-distances=0`，或 profile 里带 `layer_gcode: "G92 E0"` |
| `run found error` 载入 `--load-settings` 内置 profile | 系统 profile 有 `inherits` 链（如 `fdm_klipper_common`），CLI 不解析 | 需要 **flatten 后的配置文件**，或带内嵌配置的 3MF（见下） |

### 产物（这才是节点该做的事）

```
OUT/plate_1.gcode   # 15.7 MB（花瓶 500 层）
OUT/result.json     # {"return_code":0, "sliced_time":5, "layer_height":0.2, "sliced_plates":[...]}
```

`result.json` 直接可用作任务状态回传，不用自己算。

---

## Q2. 切片耗时 → **亚秒~数秒级，<30s 档**

| 模型 | 三角面 | 层数 | 0.2mm gcode | wall |
| --- | --- | --- | --- | --- |
| 20mm 立方体 | 12 | 100 | 0.33 MB | **0.33 s** |
| 25 盒格栅（机械件代理） | 312 | 200 | 9.7 MB | **3.7 s** |
| 波浪花瓶 100mm 高 | 120,400 | 500 | 15.7 MB | **5.5 s**（冷/热一致） |

**多核确认**：一个 160k 三角的失败用例里 CPU 累计 260 s / wall 39 s ≈ **6.7 核并行**，
说明 OrcaSlicer 2.4 的 slicing 是多线程的，NAS 的 8 核吃得满。

**判定：< 30 s → 可以做「准交互式」**（改参数 → 几秒出结果），不必一上手就做成纯排队。
但**提醒**：以上模型都**没有开支撑**。树状支撑 + 高面数模型会明显拉长时间，属于后续要补测的项目。

---

## Q3. 产物体积

- 500 层 / 100 mm 花瓶 → **15.7 MB** gcode
- 200 层格栅 → 9.7 MB

< 50 MB 档 → 预览管线可以从简（按层切块 + 传输压缩），不需要一开始就上 LOD/窗口化。

---

## Q4. `--export-slicedata` → **不可用**

`--export-slicedata /tmp/sd --slice 0` 产出**空目录**（2.4.2 / aarch64 / CLI 路径）。
预览必须走 **gcode 解析**（G0/G1 + 挤出/材质标记），设计里「私有格式要自己写解析器」的悲观分支成立。

---

## Q5（追加）：用真机配置切一刀

输入：用户从 SimplyPrint 导出的 `<vendor>/orca/config/*.json`（`export-full` 格式）+ `tools/flatten_profiles.py` 展开的 Voron 2.4 350 机型。

### 5.1 SimplyPrint 导出格式与坑

结构是两层：

```json
{ "simplyprint_version": "1.0", "profile": {..元数据..}, "settings": {..真正的配置..} }
```

真正喂给 CLI 的是 **`settings`**（`profile` 只是外壳）。

**但这些 `settings` 直接喂会崩**：

```
terminate called after throwing an instance of 'Slicer::ConfigurationError'
  what(): ConfigOptionVector::set_at(): Assigning from an empty vector
```

原因是导出里带了一批空值：`compatible_printers: []`、`compatible_printers_condition: ""`、
`filament_settings_id: [""]`、`enable_pressure_advance: ""` 等。

**清洗规则（节点必须实现）**：丢掉值为 `""`、`[]`、`[""]` 的键，其余原样保留。
清洗后 116 → 107 键，立刻能切。

### 5.2 flatten 工具

`tools/flatten_profiles.py` 已写好并验证：读 vendor bundle（`resources/profiles/Voron.json` 的
`machine_list`/`process_list` 索引 + `sub_path`），递归按 `inherits` 合并（子覆盖父）。

```sh
python3 tools/flatten_profiles.py --profiles-dir <resources/profiles> \
  --vendor Voron --name "Voron 2.4 350 0.4 nozzle" -o machine.json   # 63 键
python3 tools/flatten_profiles.py --profiles-dir <resources/profiles> \
  --vendor Voron --name "0.20mm Standard @Voron"   -o process.json   # 115 键
```

展开出来的 `before_layer_change_gcode` 里自带 `G92 E0`，之前那个 `return -51` 也随之消失。

### 5.3 真机配置实测

```
orca-slicer model.stl --slice 0 \
  --load-settings "process.json;machine.json" \
  --load-filaments "Generic_PLA_atSystem_clean.json" \
  --outputdir OUT
```

| 模型 | 结果 |
| --- | --- |
| 格栅（200 层） | **4 s**，11 MB gcode |
| 花瓶（120,400 面 / 500 层） | **6 s**，15 MB gcode |

`result.json` 里还多了个 `sliced_time_with_cache: 2`（有内部缓存机制，第二次更快）。

两种耗材都跑通：`Generic PLA` → 220 °C / 热床 55；`jayo PLA` → 205 °C / 热床 48。

---

## 结论与对架构的影响

1. **形态**：节点必须是容器；宿主机原生跑被 glibc 版本判死刑。
2. **交互**：切片 3–10 s 量级 → UI 可以做成「点一下、等几秒」，进度条并非刚需但要有。
3. **接口**：节点对外只需 `POST /slice`（模型+扁平配置）→ 返回 `plate_1.gcode` + `result.json`；预览读 gcode。
4. **配置**：`flatten_profiles.py` 已解决内置 profile 的 `inherits` 问题；SimplyPrint 导出的配置需先做空值清洗。两者都是节点侧的必备步骤。
5. **待测**：开支撑的耗时、超大模型（>500k 面）、并发 2 个任务时的内存/CPU 抢占。
