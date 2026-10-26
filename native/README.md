# Z30 Studio 原生开发版

这是 Rust + Slint 迁移分支，不是功能已经迁移完成的正式版本。原 Python 2.0 保留在仓库根目录，仍是 RAW/算法对照；先读 PROJECT_PROGRESS.md 和 DEVELOPMENT_PLAN.md。

当前开发版可在原生窗口中调节九个全局参数、逐张保存配方、撤销/重做、重置、查看调整前后，以及后台导出全尺寸 JPEG（质量 95）、PNG8、真正的 TIFF16。导出均带 sRGB ICC，并拒绝覆盖任何已有文件。连续拖动约 450 ms 停顿后合并为一步撤销；历史最多 50 步且仅保留在当前进程，最终参数会自动保存并在重启后恢复。

导入并预览照片后可点“一键 AI 调色”；在“AI 设置”中可选择自然/人像/胶片/产品/夜景风格、补充目标、强度和“自动应用/只给建议”。默认使用本机 Codex CLI：先运行 `codex login`，程序直接复用登录，不读取或保存认证文件。阶段 3B 可选 DeepSeek Flash 图像模型，或填入支持图片和 JSON 对象输出的 OpenAI 兼容接口 Base URL/模型名；后者也可指向本机回环模型。远端接口只接受 HTTPS，本机 `localhost/127.0.0.1/[::1]` 可用 HTTP。远端可在界面临时输入 API Key，或通过 `DEEPSEEK_API_KEY` / `Z30_AI_API_KEY` 环境变量提供。Key 只在内存和请求头中传递，不写入项目、命令行或临时文件；界面输入在启动后清空。兼容接口调用系统 `curl`，缺失时会明确报错，默认 Codex CLI 不依赖它。实际服务商可能收费，程序不会自动调用或重试。

启动任务会发送中性和当前两张最长边 1024 像素的无 EXIF/GPS sRGB JPEG 预览、九参数与调色目标到所选模型服务；程序不自动附带原片、文件名或路径。用户自己输入的目标文字也会发送，请勿在其中填写不想传出的信息。模型建议统一经过结构/范围/画质检查并限制强度，可取消、手动重试、撤销；切图和继续编辑会取消旧任务。请在“查看分析”中查看说明和回退结果。模型只控制九个全局参数，不能完成污点修复或局部 AI 修图。Codex/DeepSeek 的真实云推理、其他兼容服务和用户样片效果未验证；本机模拟 HTTP 服务器通过不等于实际本机模型通过。见 docs/reports/native-stage3a-ai-cli.md、docs/reports/native-stage3b-compatible.md。

JPEG/PNG 输入支持已有 ICC 策略及 RGB16 PNG。2G 完整软件包包含 LibRaw 0.22.2，**解压整个 ZIP 后直接运行 Z30Studio.exe，无需配置 RAW 环境变量或另装 VC++ 运行库**。请保留同目录的 raw、sources 和 licenses 文件夹。程序只从自身目录读取随包解码器，不搜索工作目录/PATH。开发者仍可用绝对路径 Z30_LIBRAW_DLL 显式覆盖。公开 Z30 12/14 位实拍样片已验证；用户自己的样片、干净 Windows、显示器 ICC/校准肤色/打印尚未验证。损坏 ICC、CMYK/YCCK、cICP/HDR 等不支持的输入仍明确拒绝。

导出捕获点击时的照片和配方，之后切图或编辑不会改写正在导出的任务。关闭窗口会先保存参数，再请求取消导出并清理临时文件；取消按处理阶段和写入边界生效，不会强杀线程。极端解码器/文件系统阻塞尚无独立进程硬超时保证。

“批量导出”会将照片库全部照片逐张导出，可选择各自的参数或统一使用当前照片参数；输出格式沿用顶部选择。选择输出目录后，任务和配方先写入本机 `batch-jobs.json`，后台顺序处理。底部可暂停、取消、重试及查看明细。暂停会等待当前照片完成；关闭程序会暂停后续任务并请求取消当前照片。重启后需要手动继续队列或重试中断项。输出名称为 `原文件名-z30-序号`，已存在的文件绝不覆盖；换目录可建立新批次。“清理已结束记录”只移除任务记录，不删除输出照片。队列最多保留 512 项。

连续导出同一照片时可复用最近一张全尺寸浮点图像，最多占用 320 MiB，空闲 30 秒或切换照片/白平衡后释放。公开 Z30 样片的重复 TIFF16 导出三轮中位数约 2.0→0.8 秒，核心峰值约 478→596 MiB；这是速度与内存的取舍。普通批量未命中缓存时不保留新的全尺寸缓存，两张公开 RAW 的本机窗口峰值约 551 MiB。样片和机器不同，耗时与内存会不同。详见 docs/reports/native-stage4c-performance.md。

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

头文件、上游许可证和下载/文件校验在 vendor/libraw/。随包 DLL 从固定 SHA-256 的官方 0.22.2 源码构建，静态链接 GCC 运行库，仅导入 Windows 系统库；对应完整 SDK 源码、构建脚本、CDDL/GCC 运行库例外及版权声明均在包内。官方 MSVC DLL 保留为对照基准。见 docs/reports/native-stage2g-runtime.md；未使用干净 Windows 虚拟机验收。

构建包含 RAW 的完整预览包（Python 仅为开发打包工具，不随应用运行）：

```powershell
python tools/build_native_libraw.py --archive LibRaw-0.22.2-Win64.zip --toolchain-bin '你的w64devkit/bin' --out .development/raw-runtime
./tools/native_build.ps1 -ToolchainRoot '你的独立工具链目录' -Test -RawRuntime .development/raw-runtime
# 提交并确保 git status 干净后重新构建，再使用返回的 stage 路径
python tools/package_native.py pack --stage native/dist/stage-实际编号 --out native/dist/Z30Studio-native.zip
```

RAW 构建目录和 ZIP 必须是新路径。打包器拒绝脏构建、缺失源码/许可、非系统 DLL 依赖、哈希不一致及覆盖现有 ZIP。解压后可执行 `python tools/package_native.py verify --stage 解压目录` 检查完整性；校验和不是数字签名。普通构建未传 `-RawRuntime` 时仍只生成开发阶段目录，不能通过完整 ZIP 打包门槛。

本机验证的是 Rust GNU + 便携 w64devkit 2.10.1：Rust 自带 CRT/链接库与便携 dlltool/汇编器配合，构建脚本仅修改子进程环境，不改全局 PATH。Windows Actions 使用 MSVC；不能在 CI 完成前宣称两条编译链均已验证。

内部构建输出为 target/release/z30-native.exe；每次复制到一个新的 dist/stage-* 目录，提供用户所需的 **Z30Studio.exe**，不会覆盖正在运行的版本。基础软件不需要 Python、浏览器、WebView 或 HTTP 服务。Python 测试/比对脚本是开发工具，不随原生程序运行。

## 数据与限制

程序按路径只读引用原片，当前本机草稿位于 `%LOCALAPPDATA%/Z30StudioNative`。工作目录独占锁防止两个新版实例同时写入；保存前检查磁盘版本，冲突或坏 JSON 明确阻止覆盖。旧开发构建没有该锁，升级时先关闭旧构建，不要跨版本同时编辑同一草稿。

`Z30_NATIVE_DATA_DIR` 仅用于显式选择独立工作目录/测试；新原生草稿不是 Python .z30proj 的替代格式。高级项目、版本、多选同步、批量 AI 调色和人物精修仍须逐项迁移，不静默抛弃旧参数。进程崩溃可能留下输出目录中的 `.z30-export-*.part`，未完成任务会标为中断；重试不会把已有最终文件当作成功或覆盖它。

`tools/smoke_native.py` 使用隔离中文目录、系统 PATH、进程标记测试启动/预览就绪、退出、重复实例拒绝和重启恢复。事件循环标记不是屏幕首帧指标；报告不能当成完整 RAW 显影性能或专业画质验收。

## 许可与大小

Slint 1.18.1 采用 Royalty-free Desktop License 2.0，并在顶层“关于”面板保留官方 AboutSlint 组件。原文在 licenses/；MinGW 静态运行库声明也保留。Rust 工具链、编译依赖与可选大模型不会作为基础软件运行依赖隐藏在外部。

目前 EXE 体积只是基础编辑阶段的数据，不能据此宣称完整产品已达到 50 MB 目标。完整功能/模型/GPU 组件、依赖和许可清单在后续阶段验收后统计。正式发布、main 合并都需要用户确认。

阶段 2B 已加入 float 九参数和带 sRGB ICC 的不覆盖 JPEG/PNG/TIFF16 核心；暖色边界数值跳变已修复，修正实现四组对照通过；旧输出差异另报，见 docs/reports/native-gamut-stability.md。2F 已接通基础编辑界面，但不是完整迁移版本。`-ExportProbe` 编译仅供开发验证的合成 TIFF 探针；不替代全尺寸 NEF 导出验证。

阶段 2C 已把 JPEG/PNG 浮点 ICC 输入接入查看器，52 个合成输入和全尺寸数值/内存验证见 docs/reports/native-stage2c.md；矩阵/TRC ICC 缓存通过质量/速度复测，通用复杂 LUT 仍可能较慢；见 docs/reports/native-icc-performance.md。`-RasterProbe` 只编译开发探针。LittleCMS 与 MozJPEG 静态链接；This software is based in part on the work of the Independent JPEG Group.

阶段 2D 的完整公开 NEF JPEG/PNG/TIFF16 文件已独立验证；JPEG 编码改用 MozJPEG，详见 docs/reports/native-stage2d-export.md。2F 已接入后台导出；`-PhotoExportProbe` 仍是独立开发验收工具。

原生草稿当前写 v2、读 v1/v2；第一次 v1→v2 保存前生成 session-v1-backup-*.json 原字节备份。此为原生草稿，仍不直接导入 Python .z30proj。2E 配方/50 步内存撤销/缓存接口已验证，现已接线到 2F 界面；见 docs/reports/native-stage2e-foundation.md 与 native-stage2f-ui.md。

`tools/smoke_native_editing.py --exe ... --out 新报告.json [--raw 样片.NEF --dll 固定库.dll]` 通过显式 QA 文件协议驱动同一组 Slint 回调，在隔离草稿和新文件名下验证调色/切图/导出/取消/退出/恢复，不启用 HTTP 服务。它不是鼠标或截图验收；2F 另用 Computer Use 实际检查过窗口、曝光/对比和文件保存对话框。

随包验收改用 `--raw 样片.NEF --bundled-raw`，明确移除 RAW 环境变量、仅保留系统 PATH，并从不同的中文工作目录启动。探针会核对进程内嵌提交/脏标记与 build-info.json，避免 Cargo 缓存造成旧构建来源标记。

阶段 5A 仅新增开发者可用的 `z30-vision` 人脸/五点定位核心和 `-VisionProbe` / `-VisionFixtures` 验证入口，桌面人像按钮和修饰功能尚未接通。模型/运行库不随当前基础包提供；可选组件还需要本机 Microsoft VC++ x64 运行库，未验证干净 Windows。基础编辑启动不加载它。见 docs/reports/native-stage5a-face.md。
