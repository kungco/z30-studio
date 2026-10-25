# Z30 Studio 开发检查点

更新：2026-10-25（Asia/Hong_Kong）。原生迁移总任务未完成。

- 当前阶段：1 限定验收完成；2A RAW 核心通过；2B 暖色稳定性修复通过；2C 浮点 ICC 输入已接入查看器、52 个合成案例通过，矩阵/TRC ICC 缓存优化通过，2D 真实全尺寸 NEF 最终文件/精度通过，JPEG 编码已优化；2E 配方草稿/迁移备份/撤销/缓存预览基础通过；阶段 2 整体仍未完成。RAW/调色/导出/AI UI 继续关闭。
- 分支 feat/native-slint，草稿 PR https://github.com/kungco/z30-studio/pull/2；原 Python 基线 a2a2e089d760221a52c8774cca8a2e42f0a82269。最新 SHA 以 git rev-parse HEAD 和 .development/checkpoint.json 为准；不合并 main、不正式发布。
- 已实现：原生查看器/评分/草稿/独占与 CAS，LibRaw 0.22.2 只读桥接、RGB16→float、八方向/裁剪/半尺寸，九参数 float 核心/滚动锐化，sRGB ICC JPEG/PNG/TIFF16 不覆盖输出。核心探针不是完整 UI。
- RAW 纯解码四组与原 Python 逐像素相同；提前释放上下文后全尺寸峰值约 561→361 MiB，半尺寸 174→115 MiB。全尺寸解码仍慢于 Python。见 docs/reports/native-stage2a.md。
- 暖色根因：旧 float32 十轮色度搜索把两 ULP 的亮度误差放大成约 .008 蓝通道跳变。Python/Rust 同时改成精确色彩路径、24 轮及连续自适应内边界。独立三次多项式参考和微扰回归通过；未放宽误差阈值或原饱和度测试。见 docs/reports/native-gamut-stability.md。
- 修正 Rust/修正 Python 四组暖色+锐化最大误差均 <7e-6（门槛 2e-5），均小于一个 RGB16 码值，无非有限值，原片 SHA 不变。旧失败报告保留；与固定原始 Python 的差异报告 success=false，最大约 .014–.063，必须单独审阅，不能宣称旧输出一致。
- 当前验证：34 Rust 通过（含 52 输入及 7 缩放参考），1 项真实 RAW fixture 默认 ignored 且本轮已另行显式通过；163 Python 全通过，31 Node 通过。7b57ced 的 MSVC/原双 OS CI 成功，本阶段提交 CI 必须另查。
- 2C：原生 ICC/PNG 元数据/Adobe EXIF/16 位 PNG、原地八方向/线性缩放已接入查看器。最终 MozJPEG 精确 DCT 的全尺寸无 ICC JPEG 两组与旧 Python 逐像素相同，约 .32 秒/302 MiB；带 ICC 优化由约 11.8 秒降至 .51 秒，逐点 CMS 质量对照通过。读 docs/reports/native-stage2c.md / native-raster-icc.json，报告 docs/reports/native-icc-performance.md；复杂 LUT 仍走通用 CMS。
- 性能仅单次：14 位全尺寸原生调色约 8.08 秒/479 MiB，修正 Python 13.91 秒/757 MiB；原始 Python 约 5.5 秒，不能宣称总体提速。另次原生测得约 10 秒，负载/频率变动尚未统计。
- 2D：两份 12/14 位公开 NEF 真正导出完整 JPEG/PNG/TIFF16，通过独立 Pillow/tifffile 检查；TIFF16 覆盖 65536 码值，PNG/TIFF 与渲染量化逐点一致，原片/重名保护通过。JPEG 编码约 31–34 秒降至 .70 秒，PSNR 未降；显影约 8.4 秒仍待优化。见 docs/reports/native-stage2d-export.md。ICC 通用 ImageReader TIFF 元数据入口曾返回 None，类型化 decoder/独立 IFD 验证通过，后续要查适配/限制。
- 2E：原生 v2 草稿保存每照片 Recipe，读 v1/v2，迁移前备份原 JSON；50 步内存撤销/redo、单帧编辑缓存通过。真实 RAW14 缓存暖色约 .71 秒/362 MiB，实际程序 v1 恢复迁移/备份/重启通过。见 docs/reports/native-stage2e-foundation.md。接口尚未接到编辑 GUI。
- UI：用户确认早期窗口可见且可操作；桌面自动化黑屏/激活失败不视为截图点击验收通过。不要盲目关闭用户正在使用的旧窗口。
- 未完成：编辑/撤销/对比/导出 GUI 接线和生命周期验收，复杂 ICC 性能/CMYK/HDR/显示管理、广色域、AI、批量、高级暗房/人像、GPU、性能/体积/完整分发。用户实机/肤色灰卡/校准显示器、干净 Windows、云推理未验证。

## 恢复环境和顺序

1. 阅读本文件、DEVELOPMENT_PLAN.md、.development/checkpoint.json，核对 Git 状态。旧 Python 完成声明不是原生完成。
2. 独立 Rust 1.99.1 GNU/w64devkit 位于 D:/codex/toolchains/z30-native；执行 tools/native_build.ps1 -ToolchainRoot D:/codex/toolchains/z30-native -Test -RawProbe -ExportProbe。不要重装或使用曾中断的版本化目录。
3. 样片为 D:/codex/2026-10-23/ni-k/work/Z30-12bit.NEF、Z30-14bit.NEF；官方 DLL 在 work/native-libraw/LibRaw-0.22.2/bin/libraw.dll。不得提交/覆盖；Python 实拍测试设置 Z30_RAW_SAMPLES=work，Rust fixture 显式设置 Z30_LIBRAW_DLL/Z30_RAW_12/Z30_RAW_14 并用 -RawFixtures。
4. 基准 tools/native_raw_benchmark.py 默认 --oracle-mode baseline 固定原始算法；修正实现对照显式用 --oracle-mode current，暖色参数见 native-stage2b.md。修正四组现场 .development/native-raw/run-b45b77eb14d04c15818b9cdc46c04aa4；旧算法差异 run-fd4235e248814befb8d294ad0ad02c13。
5. 下一步使用现成 recipe.rs / editor.rs / EditHistory 修改 desktop/src/main.rs 和 ui/main.slint，连接九参数/撤销/重置/对比/后台导出，验收照片切换与生命周期；不要再造算法。已具备参数持久化/迁移与缓存核心，GUI 尚未接入。2C 缓存及 2D 完整 NEF 文件导出已通过限定验证。ICC core 已接入查看器，CMYK/HDR/显示器仍待迁移。尚不能进入 AI 或打开未验证的 RAW 功能。
6. 大任务前和阶段边界读 get_usage_limits，只保存去账户身份快照；剩余约 10% 时收尾/提交/检查点，不开新大任务。未知记未知。checkpoint 脚本不会自动启动 Codex。

未启用额度重置自动续跑。不会自动花费推理额度、读取 auth.json、提交凭证/原片、合并 main、发布正式版或关机。
