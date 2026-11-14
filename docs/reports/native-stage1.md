# 原生迁移阶段 1 验收

日期：2026-10-25（Asia/Hong_Kong）。Python 对照基线 a2a2e089d760221a52c8774cca8a2e42f0a82269；原生界面/核心提交 835c6ed，保存保护提交 e2c5d11。后续构建以包内 build-info.json 的提交/dirty 标记为准。此阶段是查看器基础，不是整款软件迁移完成。

## 实际改动

native/crates/core 提供只读照片引用、JPEG/PNG 预览、版本/字段验证、工作目录独占锁、磁盘保存冲突检查、原子落盘与最新请求队列；native/crates/desktop 是 Slint 三栏原生窗口、原生文件选择、缩放/评分/恢复、失败保存退出提示、后台预览与有界 QA 标记。tools/native_build.ps1 生成 Debug/Release、复制到独立版本目录；.github/workflows/native-windows.yml 实际构建 Windows MSVC；tools/smoke_native.py 做隔离进程验收。检查点工具/计划/进度和新交接说明均落盘。

## 验证

- 本轮 Python 基线 158 项（44.014 秒，含公开 Z30 12/14 位 NEF）与 Node 31 项通过；另新增检查点测试 3 项通过。原 11 项 Codex CLI 契约测试未修改。
- Rust 核心 7 项通过：原片不变、重复路径、草稿往返/版本/坏 JSON、不覆盖外部修改、工作目录独占、过期/取消结果拒绝、结果未读取时仍保留最新预览。
- Rust 1.99.1 / Slint 1.18.1 本机 GNU Debug/Release 构建实际成功；835c6ed 的 Windows MSVC CI 与 Python/Node CI 均通过：[原生 Windows](https://github.com/kungco/z30-studio/actions/runs/40178089459)、[既有回归](https://github.com/kungco/z30-studio/actions/runs/40178089434)。保存保护后续提交将另跑 CI，不把旧 SHA 的 CI 当成新提交验证。
- 实际隔离 Release EXE smoke 见 native-stage1-smoke.json：移除 Python/开发工具 PATH，中文/空格目录，两轮启动/参考 JPEG/草稿重启恢复，第二进程被拒绝且未改写状态，输入 SHA 不变。没有实际 RAW 解码或云推理。
- 用户确认原生窗口可见且可操作。桌面工具曾返回黑色截图、Backstop Window 遮挡、failed to activate captured window；没有把自动截图/点击记为成功，没有用设计稿冒充窗口截图。自动 UI 操作、高 DPI 多屏仍未验证。

| 隔离运行 | 启动到事件循环标记 | 预览计算就绪（进程内） | 峰值工作集 | 正常退出 |
|---|---|---|---|---|
| 1 | 185.3 ms | 99.1 ms | 41.59 MiB | 0.031 s，退出 0 |
| 2 | 155.2 ms | 96.2 ms | 41.37 MiB | 0.047 s，退出 0 |

事件循环/预览标记不是屏幕首帧或最终画面显示时间。样片为从公开 NEF 生成的 1238×1856 sRGB JPEG 参考图，并非原生 RAW 验收。当前 Release 单 EXE 约 15.3 MB、没有随附模型/解释器/浏览器引擎；这不代表完整原生产品达到 50 MB，不能与已具完整功能的 Python 分发直接宣称体积/性能提升。

## 环境恢复

本机没有现成 MSVC SDK，独立工具链置于 D:/codex/toolchains/z30-native，不改全局 PATH。官方大文件和 Cargo 曾零字节超时，按官方 SHA / Cargo.lock 校验分段缓存后完成。GNU 链接使用 Rust 自带 CRT（link-self-contained=yes）和便携 w64devkit 的 dlltool/汇编器；不要混用未配置的 libgcc。可用的 stable 工具链实际版本 1.99.1；取消过的版本化安装目录可能不完整，使用已验证的 tools/native_build.ps1 -ToolchainRoot 路径，不重复安装。

## 残余风险/后续

NEF 原生解码、完整 ICC/位深/调色/导出、AI、批量、高级暗房/人像与完整旧项目格式仍待迁移。当前只显示未配置 ICC 的参考 JPEG/PNG，嵌入 ICC 明确拒绝；无 ICC 的高级色度/Exif/透明色策略尚待阶段 2，不能作为专业颜色判断或正式导出。完整依赖许可清单、干净 Windows、Windows 10/11/多 DPI/小内存压力、RTX 4060 GPU 与人物画质仍未完成最终验收。旧 Python 2.0 保留且未被替换。

阶段 1 按“基线 + 可运行原生 GUI + 核心/生命周期/恢复检查点”的限定验收推进；后续阶段继续逐项核对功能矩阵，不把当前可运行窗口算作整体任务完成。不合并 main、不发布正式版、不继承历史关机请求。
