# Z30 Studio 开发检查点

更新：2026-10-25（Asia/Hong_Kong）。当前原生迁移进行中，整个总任务未完成。

- 当前阶段：阶段 2A RAW 核心限定验收通过；继续内存/耗时优化，再做阶段 2B ICC/float 调色/导出。
- 分支 feat/native-slint；Python 基线 a2a2e089d760221a52c8774cca8a2e42f0a82269；原生界面 835c6ed、保存保护 e2c5d11。实时最新 SHA 以 git rev-parse HEAD 和提交后 .development/checkpoint.json 为准。
- 已完成：可运行 Slint 原生三栏窗口，后台参考 JPEG/PNG 查看、评分/草稿恢复、独占/冲突保护/原子保存、最新请求队列；独立构建/版本目录、Windows CI、进度和检查点工具。原 Python 全部保留。
- 测试：158 Python 原有基线 + 3 新检查点、31 Node、7 Rust 通过；835c6ed 的 MSVC 与既有回归 CI 通过；保存保护后续提交需等其自己的 CI。实际 EXE 两轮退出 0，PATH 无 Python、中文路径、重启/重复实例/原片不变通过；报告 docs/reports/native-stage1.md 和 native-stage1-smoke.json。
- UI：用户确认可见可操作；桌面自动化黑屏/激活失败未解决，未宣称自动截图/点击验收通过。不是程序崩溃结论。
- 阶段 2A：raw.rs/native_raw.c/raw_geometry.rs/frame.rs 已实现安全 LibRaw 0.22.2、RGB16→float、裁剪/八方向。12 项 Rust + 显式 12/14 位全/半尺寸样片通过；四组与旧 oracle 逐像素完全一致。初版较慢/较耗内存，详见 docs/reports/native-stage2a.md，未宣称性能达标。
- 未完成：原生 RAW 界面/ICC/float 调色与 JPEG/PNG/TIFF16、AI、批量、完整高级暗房/人像、GPU、全功能一致性/体积/正式交付，详见 DEVELOPMENT_PLAN.md 与功能矩阵。
- 已知风险：阶段 1 仅查看器，无 ICC 暂按 sRGB，专业色彩/透明输入尚待阶段 2；旧开发构建无目录锁，升级前关闭旧构建。原有 Python 一次无关停确认现场仍未全归因。没有真实云推理、专业实拍/干净机器/多 DPI/GPU 验收。
- 日志：D:/codex/2026-10-23/ni-k/work/native-baseline-python.log、native-baseline-node.log、native-stage1-safety.log、native-stage1-final-build.log、native-stage1-smoke.log。
- 工作区：提交前核对 git status；阶段 2A 源码/回归/对照报告正在提交。未验证代码不要标完成。

## 环境和恢复

1. 先读本文件、DEVELOPMENT_PLAN.md、本机 .development/checkpoint.json，再核对 git status/log；旧 AI_HANDOFF 的 Python 全清单完成不是原生完成。
2. 本机独立 Rust 1.99.1 GNU + w64devkit 在 D:/codex/toolchains/z30-native；无需改全局 PATH。运行 tools/native_build.ps1 -ToolchainRoot D:/codex/toolchains/z30-native -Test。已解决下载/dlltool/CRT 环境问题，不重新盲装工具链；中断的版本化安装可能不完整。
3. 每次构建从 native/target 的内部 z30-native.exe 复制到新的 native/dist/stage-*/Z30Studio.exe，避免覆盖正在运行版本。基线公开 NEF 仍在仓库外 work/Z30-12bit.NEF 和 Z30-14bit.NEF；不得打包/覆盖。
4. 阶段 2A 已通过 RAW 基线。先依据 mem_image.cpp 独立分配契约提前释放解码上下文并增加分段计时，再重测内存/耗时；随后移植 ICC、processing.py 九参数/OKLab/肩部及真正 TIFF16，不提前开放 UI。公开样片测试详见 native/README.md 的环境变量与 -RawFixtures；固定 DLL 在 work/native-libraw/LibRaw-0.22.2/bin/libraw.dll。
5. 大任务前/阶段边界调用 get_usage_limits，保存去账户身份的 .development/quota-snapshot.json。约低于 10% 剩余只收尾保存；未知就使用定期检查点，不猜百分比。当前快照不是实时保证。
6. 运行 python tools/development_checkpoint.py --stage 阶段名 --next 下一步 --quota-file .development/quota-snapshot.json --report 已有日志；文件原子保存，不自动启动 Codex。仅已验证代码独立 commit。

没有启用额度重置自动续跑，没有创建循环/系统提权调度；官方本地定时任务需要电脑/应用运行，尚无可靠重置事件接口验证。不自动消费模型额度、读取 auth.json、提交密钥/照片、合并 main、发布正式版或关机。
