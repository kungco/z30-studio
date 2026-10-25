# Z30 Studio 开发检查点

更新：2026-10-25（Asia/Hong_Kong）。本文件属于用户新一轮 Rust/Slint 迁移，旧 2.0 全清单完成不等于原生迁移完成。

- 当前：阶段 1，基线复测已完成，Rust/Slint Debug/Release 已构建，继续安全性/生命周期验收。
- 分支：feat/native-slint；计划/检查点工具已提交 240b0ac；对照基线 a2a2e089d760221a52c8774cca8a2e42f0a82269。实时提交执行 `git rev-parse HEAD`；本机提交后快照记录在 .development/checkpoint.json。
- 已完成：原 Python 全功能保留；本次 158 Python（44.014 秒，含公开 12/14 位 Z30 NEF）/31 Node 通过；没有修改原 11 项 Codex 契约测试；新增检查点 3 项、Rust core 5 项均通过。Release EXE 15,236,128 字节（仅查看器阶段，不是完整迁移体积）。
- 正在进行：安装独立本机 Rust 工具链（不改全局 PATH）、建立 native/ 工作区、Slint 原生 UI、后台预览和恢复检查点工具。
- 未完成：阶段 1 的实际构建/UI 验收；阶段 2–6 全部原生功能，详见 DEVELOPMENT_PLAN.md 与功能矩阵。
- 已知问题：已验证独立 Rust 1.99.1 GNU 可执行；官方大文件/部分 Cargo 下载曾超时，已用官方清单/锁文件 SHA 校验缓存。首编译缺 GNU 汇编器，已补齐经发布 SHA 校验的便携 w64devkit 2.10.1（仅构建工具，不进产品）；已继续定位并修正 dlltool/汇编器与 libgcc 链接器路径顺序；依赖已按 Cargo.lock 校验补齐。该构建路径已通过 Debug/Release。桌面工具出现黑屏与 failed to activate captured window；用户已确认真实窗口可见且可操作，自动截图/点击仍未验证。旧版一次无首条退出确认现场仍未完全归因；不能当成原生版本已解决的证据。
- 工作区修改：已有 native/ 工作区、Slint 界面、后台最新请求队列、只读照片引用/本机草稿、构建脚本/Actions 和 QA 文件标记代码；已构建和 unit 验证的界面/核心将独立提交；仍有待补的多实例/冲突保护与完整验收。；保存时须核对 git status，不用文档代替实际代码。
- 测试日志（本机，不含凭证）：D:/codex/2026-10-23/ni-k/work/native-baseline-python.log、native-baseline-node.log；旧报告在 docs/reports/，标记为历史对照。
- 额度：本轮开始工具实际读数为 5 小时窗口已用 22%、周窗口已用 3%；不是实时读数，启动下一大型步骤前重新读取。没有启用额度重置自动唤醒。

## 接续操作

1. 核对 `git status --short --branch` 与 `git log -6 --oneline`，保留其他人修改，不从零设计。
2. 读取 `D:/codex/toolchains/z30-native/install.log`；执行 tools/native_build.ps1 -ToolchainRoot D:/codex/toolchains/z30-native -DebugBuild -Test；正在运行的编译日志在 work/native-stage1-debug.log。不要重新启动已结束的下载；版本号必须匹配 native/rust-toolchain.toml。
3. 补齐单实例/保存冲突保护、生命周期/恢复/体积报告与阶段检查点，再迁移 LibRaw/色彩；没有通过原生画质对照前不替换 Python 产品。
4. 安全暂停时运行检查点工具，记录失败命令、下一步与未提交文件；已验证代码单独提交。停止只有本轮自己启动的进程。

本轮不继承历史关机请求；不自动合并 main、发布正式版、读 auth.json、提交 Key/个人照片或发起收费推理。
