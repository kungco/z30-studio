# Z30 Studio 原生迁移计划

2026-10-29 增量：6A 的最终干净包、实际窗口恢复和六项 CI 已完成。5F3 接入本机轮廓与四项可确认的轻微修形，新增七项 Rust、四组独立数值和45项试构建流程通过；最终包验收以 checkpoint 和 `.development/native-5f3-final-artifact.json` 为准。下表中的“自动轮廓尚未接入”为此前阶段记录；5F3 不代表自然度、GPU 或完整阶段5验收。见 docs/reports/native-stage5f3-contour.md。

更新：2026-10-28；用户本轮总任务优先于旧交接说明。开发分支 `feat/native-slint`，保留 Python 2.0 基线 `a2a2e089d760221a52c8774cca8a2e42f0a82269`。不重写已经验证的算法设计，不删除原实现，不合并 main、不发布正式版、不覆盖原片。本轮没有关机授权。

目标：Windows 10/11 x64，Rust + Slint，直接运行 Z30Studio.exe；无浏览器/WebView/Electron/Python 运行时/本地 HTTP 服务。RTX 4060 / 32 GiB 是目标硬件，不等于所有功能已经做过该硬件验证。基础分发 50 MB 是优化目标；不得隐瞒依赖或删减关键功能达标。

优先级：画质与数据安全 > 稳定性 > 功能 > 交互 > 性能 > 大小。

| 阶段 | 依赖 | 可验收结果 | 状态 |
|---|---|---|---|
| 1 基线/GUI/检查点 | 原 Python 基线 | 功能矩阵、158/31 复测、Rust/Slint 实际 Windows 构建、原生三栏界面/后台读取/生命周期；构建/测试/恢复说明 | 限定范围已验收，见 docs/reports/native-stage1.md |
| 2 RAW/画质 | 1 | LibRaw 安全边界，16 位/float 中间数据，方向/裁剪/ICC，基础九参数，JPEG/PNG/RGB16 TIFF；新旧真实 NEF 与合成方向/色彩/数值回归 | 2A RAW 核心通过；2B 暖色色域稳定性修复及修正实现四组对照通过，旧输出差异另报；2C 浮点 ICC 输入/52 案例及矩阵缓存质量/性能通过；2D 全尺寸 NEF 最终文件和 JPEG 编码优化通过；2E 配方/迁移/撤销/缓存核心通过；2F 编辑/导出 UI 及公开 RAW 文件输出通过限定验收；2G RAW 随包构建/源码许可/依赖校验与四组像素对照通过；干净 Windows 验收仍待完成 |
| 3 AI 调色 | 2 | 现有 CLI 登录/九参数契约，照片预览/模式/自然语言/强度，严格参数验证、撤销、手动重试、可选 API/本机后端；无云端强制依赖 | 3A 原生 Codex CLI 与离线端到端/公开 12、14 位 RAW 验收通过；3B 可选 DeepSeek Flash/兼容图像 HTTP 接口及本机回环流程通过模拟服务验收。真实云端、真实本机模型、用户样片与干净 Windows 验收待做，阶段 3 未完成。见 docs/reports/native-stage3a-ai-cli.md、native-stage3b-compatible.md |
| 4 批量 | 2、3 | 持久任务队列、逐张参数/公共预设，进度、暂停/取消/重试、全尺寸缓存与内存基准 | 4A–4C 顺序队列/桌面交互/公开 12、14 位 RAW 与缓存性能、内存通过限定验收；干净 Windows 及完整旧功能仍待阶段 6，见 docs/reports/native-stage4a-batch-queue.md、native-stage4b-batch-ui.md、native-stage4c-performance.md |
| 5 人像 | 2、4 | 许可兼容的可选模型；人脸/关键点/皮肤，纹理保留磨皮、瑕疵、均匀肤色与受限轮廓形变；强度/对比/撤销；CPU 与可选 GPU | 5A 核心五组参考对照通过；5B 面部定位/逐张绑定/保守柔化/撤销恢复/导出与可选组件打包实现，18 项窗口回调及实际点击通过；5C1 皮肤语义核心及官方 TFLite/ONNX/Rust 九组对照已通过；5C2 桌面/蒙版预览、v4 草稿/撤销/无模型恢复/批量导出持久化及 256/512 容量已通过限定验收，5D 独立均匀/提亮及 v5/v3 迁移、数值/撤销/导出/布局和 CPU 合成基准通过限定验收；5E1 浮点克隆/均值修复核心及十组旧版对照已完成；5E2a 绑定配方/保存/显影/导出/取消通过限定验收，5E2b 鼠标选点/取样/独立参数/移除/撤销、缩放平移和异步保护通过限定验收；5F1 受限浮点形变核心/独立数值与CPU合成基准通过；5F2a 接通原片绑定/v7草稿/v5队列/独立强度/撤销/清除/线性光预览导出，121Rust+37项新增进程流程通过，5F2b补齐实际拖动/半径/撤销/松开预览、双尺寸限幅及旧坐标保护，提交前125Rust与47项新增流程/实际操作通过；自动轮廓尚未接入；5E3已接皮肤小暗点候选/编号勾选/强度/应用、一次撤销与保存导出；134Rust/8组独立参考/39项进程流程和实际界面通过，真实祛痘自然度未验证，414项最终干净包/同提交CI以checkpoint为准；自动自然轮廓/GPU 和干净 Windows 仍待完成，见 docs/reports/native-stage5b-portrait.md、native-stage5c-skin.md、native-stage5c2-skin-workflow.md、native-stage5d-skin-tone.md、native-stage5e1-retouch.md、native-stage5e2a-repair-recipes.md、native-stage5e2b-repair-tools.md、native-stage5f1-warp-core.md、native-stage5f2a-warp-recipes.md、native-stage5f2b-manual-warp.md、native-stage5e3-blemish-review.md |
| 6 完整迁移/交付 | 前述阶段 | 原功能矩阵核对、UI/高 DPI/快捷键、稳定性/画质/性能/体积，完整打包/Actions/中文文档/风险 | 6A提前修复已证实的Windows无效区域重绘缺陷，固定回移Slint上游#13568；旧12例失败/新12例成功、源码分发保护已接入，最终验收以checkpoint为准。其余完整迁移尚未开始；不代表阶段5或阶段6整体完成 |

原有高级暗房、范围/画笔/语义蒙版、镜头数据库、图库/同步、草稿版本、广色域/软打样等能力必须逐项迁移，见 docs/native/feature-baseline.md；“有窗口”不代表整款软件迁移完成。各阶段只有在代码、构建与对应验收通过后才标记完成。

## 检查点与额度

- 大型任务开始前及长任务阶段边界调用本环境 `get_usage_limits`，实际读取；剩余约 10% 时只收尾/保存，不启动新大任务。额度不可用则明确未知，用检查点和时间分段降低损失。
- 已验证阶段独立 commit；未验证修改保留工作区并准确记录。开发者运行 `python tools/development_checkpoint.py` 保存本机检查点（不提交账户/照片/凭证）；随后更新 PROJECT_PROGRESS.md。
- 恢复顺序：PROJECT_PROGRESS.md → 本文件 → 本机 .development/checkpoint.json（存在时）→ git status/log → 上次测试/构建日志 → 未完成步骤。禁止将旧成功报告当成本次测试结果。
- 官方会话恢复与本地定时任务存在，但没有证据表明当前工具支持“精确额度重置事件唤醒并无条件续跑”。本轮不创建循环、提权执行器或自动付费任务；如果以后需要调度，先提出有截止时间/次数、预算与人工停止入口的方案。当前未配置自动续跑。
- 额度快照仅保存窗口用量/重置时间/读取时刻，不保存 accountId、Key、认证文件或原片。普通工作与暂停都写可恢复状态。

## 交付门槛

源码/锁文件、构建脚本、Windows Actions、实际 Z30Studio.exe、依赖/许可/体积清单、启动/RSS/RAW 对比、中文使用说明。无法验证的 GPU、肤色/灰卡/镜头网格/校准屏幕/打印、干净 Windows 机器、云模型等单独标明。云推理与模型下载不用于伪造验收；不自动花费推理额度。
