2026-10-25 最新原生断点：2A RAW、2B 暖色稳定性、2C 浮点 ICC 输入通过限定验收。查看器已接 ICC/16 位 PNG，52 合成案例通过；ICC 通用路径很慢，先优化并复跑质量，再接持久参数/编辑/导出 UI。阶段 2 整体未完，先读 PROJECT_PROGRESS.md / docs/reports/native-stage2c.md / 检查点；不跳 AI、不自动关机。

> 新任务：当前开发分支为 `feat/native-slint`，正在进行 Rust + Slint 原生迁移。请优先阅读 PROJECT_PROGRESS.md 和 DEVELOPMENT_PLAN.md。下文 2.0“已完成”仅指历史 Python 工作台，不代表原生迁移完成。严禁据此直接宣布新总任务完成或关机。

# Z30 Studio 2.0：开发者 / AI 交接

更新：2026-10-25，Asia/Hong_Kong。事实以实际源码/Git/报告为准，新用户指令优先。

仓库 https://github.com/kungco/z30-studio ，分支 `fix/staged-image-pipeline`，PR https://github.com/kungco/z30-studio/pull/1 。PR 未自动合并；只克隆 main 会漏掉本轮工作。当前全清单功能已实现，最终 Windows 构建/实际 EXE 验收状态见 docs/implementation-status.md 和 docs/reports/windows-v2-validation.md。包内 build-info.json 的 source_commit 才是二进制代码基线，后续纯验收文档提交不代表二进制改动。

## 保留的约束

- 不覆盖 NEF/JPEG/PNG；上传/草稿/后台任务只管理本机副本。
- 默认 Codex CLI 命令、登录和九参数 Schema 不变；test_codex_assist.py 原有 11 项测试未修改。
- 不新增必需付费 API/SDK，不自动消耗模型额度或重试消费。Key 仅当前进程内存；不进项目/草稿/日志/预览 JSON。
- 不读取/复制 Codex auth.json，不强制结束其他应用，不改测试掩盖问题。
- 用户曾要求本轮全部工作、提交、交付完再正常关机；不能由交接文档推断未来另一个任务也被授权关机。

## 模块

photo_workflow.py 管理图库/参数同步（避免第三方 workflow 打包钩子冲突）；server.py 负责回环 API、会话与调度；imaging.py 管理 RAW 几何/输入 ICC；processing.py 为 float32 tone/OKLab；darkroom.py 是曲线/HSL/分级/WB/构图/细节；spatial.py 是蒙版/降噪/修复；lens_tools.py 与 data/ 管理 Lensfun 校准快照；colour_management.py 管理浮点空间/生成 ICC/预览打样；export_tools.py 管理输出缩放/锐化/水印/元数据；native_cache.py 是只读租约、请求 Scratch 与有界 LRU；export_jobs.py 是持久快照/逐张配方/双路进度与恢复；drafts.py 是去重原片与 SQLite 版本草稿；local_vision.py 是可选本地 ONNX 分割；api_backend.py / ai_preflight.py / codex_assist.py 共用模型校验与画质保护；desktop.py / shutdown_watchdog.py 管理 Windows 生命周期。web/ 的控制模块配合 app.js，新增脚本须列入 server 静态路由。

## 重要行为

项目 256 张/8 GiB，单图 150 MiB，会话 24h/心跳；大草稿恢复懒解码。32 蒙版/8192 点、256 修复点。项目/参数写 v2，读 v1/v2。预览内存 128 MiB，原尺寸 float 磁盘缓存 2 GiB；CLI 低层 decode()/develop() 仍有整帧内存路径，不等于 UI 的 native_pixels()。后台导出按每张快照，输出选项公共；默认一路，低内存不允许两路。ICC 打样只影响显示，模型输入固定去 EXIF 的 sRGB JPEG。

本地 SegFormer 权重不随程序/源码包分发，界面显式下载固定版本/校验 SHA 并展示原许可。源模型/许可见 docs/local-edits.md；数据库提取的 Lensfun 数据 CC BY-SA 3.0，须保留 data/ 和 licenses 声明。

## 验证与风险

最终源码回归 158 Python / 31 Node 通过；实际 Windows EXE、公开 RAW 与本地 ONNX 也完成验收，详见最终报告。实测 Z30 12/14 位公开 NEF 原片 SHA 不变；普通 RGB 输出指标保持，复杂路径有极少数数值差异，完整分布见 docs/reports/p2-pixel-comparison.json，不宣称逐位相同。

退出已确定并修复一条无人读取 stdout 管道阻塞路径：旧版 1500 次请求只返回 120 次，退出 3；新版全部返回，退出 0。另一次现场“无首条关停确认”是否完全同源未证明；保留独立 8/20 秒监督、停止观察线程及提前/最终化栈诊断，不以重跑通过当作根治。

真实 DeepSeek Key / Ollama 权重未验证；协议 mock 不等于模型效果。没有实拍肤色/灰卡/镜头网格、校准屏幕/打印品、干净虚拟机、ARM64、数百张大项目压力验收。分割不精细抠发丝，皮肤处理为人物区域+暖色的传统柔化。16 位 RGB PNG 输入尚拒绝静默降位，LUT/CMYK ICC 输入仍有 8 位边界。全部细节看 docs/reports/。

## 命令

```powershell
$env:Z30_RAW_SAMPLES='指向含 Z30-12bit.NEF 和 Z30-14bit.NEF 的目录'
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -v
node --test tests/*.test.cjs
.\.venv\Scripts\python.exe desktop.py
.\.venv\Scripts\python.exe tools/build_windows.py
```

源码 ZIP 不带环境/原片/权重/凭证。AI 交接 ZIP 另附 Git bundle 时可离线克隆；否则从指定分支克隆获取完整阶段历史。本机本轮目录 D:\codex\2026-10-23\ni-k\outputs\Z30-Studio 。
