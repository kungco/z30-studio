# Z30 Studio 原生开发版

这是 Rust + Slint 迁移分支，不是功能已经迁移完成的正式版本。原 Python 2.0 保留在仓库根目录，仍是 RAW/算法对照；先读 PROJECT_PROGRESS.md 和 DEVELOPMENT_PLAN.md。

当前阶段可运行原生三栏窗口、后台查看带 ICC 或无 ICC 的 JPEG/PNG（含 RGB16 PNG）、NEF 文件引用、评分和本机草稿恢复。NEF 解码、完整色彩管理/调色、导出和 AI 尚未启用；ICC 通过 LittleCMS 转为浮点线性 sRGB；无声明有假设提示，损坏/通道不匹配、CMYK/YCCK、cICP/HDR 等尚不支持的输入明确拒绝。显示器 ICC 尚未适配。用户确认本机窗口可见且可操作；本次桌面自动截图/点击受工具激活错误影响，未记为自动 UI 验收通过。

阶段 2A 已加入独立 RAW 核心和开发探针：官方 LibRaw 0.22.2 → RGB16 → float32 线性 sRGB，公开 12/14 位样片全/半尺寸均与 Python oracle 逐像素一致。上段的“未启用”指用户界面；完整 ICC/调色/导出通过后才开放，不能以核心探针代替完整产品验收。

## 构建和运行

正常 Windows 开发环境使用 Rust 1.99.1 + Visual Studio C++ Build Tools：

```powershell
# 从仓库根目录；Cargo.lock 固定依赖
./tools/native_build.ps1 -Test
```

没有全局 Rust 时，可以指定已安装的独立工具链根目录（本机为 D:/codex/toolchains/z30-native）：

```powershell
./tools/native_build.ps1 -ToolchainRoot '你的独立工具链目录' -Test
```

显式真实 RAW 测试（不将照片或 DLL 提交到 Git）：设置 `Z30_LIBRAW_DLL` 为固定 0.22.2 DLL 的绝对路径，`Z30_RAW_12`、`Z30_RAW_14` 为两份公开样片路径，再执行构建脚本加 `-Test -RawProbe -RawFixtures`。未提供这些文件时常规测试明确显示 ignored；不能当成真实 RAW 已验证。对照命令为 `python tools/native_raw_benchmark.py --dll ... --raw12 ... --raw14 ...`，只在新的 .development 目录写探针数据。

头文件、上游许可证和下载/文件校验在 vendor/libraw/。官方 Windows DLL 需要 Microsoft VC++ 2015–2022 x64 运行库；当前探针在本机验证，未将该 DLL 放进基础查看器分发。最终动态库/对应源码/许可和运行库交付尚待完成，不宣称干净 Windows 已可运行 RAW。

本机验证的是 Rust GNU + 便携 w64devkit 2.10.1：Rust 自带 CRT/链接库与便携 dlltool/汇编器配合，构建脚本仅修改子进程环境，不改全局 PATH。Windows Actions 使用 MSVC；不能在 CI 完成前宣称两条编译链均已验证。

内部构建输出为 target/release/z30-native.exe；每次复制到一个新的 dist/stage-* 目录，提供用户所需的 **Z30Studio.exe**，不会覆盖正在运行的版本。基础软件不需要 Python、浏览器、WebView 或 HTTP 服务。Python 测试/比对脚本是开发工具，不随原生程序运行。

## 数据与限制

程序按路径只读引用原片，当前本机草稿位于 `%LOCALAPPDATA%/Z30StudioNative`。工作目录独占锁防止两个新版实例同时写入；保存前检查磁盘版本，冲突或坏 JSON 明确阻止覆盖。旧开发构建没有该锁，升级时先关闭旧构建，不要跨版本同时编辑同一草稿。

`Z30_NATIVE_DATA_DIR` 仅用于显式选择独立工作目录/测试；新原生草稿不是 Python .z30proj 的替代格式。高级项目、版本、后台批量、原生 RAW 和人物精修须逐项迁移，不静默抛弃旧参数。

`tools/smoke_native.py` 使用隔离中文目录、系统 PATH、进程标记测试启动/预览就绪、退出、重复实例拒绝和重启恢复。事件循环标记不是屏幕首帧指标；报告不能当成完整 RAW 显影性能或专业画质验收。

## 许可与大小

Slint 1.18.1 采用 Royalty-free Desktop License 2.0，并在顶层“关于”面板保留官方 AboutSlint 组件。原文在 licenses/；MinGW 静态运行库声明也保留。Rust 工具链、编译依赖与可选大模型不会作为基础软件运行依赖隐藏在外部。

目前 EXE 体积只是查看器阶段的数据，不能据此宣称完整产品已达到 50 MB 目标。完整功能/模型/GPU 组件、依赖和许可清单在后续阶段验收后统计。正式发布、main 合并都需要用户确认。

阶段 2B 已加入 float 九参数和带 sRGB ICC 的不覆盖 JPEG/PNG/TIFF16 核心；暖色边界数值跳变已修复，修正实现四组对照通过；旧输出差异另报，见 docs/reports/native-gamut-stability.md。界面保持关闭，不能作为已迁移完成版本交付。`-ExportProbe` 编译仅供开发验证的合成 TIFF 探针；不替代全尺寸 NEF 导出验证。

阶段 2C 已把 JPEG/PNG 浮点 ICC 输入接入查看器，52 个合成输入和全尺寸数值/内存验证见 docs/reports/native-stage2c.md；矩阵/TRC ICC 缓存通过质量/速度复测，通用复杂 LUT 仍可能较慢；见 docs/reports/native-icc-performance.md。`-RasterProbe` 只编译开发探针。LittleCMS 与 MozJPEG 静态链接；This software is based in part on the work of the Independent JPEG Group.
