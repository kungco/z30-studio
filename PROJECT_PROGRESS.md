# Z30 Studio 开发检查点

更新：2026-10-26（Asia/Hong_Kong）。原生迁移总任务未完成。

- 当前阶段：1 限定验收完成；2A RAW 核心通过；2B 暖色稳定性修复通过；2C 浮点 ICC 输入已接入查看器、52 个合成案例通过，矩阵/TRC ICC 缓存优化通过，2D 真实全尺寸 NEF 最终文件/精度通过，JPEG 编码已优化；2E 配方草稿/迁移备份/撤销/缓存预览基础通过；阶段 2 整体仍未完成。2F 已接入九参数/撤销重做/对比/后台导出 GUI 并完成限定验收；2G 已实现随包 RAW 运行库与来源/依赖校验。AI UI 继续关闭。
- 分支 feat/native-slint，草稿 PR https://github.com/kungco/z30-studio/pull/2；原 Python 基线 a2a2e089d760221a52c8774cca8a2e42f0a82269。最新 SHA 以 git rev-parse HEAD 和 .development/checkpoint.json 为准；不合并 main、不正式发布。
- 已实现：原生查看器/评分/草稿/独占与 CAS，LibRaw 0.22.2 只读桥接、RGB16→float、八方向/裁剪/半尺寸，九参数 float 核心/滚动锐化，sRGB ICC JPEG/PNG/TIFF16 不覆盖输出。2F 将现成核心接到实际窗口，仍非完整迁移产品。
- RAW 纯解码四组与原 Python 逐像素相同；提前释放上下文后全尺寸峰值约 561→361 MiB，半尺寸 174→115 MiB。全尺寸解码仍慢于 Python。见 docs/reports/native-stage2a.md。
- 暖色根因：旧 float32 十轮色度搜索把两 ULP 的亮度误差放大成约 .008 蓝通道跳变。Python/Rust 同时改成精确色彩路径、24 轮及连续自适应内边界。独立三次多项式参考和微扰回归通过；未放宽误差阈值或原饱和度测试。见 docs/reports/native-gamut-stability.md。
- 修正 Rust/修正 Python 四组暖色+锐化最大误差均 <7e-6（门槛 2e-5），均小于一个 RGB16 码值，无非有限值，原片 SHA 不变。旧失败报告保留；与固定原始 Python 的差异报告 success=false，最大约 .014–.063，必须单独审阅，不能宣称旧输出一致。
- 当前验证：42 Rust 通过（含 52 输入及 7 缩放参考），真实 RAW fixture 默认 ignored，已用随包 DLL 另行显式通过；171 Python（包含显式启用的 2 个公开 RAW 测试）；31 Node 通过。1c973d 的 MSVC/原双 OS CI 均成功；2G 提交的 CI 必须另查。
- 2C：原生 ICC/PNG 元数据/Adobe EXIF/16 位 PNG、原地八方向/线性缩放已接入查看器。最终 MozJPEG 精确 DCT 的全尺寸无 ICC JPEG 两组与旧 Python 逐像素相同，约 .32 秒/302 MiB；带 ICC 优化由约 11.8 秒降至 .51 秒，逐点 CMS 质量对照通过。读 docs/reports/native-stage2c.md / native-raster-icc.json，报告 docs/reports/native-icc-performance.md；复杂 LUT 仍走通用 CMS。
- sRGB 插值优化：35 Rust/200 万点解析参考通过，四组 RAW 暖色仍 <一个 RGB16 码值；全尺寸显影约 8.4→5.6–5.8 秒，完整最终文件重测通过。见 docs/reports/native-gamma-performance.md。
- 历史性能仅单次：14 位全尺寸原生调色约 8.08 秒/479 MiB，修正 Python 13.91 秒/757 MiB；原始 Python 约 5.5 秒，不能宣称总体提速。另次原生测得约 10 秒，负载/频率变动尚未统计。
- 2D：两份 12/14 位公开 NEF 真正导出完整 JPEG/PNG/TIFF16，通过独立 Pillow/tifffile 检查；TIFF16 覆盖 65536 码值，PNG/TIFF 与渲染量化逐点一致，原片/重名保护通过。JPEG 编码约 31–34 秒降至 .70 秒，PSNR 未降；后续显影降至约 5.6–5.8 秒。见 docs/reports/native-stage2d-export.md。ICC 通用 ImageReader TIFF 元数据入口曾返回 None，类型化 decoder/独立 IFD 验证通过，后续要查适配/限制。
- 2E：原生 v2 草稿保存每照片 Recipe，读 v1/v2，迁移前备份原 JSON；50 步内存撤销/redo、单帧编辑缓存通过。真实 RAW14 缓存暖色约 .7–.8 秒/362 MiB（后续 sRGB 优化复测见 gamma 报告），实际程序 v1 恢复迁移/备份/重启通过。见 docs/reports/native-stage2e-foundation.md。这些接口现已在 2F 接入编辑 GUI。
- 2F UI：实际 EXE 的 12/14 位公开 NEF、JPEG/PNG、独立照片历史、取消/切图/恢复/导出中退出通过。Computer Use 已实际操作曝光、观察调整前后画面，并用真实保存对话框导出全尺寸带 ICC JPEG。新旧 14 位 NEF 同参数 TIFF16 逐码值相同。详见 docs/reports/native-stage2f-ui.md；不把旧截图失败覆盖成成功。
- 未完成：干净 Windows 实机/虚拟机验收，复杂 ICC 性能/CMYK/HDR/显示管理、广色域、AI、批量、高级暗房/人像、GPU、性能/体积/完整分发。用户实机/肤色灰卡/校准显示器、干净 Windows、云推理未验证。

## 2F 收尾与 2G 分发

2026-10-26：用户新请求后恢复界面验收，1c973d 干净构建最终布局已目视确认，格式框/工具栏尺寸正常。该提交的公开 RAW14 回调/进程复测通过，已同步 PR #2，两项 GitHub CI 成功。历史 Esc 中断已保留在旧阶段报告中，不作为永久阻塞。

2G 增加程序目录 RAW 自动发现、固定源码 GNU 构建、系统 DLL 依赖审计、对应源码/许可随包、原子不覆盖 ZIP 和完整性校验。12/14 位全尺寸/半尺寸与官方 DLL 四组逐像素相同，原片不变；系统 PATH、无 RAW 环境变量的工作区构建编辑/导出/退出/重启通过。发现并修复 Cargo 缓存造成进程内旧干净标记的问题，新增进程/包来源一致性检查；最终交付只使用提交后的干净构建。见 docs/reports/native-stage2g-runtime.md。最终 ZIP 路径、哈希及解压后实测记录以 .development/final-artifact.json 为准。

## 恢复环境和顺序

1. 阅读本文件、DEVELOPMENT_PLAN.md、.development/checkpoint.json，核对 Git 状态。旧 Python 完成声明不是原生完成。
2. 独立 Rust 1.99.1 GNU/w64devkit 位于 D:/codex/toolchains/z30-native；执行 tools/native_build.ps1 -ToolchainRoot D:/codex/toolchains/z30-native -Test -RawProbe -ExportProbe。不要重装或使用曾中断的版本化目录。
3. 样片为 D:/codex/2026-10-23/ni-k/work/Z30-12bit.NEF、Z30-14bit.NEF；官方 DLL 在 work/native-libraw/LibRaw-0.22.2/bin/libraw.dll。不得提交/覆盖；Python 实拍测试设置 Z30_RAW_SAMPLES=work，Rust fixture 显式设置 Z30_LIBRAW_DLL/Z30_RAW_12/Z30_RAW_14 并用 -RawFixtures。
4. 基准 tools/native_raw_benchmark.py 默认 --oracle-mode baseline 固定原始算法；修正实现对照显式用 --oracle-mode current，暖色参数见 native-stage2b.md。修正四组现场 .development/native-raw/run-b45b77eb14d04c15818b9cdc46c04aa4；旧算法差异 run-fd4235e248814befb8d294ad0ad02c13。
5. 下一步先核对 2G 最终干净 ZIP、解压后复测和对应 GitHub CI；有干净 Windows 环境时再做独立安装验收。之后进入阶段 3。2F 九参数/逐张历史/对比/后台导出已接入 GUI；运行 tools/smoke_native_editing.py 可重现真实应用回调与进程验收。2G 完整 ZIP 已设计为包含 RAW 解码器/源码/许可，普通无 -RawRuntime 的开发构建不具备该分发保证。ICC/CMYK/HDR/显示器的剩余范围须按功能清单迁移。
6. 大任务前和阶段边界读 get_usage_limits，只保存去账户身份快照；剩余约 10% 时收尾/提交/检查点，不开新大任务。未知记未知。checkpoint 脚本不会自动启动 Codex。

未启用额度重置自动续跑。不会自动花费推理额度、读取 auth.json、提交凭证/原片、合并 main、发布正式版或关机。

额度规则：约剩 10% 时按总任务规则保存断点，不开启新大任务；不是总任务完成。最新构建/源码以 .development/checkpoint.json 和 build-info.json 为准，未启用自动额度重置续跑。
