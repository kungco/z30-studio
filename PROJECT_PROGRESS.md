# Z30 Studio 开发检查点

更新：2026-10-27（Asia/Hong_Kong）。原生迁移总任务未完成。

当前新增 5F1：受限局部浮点形变核心、四点/10%半径位移/平滑边界与防折叠约束、合成坐标后单次插值、取消和预算保护已实现。Rust110+两项显式fixture、Python180/Node31及独立NumPy八组通过（像素误差0、坐标约3.55e-15），旧十组修复对照保留。合成20MP四点约.058秒/460MiB，非真实NEF或人像性能。尚未接桌面、配方/保存或人脸轮廓定位，用户可用的修复工具仍为5E2b。源码/干净包289项和对应CI以checkpoint为准；详见 docs/reports/native-stage5f1-warp-core.md/json。下一步5F2绑定/流程/标记与UI；自动瑕疵、真实自然轮廓/GPU及阶段6仍待，总任务未完成。

历史 5E2b：实际鼠标修复/克隆、重新取样、选点/独立参数/移除/清除和撤销，缩放/平移/像素中心与留白判定已接入。后台取样使用当前线性缓存，原片或编辑变化、切图、取消和弹窗阻止旧结果写回。Rust105+两项显式fixture、完整 Python180/Node31和十组冻结数值通过；新增公开12/14位NEF真实RGB16与窗口回调验证、computer-use实际点选/克隆/滑块/撤销/平移通过。最终干净包完整289项（旧233+工具56）/对应CI与提交以checkpoint为准；详见 docs/reports/native-stage5e2b-repair-tools.md/json。自动瑕疵识别、轮廓/GPU和阶段6仍待，总任务未完成。

历史 5E2a：修复集合接入原片绑定、v6 草稿/v4 队列备份迁移、独立撤销、预览与真实 TIFF16/冻结批量。Rust98、Python180/Node31及最终干净包233项/六项CI通过，原片SHA不变，含重任务取消。详见 docs/reports/native-stage5e2a-repair-recipes.md/json。本阶段尚未提供鼠标工具，5E2b在此基础继续实现。

历史 5E1：已迁移未量化线性 RGB 克隆/均值修复与确定性邻近取样核心，尚未接桌面/配方/保存/导出，不是自动祛痘。Rust 93、冻结旧版十组浮点对照（最大 4.77e-7）、完整 Python 180（含公开 RAW，无跳过）和 Node 31 通过；合成 2000 万像素 32 点约 .040 秒/460 MiB，额外一份输出约 228 MiB。详见 docs/reports/native-stage5e1-retouch.md/json。最终提交/干净构建/CI 以 checkpoint 为准；下一步 5E2 接来源绑定和完整工作流。5D 干净包 207 项及六项 CI 已通过；总任务仍未完成。

历史 5D：肤色均匀/提亮的独立强度、v5 草稿/v3 队列迁移、原片变化阻止应用、撤销/批量/无模型恢复与 TIFF16 已实现。修复切换不同滑块时手势被合并为一次撤销的问题。Rust 86、八组独立 NumPy 对照、29 项窗口回调及实际点击/布局/独立撤销/蒙版对比通过；完整 Python 180（默认两项 RAW 跳过后另跑通过）、Node 31 通过。约 2000 万像素合成图三轮 CPU 肤色处理约 .49–.72 秒，均匀处理进程峰值比禁用约增加 3.7 MiB；不是实际 NEF 人像性能/画质验收。详见 docs/reports/native-stage5d-skin-tone.md/json。提交、干净 ZIP/207 项解压流程和对应 CI 以 `.development/checkpoint.json` 为准，不能把提交前构建当成最终干净包。下一步瑕疵/轮廓/GPU，阶段 5 总体未完成。

历史 5C2：皮肤识别已接入原生桌面、绿色实际区域预览和完整配方路径。蒙版不可变共享、无损压缩、校验并随 v4 草稿/v2 队列保存；可撤销/重启，无模型也能恢复 PNG 人像并导出，公共批量不串片。Rust 78、完整 Python 180（默认 2 跳过后另跑公开 RAW）、Node 31 通过；皮肤窗口 23、人像 18、RAW 14 和官方模型九组对照通过，实际点击/放大/调整前检查通过。256 照片/512 任务存储基准通过，最坏蒙版亦在 32/64 MiB 预算内。详见 docs/reports/native-stage5c2-skin-workflow.md/json；最终干净包和当前提交 CI 以 .development/checkpoint.json 为准。用户 Z30 人像、多肤色/遮挡、干净 Windows/GPU 未验证；下一步瑕疵/肤色等后续人像能力，阶段 5 总体未完成。


历史 5C1：许可已核对的本机皮肤语义识别、绑定原片的保守蒙版和实际浮点柔化入口，独立 Google TFLite/ONNX/Rust 九组对照通过。Rust 71 通过、2 项默认 ignored 随后显式通过；Python 179（2 跳过）、Node 31 通过，既有人像/RAW 窗口 18/14 项及 YuNet 五组参考通过。五份 RAW TIFF16 与 5B 逐字节一致。**语义功能尚未接入桌面或持久会话**，下一步 5C2 补按钮/蒙版预览、草稿/撤销/批量与导出一致性；模型安装和最终界面验收不能省略。实体倒置 180° 公开图返回空蒙版，用户 Z30 人像/多肤色/干净 Windows/GPU 未验证。详见 docs/reports/native-stage5c-skin.md；干净构建、GitHub 和 CI 以 .development/checkpoint.json 为准。

历史 5B：桌面面部定位、原片绑定的保守柔化、撤销/重启恢复和 TIFF16/逐张批量已实现。新增核心几何/色彩/绑定回归，Rust 66 通过、2 ignored；完整 Python 175（2 跳过）、Node 31 通过；人像窗口 18、RAW/批量/编辑 14/14/26、离线 CLI/HTTP 61/45 项通过，并实际点击/复看窗口。关闭修饰时两份 RAW TIFF16 与 4C 逐字节一致。可选组件已接入打包，仍依赖系统 VC++ x64；语义皮肤/瑕疵/肤色/轮廓/GPU 尚未完成。提交前验证与最终干净包/CI 分开记录，最终状态见 .development/checkpoint.json；详见 docs/reports/native-stage5b-portrait.md。

阶段 4 最终交付：`0539886` 干净 RAW ZIP 已解压完整性验证，随后 RAW/批量/编辑 14/14/26 项全部通过，进程来源标记与包清单一致；该 SHA 的 Windows/Ubuntu/原生 MSVC CI 全部成功。ZIP 为 `native/dist/Z30Studio-native-stage4c-0539886.zip`，12,460,164 字节，SHA-256 `7d0419c0c3bca9f6532be434dc81d55aab2f0bed284734929f69e60c20657179`。阶段 5A 不改变该历史交付包。

最新进展：阶段 4A `c1b83e4`、4B `d4900ff` 已独立提交；4C 加入有界全尺寸缓存并完成公开 Z30 12/14 位 RAW、速度与内存验收，源码提交和干净包以最新 Git/检查点为准。RAW 窗口 14 项、合成照片批量 14 项、原有编辑/导出 26 项通过；三轮重复 TIFF16 导出约 2.0→0.8 秒且所有字节一致。缓存核心峰值约 478→596 MiB；普通批量未命中不保留新缓存，窗口峰值约 551 MiB。阶段 4 为限定范围验收，磁盘缓存/并行/ETA/批量 AI 等仍待完整迁移。见 `docs/reports/native-stage4c-performance.md`。

- 当前阶段：1、2A–2G 和 4A–4C 通过限定验收。3A/3B 接口与离线模拟通过，真实云端/本机大模型未验证。5A/5B 面部定位/几何柔化已接入桌面；5C1 核心与原模型对照、5C2 桌面/持久化和 5D 独立肤色控制通过限定验收，阶段 5 及阶段 6 完整迁移尚未完成。
- 分支 feat/native-slint，草稿 PR https://github.com/kungco/z30-studio/pull/2；原 Python 基线 a2a2e089d760221a52c8774cca8a2e42f0a82269。最新 SHA 以 git rev-parse HEAD 和 .development/checkpoint.json 为准；不合并 main、不正式发布。
- 已实现：原生查看器/评分/草稿/独占与 CAS，LibRaw 0.22.2 只读桥接、RGB16→float、八方向/裁剪/半尺寸，九参数 float 核心/滚动锐化，sRGB ICC JPEG/PNG/TIFF16 不覆盖输出。2F 将现成核心接到实际窗口，仍非完整迁移产品。
- RAW 纯解码四组与原 Python 逐像素相同；提前释放上下文后全尺寸峰值约 561→361 MiB，半尺寸 174→115 MiB。全尺寸解码仍慢于 Python。见 docs/reports/native-stage2a.md。
- 暖色根因：旧 float32 十轮色度搜索把两 ULP 的亮度误差放大成约 .008 蓝通道跳变。Python/Rust 同时改成精确色彩路径、24 轮及连续自适应内边界。独立三次多项式参考和微扰回归通过；未放宽误差阈值或原饱和度测试。见 docs/reports/native-gamut-stability.md。
- 修正 Rust/修正 Python 四组暖色+锐化最大误差均 <7e-6（门槛 2e-5），均小于一个 RGB16 码值，无非有限值，原片 SHA 不变。旧失败报告保留；与固定原始 Python 的差异报告 success=false，最大约 .014–.063，必须单独审阅，不能宣称旧输出一致。
- 阶段 4 验证：49 Rust 核心 + 2 原生桌面测试通过，另用随包 DLL 显式通过 1 项真实 RAW 全尺寸/半尺寸 fixture。171 Python：169 通过、2 个公开 RAW 测试在普通环境下跳过；31 Node 通过。4C RAW/合成批量/原有编辑窗口流程分别 14/14/26 项通过。历史 3A 模拟 Codex CLI 61 项、3B 模拟 HTTP 55 项均已验证真实公开 RAW；没有真实云推理请求。
- 2C：原生 ICC/PNG 元数据/Adobe EXIF/16 位 PNG、原地八方向/线性缩放已接入查看器。最终 MozJPEG 精确 DCT 的全尺寸无 ICC JPEG 两组与旧 Python 逐像素相同，约 .32 秒/302 MiB；带 ICC 优化由约 11.8 秒降至 .51 秒，逐点 CMS 质量对照通过。读 docs/reports/native-stage2c.md / native-raster-icc.json，报告 docs/reports/native-icc-performance.md；复杂 LUT 仍走通用 CMS。
- sRGB 插值优化：35 Rust/200 万点解析参考通过，四组 RAW 暖色仍 <一个 RGB16 码值；全尺寸显影约 8.4→5.6–5.8 秒，完整最终文件重测通过。见 docs/reports/native-gamma-performance.md。
- 历史性能仅单次：14 位全尺寸原生调色约 8.08 秒/479 MiB，修正 Python 13.91 秒/757 MiB；原始 Python 约 5.5 秒，不能宣称总体提速。另次原生测得约 10 秒，负载/频率变动尚未统计。
- 2D：两份 12/14 位公开 NEF 真正导出完整 JPEG/PNG/TIFF16，通过独立 Pillow/tifffile 检查；TIFF16 覆盖 65536 码值，PNG/TIFF 与渲染量化逐点一致，原片/重名保护通过。JPEG 编码约 31–34 秒降至 .70 秒，PSNR 未降；后续显影降至约 5.6–5.8 秒。见 docs/reports/native-stage2d-export.md。ICC 通用 ImageReader TIFF 元数据入口曾返回 None，类型化 decoder/独立 IFD 验证通过，后续要查适配/限制。
- 2E：原生 v2 草稿保存每照片 Recipe，读 v1/v2，迁移前备份原 JSON；50 步内存撤销/redo、单帧编辑缓存通过。真实 RAW14 缓存暖色约 .7–.8 秒/362 MiB（后续 sRGB 优化复测见 gamma 报告），实际程序 v1 恢复迁移/备份/重启通过。见 docs/reports/native-stage2e-foundation.md。这些接口现已在 2F 接入编辑 GUI。
- 2F UI：实际 EXE 的 12/14 位公开 NEF、JPEG/PNG、独立照片历史、取消/切图/恢复/导出中退出通过。Computer Use 已实际操作曝光、观察调整前后画面，并用真实保存对话框导出全尺寸带 ICC JPEG。新旧 14 位 NEF 同参数 TIFF16 逐码值相同。详见 docs/reports/native-stage2f-ui.md；不把旧截图失败覆盖成成功。
- 未完成：阶段 3 真实 Codex/DeepSeek 云推理及真实本机大模型；干净 Windows 实机/虚拟机、复杂 ICC/CMYK/HDR/显示管理、广色域、完整批量工作流、高级暗房/人像修饰、GPU、完整分发。用户实拍/肤色灰卡/校准显示器未验证。

## 2F 收尾与 2G 分发

2026-10-26：用户新请求后恢复界面验收，1c973d 干净构建最终布局已目视确认，格式框/工具栏尺寸正常。该提交的公开 RAW14 回调/进程复测通过，已同步 PR #2，两项 GitHub CI 成功。历史 Esc 中断已保留在旧阶段报告中，不作为永久阻塞。

2G 增加程序目录 RAW 自动发现、固定源码 GNU 构建、系统 DLL 依赖审计、对应源码/许可随包、原子不覆盖 ZIP 和完整性校验。12/14 位全尺寸/半尺寸与官方 DLL 四组逐像素相同，原片不变；系统 PATH、无 RAW 环境变量的工作区构建编辑/导出/退出/重启通过。发现并修复 Cargo 缓存造成进程内旧干净标记的问题，新增进程/包来源一致性检查；最终交付只使用提交后的干净构建。见 docs/reports/native-stage2g-runtime.md。最终 ZIP 路径、哈希及解压后实测记录以 .development/final-artifact.json 为准。

## 3A 原生 Codex CLI

采用现有本机 `codex login`，一次请求仅传两张最长边 1024 像素、无 EXIF/GPS 的 sRGB JPEG 预览、当前九参数、白平衡模式和目标；不读凭证、不传文件名/原片/路径。模型输出经过严格 Schema/范围验证、强度限制和像素画质检测，分档回退后才自动应用；可选只给建议，应用成为一步撤销。照片切换/编辑/关闭会取消正在运行的 CLI，旧结果不能改新照片。见 docs/reports/native-stage3a-ai-cli.md。用户自备 CLI 登录下的真实模型效果和费用未验证，离线 QA 不代表真实云端成功。

原有 24 项编辑/导出 UI smoke 复跑通过；Stage 2G 与 3A 的同配方公开 RAW14 全尺寸 TIFF16，62,005,248 个通道码值及整个文件 SHA-256 相同。Debug/Release 耗时不可直接比较，见 3A 报告。

3A 源码提交 109ce83 已同步草稿 PR #2，对应 Windows/Ubuntu/原生 CI 通过。干净 Release ZIP `native/dist/Z30Studio-native-stage3a-preview.zip` 12,235,211 字节、SHA-256 `a13e99d90f919c3dbad5647e6737cf8cfd802b28a1e0195e26e24017b1cef44c`，解压后完整性/RAW DLL 校验和 61 项 AI 流程通过；模拟 CLI 并未发出云端请求。

## 3B 可选兼容图像接口

默认 Codex CLI 保持不变；AI 设置增加 DeepSeek Flash 及自定义 OpenAI 兼容图像接口。远端仅 HTTPS、本机回环可 HTTP；API Key 通过 `curl --header @-` 的 stdin 传递，不在命令行、临时请求文件或草稿中。两张无 EXIF 的缩小预览进入 Chat Completions；返回的建议复用 3A 的严格解析、参数限制、画质回退、取消、会话版本和撤销。55 项模拟 HTTP 回调/进程检查通过，真实服务商/本机模型和费用未验证；见 docs/reports/native-stage3b-compatible.md。

## 恢复环境和顺序

1. 阅读本文件、DEVELOPMENT_PLAN.md、.development/checkpoint.json，核对 Git 状态。旧 Python 完成声明不是原生完成。
2. 独立 Rust 1.99.1 GNU/w64devkit 位于 D:/codex/toolchains/z30-native；执行 tools/native_build.ps1 -ToolchainRoot D:/codex/toolchains/z30-native -Test -RawProbe -ExportProbe。不要重装或使用曾中断的版本化目录。
3. 样片为 D:/codex/2026-10-23/ni-k/work/Z30-12bit.NEF、Z30-14bit.NEF；官方 DLL 在 work/native-libraw/LibRaw-0.22.2/bin/libraw.dll。不得提交/覆盖；Python 实拍测试设置 Z30_RAW_SAMPLES=work，Rust fixture 显式设置 Z30_LIBRAW_DLL/Z30_RAW_12/Z30_RAW_14 并用 -RawFixtures。
4. 基准 tools/native_raw_benchmark.py 默认 --oracle-mode baseline 固定原始算法；修正实现对照显式用 --oracle-mode current，暖色参数见 native-stage2b.md。修正四组现场 .development/native-raw/run-b45b77eb14d04c15818b9cdc46c04aa4；旧算法差异 run-fd4235e248814befb8d294ad0ad02c13。
5. 2G、3A、3B、4C 干净 ZIP/解压复测/对应 GitHub CI 已完成。5A 核心探针和五组公开图对照、5B 会话/桌面/几何柔化及可选打包通过限定验收；显式 Z30_VISION_DIR / Z30_VISION_IMAGE 可复跑固定 CPU 模型，可选运行库仍有 VC++ 依赖。5C1/5C2 语义皮肤与持久化、5D 独立肤色控制通过限定验收。先核对 checkpoint 的干净包/CI，再继续瑕疵、轮廓/GPU 与完整高级功能。有实际 Key/用户样片时再经用户明确选择服务发起云端验收，不能自动产生收费请求。干净 Windows、ICC/CMYK/HDR/显示器等剩余范围按功能清单迁移。
6. 大任务前和阶段边界读 get_usage_limits，只保存去账户身份快照；剩余约 10% 时收尾/提交/检查点，不开新大任务。未知记未知。checkpoint 脚本不会自动启动 Codex。

未启用额度重置自动续跑。不会自动花费推理额度、读取 auth.json、提交凭证/原片、合并 main、发布正式版或关机。

额度规则：约剩 10% 时按总任务规则保存断点，不开启新大任务；不是总任务完成。最新构建/源码以 .development/checkpoint.json 和 build-info.json 为准，未启用自动额度重置续跑。
