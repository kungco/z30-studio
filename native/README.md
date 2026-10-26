# Z30 Studio 原生开发版

这是 Rust + Slint 迁移分支，不是功能已经迁移完成的正式版本。原 Python 2.0 保留在仓库根目录，仍是 RAW/算法对照；先读 PROJECT_PROGRESS.md 和 DEVELOPMENT_PLAN.md。

当前开发版可在原生窗口中调节九个全局参数、逐张保存配方、撤销/重做、重置、查看调整前后，以及后台导出全尺寸 JPEG（质量 95）、PNG8、真正的 TIFF16。导出均带 sRGB ICC，并拒绝覆盖任何已有文件。连续拖动约 450 ms 停顿后合并为一步撤销；历史最多 50 步且仅保留在当前进程，最终参数会自动保存并在重启后恢复。

JPEG/PNG 输入支持已有 ICC 策略及 RGB16 PNG。NEF 使用已验证的 LibRaw 0.22.2，**当前仍需显式设置 Z30_LIBRAW_DLL 为 DLL 绝对路径**；它尚未随 ZIP 分发，缺失时显示错误。公开 Z30 12/14 位实拍样片的界面显影和全尺寸输出已验证，用户自己的样片、干净 Windows、显示器 ICC/校准肤色/打印尚未验证。损坏 ICC、CMYK/YCCK、cICP/HDR 等不支持的输入仍明确拒绝。

导出捕获点击时的照片和配方，之后切图或编辑不会改写正在导出的任务。关闭窗口会先保存参数，再请求取消导出并清理临时文件；取消按处理阶段和写入边界生效，不会强杀线程。极端解码器/文件系统阻塞尚无独立进程硬超时保证。崩溃恢复导出队列和批量将在后续阶段迁移。

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

`Z30_NATIVE_DATA_DIR` 仅用于显式选择独立工作目录/测试；新原生草稿不是 Python .z30proj 的替代格式。高级项目、版本、后台批量、RAW 独立分发和人物精修须逐项迁移，不静默抛弃旧参数。

`tools/smoke_native.py` 使用隔离中文目录、系统 PATH、进程标记测试启动/预览就绪、退出、重复实例拒绝和重启恢复。事件循环标记不是屏幕首帧指标；报告不能当成完整 RAW 显影性能或专业画质验收。

## 许可与大小

Slint 1.18.1 采用 Royalty-free Desktop License 2.0，并在顶层“关于”面板保留官方 AboutSlint 组件。原文在 licenses/；MinGW 静态运行库声明也保留。Rust 工具链、编译依赖与可选大模型不会作为基础软件运行依赖隐藏在外部。

目前 EXE 体积只是基础编辑阶段的数据，不能据此宣称完整产品已达到 50 MB 目标。完整功能/模型/GPU 组件、依赖和许可清单在后续阶段验收后统计。正式发布、main 合并都需要用户确认。

阶段 2B 已加入 float 九参数和带 sRGB ICC 的不覆盖 JPEG/PNG/TIFF16 核心；暖色边界数值跳变已修复，修正实现四组对照通过；旧输出差异另报，见 docs/reports/native-gamut-stability.md。2F 已接通基础编辑界面，但不是完整迁移版本。`-ExportProbe` 编译仅供开发验证的合成 TIFF 探针；不替代全尺寸 NEF 导出验证。

阶段 2C 已把 JPEG/PNG 浮点 ICC 输入接入查看器，52 个合成输入和全尺寸数值/内存验证见 docs/reports/native-stage2c.md；矩阵/TRC ICC 缓存通过质量/速度复测，通用复杂 LUT 仍可能较慢；见 docs/reports/native-icc-performance.md。`-RasterProbe` 只编译开发探针。LittleCMS 与 MozJPEG 静态链接；This software is based in part on the work of the Independent JPEG Group.

阶段 2D 的完整公开 NEF JPEG/PNG/TIFF16 文件已独立验证；JPEG 编码改用 MozJPEG，详见 docs/reports/native-stage2d-export.md。2F 已接入后台导出；`-PhotoExportProbe` 仍是独立开发验收工具。

原生草稿当前写 v2、读 v1/v2；第一次 v1→v2 保存前生成 session-v1-backup-*.json 原字节备份。此为原生草稿，仍不直接导入 Python .z30proj。2E 配方/50 步内存撤销/缓存接口已验证，现已接线到 2F 界面；见 docs/reports/native-stage2e-foundation.md 与 native-stage2f-ui.md。

`tools/smoke_native_editing.py --exe ... --out 新报告.json [--raw 样片.NEF --dll 固定库.dll]` 通过显式 QA 文件协议驱动同一组 Slint 回调，在隔离草稿和新文件名下验证调色/切图/导出/取消/退出/恢复，不启用 HTTP 服务。它不是鼠标或截图验收；2F 另用 Computer Use 实际检查过窗口、曝光/对比和文件保存对话框。
