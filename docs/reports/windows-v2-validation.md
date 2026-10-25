# Windows 2.0 最终验收与交付

日期：2026-10-25（Asia/Hong_Kong）。本轮 P0 → P1 → P2 的实际源码修改、测试与剩余风险见各阶段报告；没有以 README 修改代替实现。

## 构建基线与交付

应用代码提交 `253df9cbc9cb781178199735baa7346e4995b034`，后续发布提交只整理验收报告/交接文档。最终 ZIP 的构建提交、运行库版本与 EXE SHA 记录在包内 `build-info.json`；最终产物 SHA/大小见随包 `SHA256SUMS.txt` 和 `delivery-manifest.json`，避免文档将自己的 ZIP 哈希写入自身。

Windows x64 便携包解压后双击 `Z30-Studio.exe`。源码包仅包含 Git 跟踪源码；AI 交接包另附分支 Git bundle、交接提示与验收清单。不包含 Python 环境、原片、项目、模型权重、API Key、Codex 凭证或用户数据。可选 SegFormer 权重由用户显式下载并核对许可/SHA。GitHub 交付仍保留在 `fix/staged-image-pipeline` / PR #1，不自动合并 main；公开预发布为 `v2.0.0-preview.1`。

## 测试结果

- Python 原有与新增回归合计 **158 通过**（44.369 秒），包括公开 Z30 12/14 位 NEF；Node **31 通过**。原有 `test_codex_assist.py` 的 11 项 CLI 契约测试与本轮基线比较没有修改。
- Windows 实际便携 EXE 通过增强 smoke，完整机器可读结果见 [windows-v2-smoke.json](windows-v2-smoke.json)。运行时从 PATH 移除 Python，使用中文/空格目录，验证单实例、全部页面脚本/样式、JPEG/PNG/TIFF16、35 个内置镜头配置、项目往返、持久后台导出与重启恢复。
- 两张公开 RAW 原尺寸均为 3712×5568，12/14 位原片 SHA 均未变化。高级导出覆盖 RGB/单通道曲线、HSL/颜色分级、Kelvin、拉直/自由裁剪、纹理、水印、输出锐化和真正 ProPhoto RGB16 TIFF。
- 实际打包 ONNX Runtime 在 CPU 上完成主体分割，覆盖比例 0.399353，仅表示蒙版面积，不能当成准确率。权重通过独立测试目录提供，没有放入发布包。
- 兼容接口和本机服务各进行 1 次协议 mock；发送前预览没有触发推理，提交的图像/文本/JSON 与冻结预览一致，本机路径无 Authorization。真实云推理次数为 0，Key 重启后未持久化；现有 Codex CLI 登录仅检查就绪状态。
- 最终候选包两轮普通关停分别 **0.342 / 0.549 秒，退出码均为 0**。前一候选包另两轮 0.420 / 0.548 秒。另有正常退出、无首条停止确认、无人读取 stdout 管道、最终化 atexit 卡住的真实子进程回归，测试独立看门狗与诊断，而非仅重新跑通。
- 手动操作实际 EXE 启动窗口：打开 2.0.0 窗口、浏览照片工作台、暖调胶片、自动保存、点击“停止服务并退出”、再启动恢复版本 2；曲线面板与页面 2.0.0 页脚已核对。交付截图仅使用公开测试原片。

本机源码命令：`python -m unittest discover -v`、`node --test tests/*.test.cjs`；Windows 打包/验收工具分别为 `tools/build_windows.py`、`tools/smoke_windows.py`。发布文档重打包后再次验证实际 ZIP；最终 ZIP SHA 与额外运行结果放在外部交付清单，防止递归改动。

## 分阶段修改与验证索引

| 阶段 | 主要修改文件 | 验证与风险报告 |
| --- | --- | --- |
| P0 数据/退出 | desktop.py、shutdown_watchdog.py、drafts.py、export_jobs.py、server.py、app_runtime.py、web/app.js | [可靠性](p0-reliability.md)、[关停跟进](p0-shutdown-followup.md) |
| 暗房 | darkroom.py、processing.py、imaging.py、web/darkroom-controls.js | [曲线/WB/构图/细节](p1-darkroom.md) |
| 图库/同步 | photo_workflow.py、projects.py、web/library-workflow.js | [工作流](p1-workflow.md) |
| 蒙版/修复 | spatial.py、local_vision.py、web/local-tools.js | [本地编辑](p1-local-edits.md) |
| 镜头 | lens_tools.py、data/、web/lens-controls.js | [镜头](p1-lens.md) |
| 色彩/导出 | colour_management.py、export_tools.py、export_jobs.py、web/output-controls.js | [输出](p1-output.md) |
| 性能 | native_cache.py、processing.py、server.py、tools/benchmark_pipeline.py | [性能/色彩](p2-performance.md) |
| AI 可控性 | ai_preflight.py、api_backend.py、codex_assist.py、web/app.js | [AI 控制](p1-ai-controls.md) |
| Windows 发布 | Z30-Studio.spec、tools/build_windows.py、tools/smoke_windows.py、photo_workflow.py、start.ps1 | 本报告；修复 workflow 第三方打包钩子冲突与页面旧版本显示 |

每阶段均有独立 Git commit。没有覆盖输入 NEF/JPEG/PNG，也没有读取/复制 Codex auth.json 或更换 CLI 登录方式。

## RAW、色彩、耗时与内存对照

完整同机结果与像素分布见 [性能报告](p2-performance.md) 及同目录 JSON。14 位 NEF 普通 TIFF16 峰值 RSS 从 633.8 降到 491.0 MiB；复杂蒙版/降噪/镜头路径从 698.8 降到 545.1 MiB。普通冷导出 8.09 → 8.71 秒，缓存后 3.13 秒；复杂路径 17.67 → 16.02 秒，缓存后 2.98 秒。单次同机测量不能推广到所有设备。

普通 RGB8 指标保持；复杂 RGB16 路径 99.95469% 通道值完全相同，平均绝对差 0.000509 个 uint16 单位，最大差 180/65535，小于一个 8 位级。少数差异尚未完成逐模块归因，不宣称逐位一致。

## 残余风险和未验证项

1. 已确定并修复 stdout 管道阻塞退出的可复现路径；另一次“无首条关停确认”现场是否完全同源尚未证明。当前有独立监督与提前/最终化栈诊断；不能将有限次数通过表述为永不超时。
2. 真实 DeepSeek 云推理、真实本机 Ollama 模型效果尚未验证，协议 mock 不等于模型质量。用户需自行填写 Key 或启动本地视觉模型；本轮没有新增必需付费依赖。
3. 肤色/灰卡/Kelvin、每支实际镜头网格、经校准显示器/打印品、复杂人像边缘等未完成专业实拍验收。分割为语义蒙版，皮肤柔化为人物区域与暖色选择的保守传统处理。
4. 没有干净虚拟机/ARM64/数百张拍摄项目/小内存或机械硬盘完整压力验证。LibRaw 首次去马赛克与部分输入/8 位编码仍有整帧分配；双路导出增加资源占用，ETA 只是估计。
5. 16 位 RGB PNG 输入仍明确拒绝静默降位；部分 LUT/CMYK ICC 输入受 8 位转换边界限制。广色域 RGB16 TIFF 已验证，不把该限制隐藏为已完全支持。
6. 未签名 Windows 程序的系统信誉提示、第三方可选模型许可证/下载可用性取决于本机环境；本轮没有绕过系统提示或强制结束其他应用。

正常关机仅是当前用户本次会话在全部交付完成后的明确请求，不是项目运行功能或未来接手 AI 的自动授权。
