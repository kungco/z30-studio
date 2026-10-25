# Z30 Studio 原生开发版

这是 Rust + Slint 迁移分支，不是功能已经迁移完成的正式版本。原 Python 2.0 保留在仓库根目录，仍是 RAW/算法对照；先读 PROJECT_PROGRESS.md 和 DEVELOPMENT_PLAN.md。

当前阶段可运行原生三栏窗口、后台查看未配置 ICC 的 JPEG/PNG、NEF 文件引用、评分和本机草稿恢复。NEF 解码、完整色彩管理/调色、导出和 AI 尚未启用；不支持的 ICC 输入明确拒绝，不以错误颜色冒充已显影。用户确认本机窗口可见且可操作；本次桌面自动截图/点击受工具激活错误影响，未记为自动 UI 验收通过。

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

本机验证的是 Rust GNU + 便携 w64devkit 2.10.1：Rust 自带 CRT/链接库与便携 dlltool/汇编器配合，构建脚本仅修改子进程环境，不改全局 PATH。Windows Actions 使用 MSVC；不能在 CI 完成前宣称两条编译链均已验证。

内部构建输出为 target/release/z30-native.exe；每次复制到一个新的 dist/stage-* 目录，提供用户所需的 **Z30Studio.exe**，不会覆盖正在运行的版本。基础软件不需要 Python、浏览器、WebView 或 HTTP 服务。Python 测试/比对脚本是开发工具，不随原生程序运行。

## 数据与限制

程序按路径只读引用原片，当前本机草稿位于 `%LOCALAPPDATA%/Z30StudioNative`。工作目录独占锁防止两个新版实例同时写入；保存前检查磁盘版本，冲突或坏 JSON 明确阻止覆盖。旧开发构建没有该锁，升级时先关闭旧构建，不要跨版本同时编辑同一草稿。

`Z30_NATIVE_DATA_DIR` 仅用于显式选择独立工作目录/测试；新原生草稿不是 Python .z30proj 的替代格式。高级项目、版本、后台批量、原生 RAW 和人物精修须逐项迁移，不静默抛弃旧参数。

`tools/smoke_native.py` 使用隔离中文目录、系统 PATH、进程标记测试启动/预览就绪、退出、重复实例拒绝和重启恢复。事件循环标记不是屏幕首帧指标；报告不能当成完整 RAW 显影性能或专业画质验收。

## 许可与大小

Slint 1.18.1 采用 Royalty-free Desktop License 2.0，并在顶层“关于”面板保留官方 AboutSlint 组件。原文在 licenses/；MinGW 静态运行库声明也保留。Rust 工具链、编译依赖与可选大模型不会作为基础软件运行依赖隐藏在外部。

目前 EXE 体积只是查看器阶段的数据，不能据此宣称完整产品已达到 50 MB 目标。完整功能/模型/GPU 组件、依赖和许可清单在后续阶段验收后统计。正式发布、main 合并都需要用户确认。
