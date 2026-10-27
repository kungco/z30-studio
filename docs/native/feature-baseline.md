# Python 2.0 功能基线与原生迁移矩阵

基线 a2a2e089d760221a52c8774cca8a2e42f0a82269；本次复测 158 Python / 31 Node 通过。下表是实际源模块能力，不是原生版本完成声明。原生实现逐项填写代码/测试/限制后再关闭。

| 能力 | Python 源码/测试 | 原生状态/阶段 |
|---|---|---|
| RAW 12/14 位、八方向、有效裁剪/半尺寸几何 | imaging.py、server.py；test_imaging.py | 2A–2G 核心/原生 UI/随包 LibRaw 通过公开样片限定验收；真实 12/14 位全尺寸/半尺寸显式测试通过，用户自己的样片和干净 Windows 未验证 |
| JPEG/PNG ICC、无 ICC 策略、alpha、EXIF、输入位深 | imaging.py、colour_management.py；test_imaging.py/test_output.py | 2C 核心和 UI 已迁移，52 个合成案例与浮点 CMS 数值/性能通过；CMYK/YCCK、损坏 ICC、cICP/HDR 等仍明确拒绝 |
| float32 tone/肩部/阴影、OKLab 护色、九参数/锐化 | processing.py、quality.py；test_processing.py/test_quality_cache.py | 2B 暖色数值跳变已修正；修正 Python/Rust 四组对照 <一个 RGB16 码值，旧输出差异另报；2F 原生 UI 已接通 |
| 曲线/HSL/分级/Kelvin/吸管/裁剪/拉直/细节 | darkroom.py；test_darkroom.py | 未迁移 / 2、6 |
| 蒙版/范围/画笔、修复/克隆/降噪 | spatial.py；test_spatial.py/test_local_edits.py | 5E1/5E2a 浮点修复、来源绑定/持久化/显影/导出/取消通过；5E2b 实际鼠标选点/取样/逐点参数/移除/撤销、缩放平移与坐标和异步保护通过限定验收。自动瑕疵识别及其他空间功能仍待迁移 |
| 本机 SegFormer 主体/天空/人物与皮肤柔化 | local_vision.py；test_local_edits.py | 5A–5D 可选人脸/五点、语义皮肤、逐张柔化/均匀/提亮、撤销/恢复/导出通过限定验收；原 SegFormer 主体/天空等场景蒙版仍待迁移，不能等同原功能完成 |
| Nikon Z Lensfun 数据/焦距插值/色差估计 | lens_tools.py、data/；test_lens_tools.py | 未迁移 / 6 |
| 图库评分/旗标/关键词/比较/选择/参数分组同步 | photo_workflow.py；test_workflow.py、library-workflow.test.cjs | 原生已迁移评分、逐张选择、调整前后比较；旗标/关键词/多选/分组同步仍待阶段 6 |
| 草稿 SQLite CAS、快照/版本/项目打包 | drafts.py、projects.py；test_drafts.py/test_projects.py | 原生 JSON 独占/CAS/v1–5→v6 草稿、v1–3→v4 队列原字节备份/逐张参数及皮肤恢复已接通；完整旧项目、快照版本/打包仍待迁移 |
| 输出尺寸/水印/命名/锐化/元数据/ICC/TIFF16 | export_tools.py、colour_management.py；test_output.py | 2D–2G 全尺寸 NEF JPEG/PNG/TIFF16、sRGB ICC、原子不覆盖及后台 UI 通过；高级导出预设与广色域输出仍待迁移 |
| 后台导出/任务冻结/中断恢复/双路/进度 | export_jobs.py；test_export_jobs.py/test_lifecycle.py | 4A–4C 原生持久顺序队列、逐张/公共配方、暂停/取消/重试/重启恢复/阶段进度通过；双路并行、ETA、批量 AI 未迁移 |
| 全尺寸 float 磁盘缓存、条带/租约/LRU | native_cache.py、processing.py；test_native_cache.py | 4C 加入一张/320 MiB/30 秒浮点内存缓存，公开 RAW 重复导出字节一致与三轮速度/内存通过；磁盘/条带/租约/LRU 仍未迁移 |
| Codex CLI 登录/九参数/取消/严格结构校验 | codex_assist.py；test_codex_assist.py（原 11 项保持） | 3A 原生接入：ai_cli.rs、ai.rs、main.slint/main.rs；离线 CLI 进程/公开 Z30 12/14 位 NEF 流程通过，真实云模型未验证 |
| 可选兼容/本机模型、输入预览/费用估计/画质回退 | api_backend.py、ai_preflight.py、automation.py；test_ai_controls.py/test_automation.py | 3A 已迁移风格、目标、强度、发送内容说明、像素画质回退；3B 已接可选 DeepSeek/兼容图像接口和本机回环地址，只有模拟 HTTP 验证。精确输入预览、费用估计、真实云端/本机模型仍待后续 |
| 生命周期、看门狗、日志/诊断、构建/实际 EXE smoke | desktop.py、shutdown_watchdog.py、tools/；test_shutdown.py/test_runtime.py | 采用原生生命周期重建 / 1、6 |

新增目标功能（Python基线无实现）：5F1完成受限局部浮点形变核心及独立八组数值/合成CPU验证；形变尚未接入产品配方、桌面或实际人脸轮廓，不能算作已迁移瘦脸。下一步需绑定、持久化、源空间处理/标记坐标及完整预览导出验证。

## 对照方法

真实公开样片仍置于仓库外 `work/Z30-12bit.NEF`、`work/Z30-14bit.NEF`，不随源码/程序分发。源 SHA、历史像素分布和性能见 docs/reports/。新比较应固定原片/参数/输出尺寸/位深/LibRaw 版本/线程数，记录误差、剪切、耗时、峰值 RSS；不能只比较速度或 JPEG 文件 SHA。

Python 可以作为开发测试 oracle；原生产品不得调用 Python、启动 HTTP 或嵌入浏览器来冒充迁移。未支持的色彩/参数/项目版本必须报明限制，不能静默忽略。
