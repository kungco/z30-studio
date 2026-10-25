# Z30 Studio 开发检查点

更新：2026-10-25（Asia/Hong_Kong）。原生迁移总任务未完成，先处理下述画质失败。

- 当前阶段：1 限定验收完成；2A RAW 核心和首轮内存优化通过；2B float/导出部分实现，暖色完整样片未通过验收。RAW/调色/导出/AI UI 继续关闭，不进入阶段 3 冒充完成。
- 分支 feat/native-slint，草稿 PR https://github.com/kungco/z30-studio/pull/2；基线 a2a2e089d760221a52c8774cca8a2e42f0a82269。2A 提交 7f0a5e8、内存优化 faed1ef；最新 SHA 以 git rev-parse HEAD 和 .development/checkpoint.json 为准。
- 已实现：Slint 原生查看器/评分/草稿/独占与 CAS；安全 LibRaw 0.22.2 动态桥接、RGB16→float、八方向/裁剪/半尺寸；九参数 float 核心、滚动锐化、sRGB ICC JPEG/PNG/TIFF16 的不覆盖输出。原 Python 全部保留，核心完成不等于 UI 完成。
- 2A 实际四组 12/14 位全/半尺寸输出与旧 oracle 逐像素完全相同。提前释放解码上下文后，全尺寸峰值约 561→361 MiB，半尺寸 174→115 MiB；全尺寸约 2.2 秒仍慢于 Python，主要在 LibRaw process。报告 docs/reports/native-stage2a.md。
- 2B 通过范围：17 项 Rust、八组冻结九参数输出；中性真实样片误差最多 1.79e-7；合成 TIFF16 经独立 tifffile/Pillow 验证为 16 位、1024 级、有 ICC、重名拒绝。**不是完整 NEF 导出验收**。
- 2B 失败：暖色+锐化四组真实样片最大误差约 .0073–.008，超过 2e-5；成功标记为 false，不能放宽阈值或只改测试掩盖。14 位半尺寸最差 (1016,1558)，3901 个通道超标，不在分段边缘。色域边界数值决策只是待验证假设。复现参数/结果/性能在 docs/reports/native-stage2b.md、native-stage2b-warm-failed.json。
- 原有回归：tests/ 150 项（原跳过的 2 项已单独设置实拍目录复测通过）+ 根目录原 11 项 Codex 测试，合计 161 项覆盖；Node 31 项通过。faed1ef 的 Windows MSVC native 和原有双 OS CI 已通过；2B 必须看自己的后续 CI。
- UI：用户确认可见且可操作；桌面自动化黑屏/激活失败未解决，未宣称自动截图/点击验收通过。旧开发窗口可能仍由用户使用，不要盲目关闭它。
- 未完成：暖色画质闭环、原生 ICC 输入/无 ICC 完整策略/广色域、预览和导出 UI、AI、批量、高级暗房/人像、GPU、完整功能/性能/体积/分发。用户实机、校准显示器/灰卡、干净 Windows、云推理未验证；总计划见 DEVELOPMENT_PLAN.md。

## 恢复环境和顺序

1. 先读本文件、DEVELOPMENT_PLAN.md、.development/checkpoint.json，核对 git status/log；旧交接中的 Python 完成是历史，不能当成原生完成。
2. 本机独立 Rust 1.99.1 GNU + w64devkit 在 D:/codex/toolchains/z30-native；执行 tools/native_build.ps1 -ToolchainRoot D:/codex/toolchains/z30-native -Test -RawProbe -ExportProbe。不要重新盲装工具链；旧中断的版本化安装可能不完整。
3. 公开样片在 D:/codex/2026-10-23/ni-k/work/Z30-12bit.NEF、Z30-14bit.NEF，官方 DLL 在 work/native-libraw/LibRaw-0.22.2/bin/libraw.dll；都不得提交或覆盖。显式 RAW 测试设置 Z30_LIBRAW_DLL、Z30_RAW_12、Z30_RAW_14 并加 -RawFixtures；Python 实拍测试设置 Z30_RAW_SAMPLES 为 work 目录。
4. 优先继续暖色差异：用 tools/native_raw_benchmark.py --dll DLL --raw12 RAW12 --raw14 RAW14 --develop-parameters .development/develop-warm.json。本机该 JSON 已有；换环境按报告中的九参数重建。大数组和失败现场在 .development/native-raw/run-acf4cc0951c044708b070e22f336e313，纯 RAW 对照在 run-f4d94c951f4047868b2ec96692448251，中性在 run-af7d6ccf5cee4556987638f9e2c62605。
5. 隔离无锐化/仅 WB/仅饱和/仅自然饱和，冻结最差区域/中间量，检查 OKLab/色域边界的浮点决策、halo 和 LUT。先修源码并复跑完整四组，不跳到 AI，不靠均值掩盖最大误差。其后再做 ICC 输入、显示和真实全尺寸导出门槛。
6. 17 项常规 Rust 已通过；真实 fixture 用例常规显示 ignored，必须显式运行。ICC 输出同时读 TIFF IFD 与类型化 decoder；通用 ImageReader 的 TIFF 元数据入口曾返回 None，后续 ICC 阶段要调查，独立文件验证通过不代表所有读取入口完成。
7. 大任务前/阶段边界读 get_usage_limits。只保存去账户身份的快照；约剩 10% 时收尾/提交/检查点，不开启大任务。未知就记未知，不猜百分比。tools/development_checkpoint.py 原子写状态，不会自动启动 Codex。

没有启用额度重置自动续跑或循环调度。不会自动消费推理额度、读取 auth.json、提交密钥/原片、合并 main、发布正式版或关机。普通草稿修改和源码提交已在授权范围内。
