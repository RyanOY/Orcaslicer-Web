# Spike 计划 —— 开工前的四个问题

> 目的：用 30 分钟回答「这个项目该不该做、做成什么形态」。
> 原则：**不写业务代码，只测能不能跑、跑多快。**

## 前置

- 在 NAS 上起一次性容器，全部产物落在容器内
- 结束执行 `docker rm -f <name>`，不碰宿主文件、不留 systemd、不装宿主机包
- **不影响正在进行的打印**（切片在 NAS，打印机在另一台机器，无耦合）

---

## Q1. 无 X 环境能否跑通？

OrcaSlicer 即便走 CLI 也仍链接 GTK/wxWidgets，纯 headless 大概率需要虚拟显示。

```bash
# 路径 A：先试裸跑
orca-slicer --help

# 路径 B：xvfb
xvfb-run -a orca-slicer --help

# 路径 C：AppImage 解包运行（FUSE 不可用时）
./OrcaSlicer_*.AppImage --appimage-extract
./squashfs-root/AppRun --help
```

**判定：**
- 三条路有一条能打出 usage → 通过
- 全挂 → 方案要改（换 PrusaSlicer CLI？换 Flatpak？）→ **项目暂停，重新评估**

## Q2. 切片耗时？（决定产品形态）

```bash
/usr/bin/time -v xvfb-run -a orca-slicer benchy.stl \
  --load-settings "voron_process.json;voron_machine.json" \
  --load-filaments pla.json \
  --slice 1 --export-3mf /tmp/out.3mf
```

同时开 `top -H` 看 CPU 分布，确认是单核还是多核。

**判定（benchy，0.2mm）：**

| 实测 | 结论 |
| --- | --- |
| < 30 s | 可以做「准交互式」：改参数 → 等一会儿 → 看结果 |
| 30–90 s | 异步队列模型，UI 必须做得能等（进度、可离开页面、完成通知） |
| > 90 s | **产品形态必须改成「提交-排队-回来取」**，并优先考虑把节点搬到 PC |

**这一项是项目成败的分水岭。** 如果 NAS 上超过 90 秒，而用户又不接受等待，正确做法不是优化前端，而是把节点放到 PC 上——好在架构已经解耦，这不是返工。

## Q3. 真实模型的产物体积？

拿一个用户实际会打的模型（不是 benchy），记录：

```bash
ls -lh /tmp/out.3mf
xz -9 out.gcode   # 压缩比参考
```

**判定：**
- gcode < 50 MB → 预览压缩方案可以从简
- 50–150 MB → 按层分块 + LOD 必需
- \> 150 MB → 还要考虑「只渲染当前层附近的窗口」而不是全模

## Q4. `--export-slicedata` 输出什么？

```bash
orca-slicer model.3mf --load-settings "..." --export-slicedata /tmp/sd --slice 1
find /tmp/sd -type f | head -20
```

想知道：是每层多边形（可直接做层预览）、还是二进制内部格式（要自己写解析）。

**判定：**
- 可直接读 → 预览管线省掉 gcode 解析器，工期 -30%
- 私有格式 → 老老实实解析 gcode（G0/G1 提取 + 材质类型映射）

---

## 交付物

一份实测记录追加到 `docs/design-notes.md` 的 §1.4，包含：

1. Q1 走通了哪条路（命令原样保留）
2. Q2 的秒数、CPU 核数分布、A76/A55 归属
3. Q3 的体积数
4. Q4 的文件格式判断

## 决策树

```
Q1 挂  → 停，重新选切片后端
Q1 通  → Q2 定形态
          ├─ <30s  → 交互式 UI（可以野心大一点）
          ├─ 30-90s → 异步队列 UI（推荐起点）
          └─ >90s  → 排队式 + 建议搬 PC；UI 先行，节点后置
        → Q3/Q4 定预览管线复杂度
```
