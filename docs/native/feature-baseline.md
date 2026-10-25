# Python 2.0 功能基线与原生迁移矩阵

基线 a2a2e089d760221a52c8774cca8a2e42f0a82269；本次复测 158 Python / 31 Node 通过。下表是实际源模块能力，不是原生版本完成声明。原生实现逐项填写代码/测试/限制后再关闭。

| 能力 | Python 源码/测试 | 原生状态/阶段 |
|---|---|---|
| RAW 12/14 位、八方向、有效裁剪/半尺寸几何 | imaging.py、server.py；test_imaging.py | 2A 核心通过：raw.rs/raw_geometry.rs，四组真实样片逐像素一致、24 组冻结半尺寸几何；UI 未启用 |
| JPEG/PNG ICC、无 ICC 策略、alpha、EXIF、输入位深 | imaging.py、colour_management.py；test_imaging.py/test_output.py | 未迁移 / 2 |
| float32 tone/肩部/阴影、OKLab 护色、九参数/锐化 | processing.py、quality.py；test_processing.py/test_quality_cache.py | 2B 部分实现；中性通过，暖色真实样片失败，UI 关闭 |
| 曲线/HSL/分级/Kelvin/吸管/裁剪/拉直/细节 | darkroom.py；test_darkroom.py | 未迁移 / 2、6 |
| 蒙版/范围/画笔、修复/克隆/降噪 | spatial.py；test_spatial.py/test_local_edits.py | 未迁移 / 5、6 |
| 本机 SegFormer 主体/天空/人物与皮肤柔化 | local_vision.py；test_local_edits.py | 未迁移 / 5 |
| Nikon Z Lensfun 数据/焦距插值/色差估计 | lens_tools.py、data/；test_lens_tools.py | 未迁移 / 6 |
| 图库评分/旗标/关键词/比较/选择/参数分组同步 | photo_workflow.py；test_workflow.py、library-workflow.test.cjs | 未迁移 / 1、4、6 |
| 草稿 SQLite CAS、快照/版本/项目打包 | drafts.py、projects.py；test_drafts.py/test_projects.py | 未迁移 / 1、4 |
| 输出尺寸/水印/命名/锐化/元数据/ICC/TIFF16 | export_tools.py、colour_management.py；test_output.py | 2B 仅基本编码/ICC/不覆盖通过合成回归；全尺寸 NEF/工作流未验收 |
| 后台导出/任务冻结/中断恢复/双路/进度 | export_jobs.py；test_export_jobs.py/test_lifecycle.py | 未迁移 / 4 |
| 全尺寸 float 磁盘缓存、条带/租约/LRU | native_cache.py、processing.py；test_native_cache.py | 未迁移 / 2、4 |
| Codex CLI 登录/九参数/取消/严格结构校验 | codex_assist.py；test_codex_assist.py（原 11 项保持） | 未迁移 / 3 |
| 可选兼容/本机模型、输入预览/费用估计/画质回退 | api_backend.py、ai_preflight.py、automation.py；test_ai_controls.py/test_automation.py | 未迁移 / 3、4 |
| 生命周期、看门狗、日志/诊断、构建/实际 EXE smoke | desktop.py、shutdown_watchdog.py、tools/；test_shutdown.py/test_runtime.py | 采用原生生命周期重建 / 1、6 |

## 对照方法

真实公开样片仍置于仓库外 `work/Z30-12bit.NEF`、`work/Z30-14bit.NEF`，不随源码/程序分发。源 SHA、历史像素分布和性能见 docs/reports/。新比较应固定原片/参数/输出尺寸/位深/LibRaw 版本/线程数，记录误差、剪切、耗时、峰值 RSS；不能只比较速度或 JPEG 文件 SHA。

Python 可以作为开发测试 oracle；原生产品不得调用 Python、启动 HTTP 或嵌入浏览器来冒充迁移。未支持的色彩/参数/项目版本必须报明限制，不能静默忽略。
