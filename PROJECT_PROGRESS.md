# Z30 Studio 开发检查点

更新：2026-10-26（Asia/Hong_Kong）。原生迁移总任务未完成。

- 当前阶段：1 和 2A–2G 的限定验收完成；2G 干净 RAW 软件包与 d18a6aa 的两项 GitHub CI 已核对成功。3A 已接通原生 Codex CLI 一键/建议调色、用户目标与强度、严格九参数、像素画质回退、异步取消及逐照片会话检查。3A 的离线模型进程、实际窗口和公开 12/14 位 NEF 已验收；可选兼容 API/本机后端及真实云端推理尚未完成，阶段 3 未完成。
- 分支 feat/native-slint，草稿 PR https://github.com/kungco/z30-studio/pull/2；原 Python 基线 a2a2e089d760221a52c8774cca8a2e42f0a82269。最新 SHA 以 git rev-parse HEAD 和 .development/checkpoint.json 为准；不合并 main、不正式发布。
- 已实现：原生查看器/评分/草稿/独占与 CAS，LibRaw 0.22.2 只读桥接、RGB16→float、八方向/裁剪/半尺寸，九参数 float 核心/滚动锐化，sRGB ICC JPEG/PNG/TIFF16 不覆盖输出。2F 将现成核心接到实际窗口，仍非完整迁移产品。
- RAW 纯解码四组与原 Python 逐像素相同；提前释放上下文后全尺寸峰值约 561→361 MiB，半尺寸 174→115 MiB。全尺寸解码仍慢于 Python。见 docs/reports/native-stage2a.md。
- 暖色根因：旧 float32 十轮色度搜索把两 ULP 的亮度误差放大成约 .008 蓝通道跳变。Python/Rust 同时改成精确色彩路径、24 轮及连续自适应内边界。独立三次多项式参考和微扰回归通过；未放宽误差阈值或原饱和度测试。见 docs/reports/native-gamut-stability.md。
- 修正 Rust/修正 Python 四组暖色+锐化最大误差均 <7e-6（门槛 2e-5），均小于一个 RGB16 码值，无非有限值，原片 SHA 不变。旧失败报告保留；与固定原始 Python 的差异报告 success=false，最大约 .014–.063，必须单独审阅，不能宣称旧输出一致。
- 当前验证：45 Rust 核心 + 1 原生桌面测试通过；真实 RAW 核心 fixture 默认 ignored，2G 已用随包 DLL 显式通过。171 Python：169 通过、2 个公开 RAW 测试在普通环境下跳过；31 Node 通过。3A 的模拟 Codex CLI 实际进程/回调 61 项检查通过，含真实 Z30 12/14 位 NEF、退出取消、坏 JSON、画质回退、原片哈希；无真实云推理请求。阶段 3A 的新 CI 必须在同步提交后单独核对。
- 2C：原生 ICC/PNG 元数据/Adobe EXIF/16 位 PNG、原地八方向/线性缩放已接入查看器。最终 MozJPEG 精确 DCT 的全尺寸无 ICC JPEG 两组与旧 Python 逐像素相同，约 .32 秒/302 MiB；带 ICC 优化由约 11.8 秒降至 .51 秒，逐点 CMS 质量对照通过。读 docs/reports/native-stage2c.md / native-raster-icc.json，报告 docs/reports/native-icc-performance.md；复杂 LUT 仍走通用 CMS。
- sRGB 插值优化：35 Rust/200 万点解析参考通过，四组 RAW 暖色仍 <一个 RGB16 码值；全尺寸显影约 8.4→5.6–5.8 秒，完整最终文件重测通过。见 docs/reports/native-gamma-performance.md。
- 历史性能仅单次：14 位全尺寸原生调色约 8.08 秒/479 MiB，修正 Python 13.91 秒/757 MiB；原始 Python 约 5.5 秒，不能宣称总体提速。另次原生测得约 10 秒，负载/频率变动尚未统计。
- 2D：两份 12/14 位公开 NEF 真正导出完整 JPEG/PNG/TIFF16，通过独立 Pillow/tifffile 检查；TIFF16 覆盖 65536 码值，PNG/TIFF 与渲染量化逐点一致，原片/重名保护通过。JPEG 编码约 31–34 秒降至 .70 秒，PSNR 未降；后续显影降至约 5.6–5.8 秒。见 docs/reports/native-stage2d-export.md。ICC 通用 ImageReader TIFF 元数据入口曾返回 None，类型化 decoder/独立 IFD 验证通过，后续要查适配/限制。
- 2E：原生 v2 草稿保存每照片 Recipe，读 v1/v2，迁移前备份原 JSON；50 步内存撤销/redo、单帧编辑缓存通过。真实 RAW14 缓存暖色约 .7–.8 秒/362 MiB（后续 sRGB 优化复测见 gamma 报告），实际程序 v1 恢复迁移/备份/重启通过。见 docs/reports/native-stage2e-foundation.md。这些接口现已在 2F 接入编辑 GUI。
- 2F UI：实际 EXE 的 12/14 位公开 NEF、JPEG/PNG、独立照片历史、取消/切图/恢复/导出中退出通过。Computer Use 已实际操作曝光、观察调整前后画面，并用真实保存对话框导出全尺寸带 ICC JPEG。新旧 14 位 NEF 同参数 TIFF16 逐码值相同。详见 docs/reports/native-stage2f-ui.md；不把旧截图失败覆盖成成功。
- 未完成：阶段 3 可选兼容 API/本机后端、真实 Codex/DeepSeek 云推理；干净 Windows 实机/虚拟机、复杂 ICC/CMYK/HDR/显示管理、广色域、批量、高级暗房/人像、GPU、完整分发。用户实拍/肤色灰卡/校准显示器未验证。

## 2F 收尾与 2G 分发

2026-10-26：用户新请求后恢复界面验收，1c973d 干净构建最终布局已目视确认，格式框/工具栏尺寸正常。该提交的公开 RAW14 回调/进程复测通过，已同步 PR #2，两项 GitHub CI 成功。历史 Esc 中断已保留在旧阶段报告中，不作为永久阻塞。

2G 增加程序目录 RAW 自动发现、固定源码 GNU 构建、系统 DLL 依赖审计、对应源码/许可随包、原子不覆盖 ZIP 和完整性校验。12/14 位全尺寸/半尺寸与官方 DLL 四组逐像素相同，原片不变；系统 PATH、无 RAW 环境变量的工作区构建编辑/导出/退出/重启通过。发现并修复 Cargo 缓存造成进程内旧干净标记的问题，新增进程/包来源一致性检查；最终交付只使用提交后的干净构建。见 docs/reports/native-stage2g-runtime.md。最终 ZIP 路径、哈希及解压后实测记录以 .development/final-artifact.json 为准。

## 3A 原生 Codex CLI

采用现有本机 `codex login`，一次请求仅传两张最长边 1024 像素、无 EXIF/GPS 的 sRGB JPEG 预览、当前九参数、白平衡模式和目标；不读凭证、不传文件名/原片/路径。模型输出经过严格 Schema/范围验证、强度限制和像素画质检测，分档回退后才自动应用；可选只给建议，应用成为一步撤销。照片切换/编辑/关闭会取消正在运行的 CLI，旧结果不能改新照片。见 docs/reports/native-stage3a-ai-cli.md。用户自备 CLI 登录下的真实模型效果和费用未验证，离线 QA 不代表真实云端成功。

原有 24 项编辑/导出 UI smoke 复跑通过；Stage 2G 与 3A 的同配方公开 RAW14 全尺寸 TIFF16，62,005,248 个通道码值及整个文件 SHA-256 相同。Debug/Release 耗时不可直接比较，见 3A 报告。

## 恢复环境和顺序

1. 阅读本文件、DEVELOPMENT_PLAN.md、.development/checkpoint.json，核对 Git 状态。旧 Python 完成声明不是原生完成。
2. 独立 Rust 1.99.1 GNU/w64devkit 位于 D:/codex/toolchains/z30-native；执行 tools/native_build.ps1 -ToolchainRoot D:/codex/toolchains/z30-native -Test -RawProbe -ExportProbe。不要重装或使用曾中断的版本化目录。
3. 样片为 D:/codex/2026-10-23/ni-k/work/Z30-12bit.NEF、Z30-14bit.NEF；官方 DLL 在 work/native-libraw/LibRaw-0.22.2/bin/libraw.dll。不得提交/覆盖；Python 实拍测试设置 Z30_RAW_SAMPLES=work，Rust fixture 显式设置 Z30_LIBRAW_DLL/Z30_RAW_12/Z30_RAW_14 并用 -RawFixtures。
4. 基准 tools/native_raw_benchmark.py 默认 --oracle-mode baseline 固定原始算法；修正实现对照显式用 --oracle-mode current，暖色参数见 native-stage2b.md。修正四组现场 .development/native-raw/run-b45b77eb14d04c15818b9cdc46c04aa4；旧算法差异 run-fd4235e248814befb8d294ad0ad02c13。
5. 2G 干净 ZIP、解压后复测和对应 d18a6aa GitHub CI 已完成；3A 用 tools/smoke_native_ai.py 的模拟 CLI 验证一键调色及真实 12/14 位 NEF 流程。下一步实现阶段 3B 可选 OpenAI 兼容/本机后端，保持 CLI 默认且不自动发起真实云推理。每次提交后重新做干净包/CI；有干净 Windows 环境时再做独立安装验收。ICC/CMYK/HDR/显示器的剩余范围按功能清单迁移。
6. 大任务前和阶段边界读 get_usage_limits，只保存去账户身份快照；剩余约 10% 时收尾/提交/检查点，不开新大任务。未知记未知。checkpoint 脚本不会自动启动 Codex。

未启用额度重置自动续跑。不会自动花费推理额度、读取 auth.json、提交凭证/原片、合并 main、发布正式版或关机。

额度规则：约剩 10% 时按总任务规则保存断点，不开启新大任务；不是总任务完成。最新构建/源码以 .development/checkpoint.json 和 build-info.json 为准，未启用自动额度重置续跑。
