# Z30 Studio

为尼康 Z30 使用者制作的中文本地 RAW 暗房，支持 NEF 显影和 Codex 辅助调色。独立项目，与尼康官方无关联。

![Codex 辅助面板](docs/images/codex-assistant.png)

## 功能

- 使用 rawpy / LibRaw 解码 NEF，支持相机、自动和日光白平衡。
- 调整曝光、对比度、高光、阴影、色温偏移、色调、饱和度、自然饱和度和锐化。
- RGB 直方图、原片对比、自定义风格预设、旋转及居中比例裁剪。
- 导出全尺寸 JPEG / 8 位 PNG / 16 位 RGB TIFF，附带 sRGB ICC；保存和载入 JSON 调色参数。
- 让 Codex 根据预览和文字目标提出调色建议，先看试片、调整强度，再应用或恢复应用前参数。
- 检查输出通道、层次、色度及暖色偏移，必要时降低 Codex 建议强度；手动导出可自行开启画质保护。
- 后台批量导出、ZIP 下载和失败项重试；预览采用 128 MiB 上限的内存缓存。

## Windows 启动

需要 **64 位 Python 3.12 或更新版本**。下载或克隆仓库后，双击 `start.bat`。首次启动会创建本项目专用的 `.venv` 并联网安装依赖，随后自动打开浏览器。

默认地址为 `http://127.0.0.1:8730`；端口被占用时会自动选择空闲端口，请以启动窗口显示的地址为准。保持启动窗口运行，按 Ctrl+C 结束服务。

也可以手动运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

界面无需 Node.js、构建步骤或外部 CDN。手动显影在依赖安装完成后可离线使用。

## Codex 辅助调色

需要本机已安装并登录官方 Codex CLI，且账户有可用权限和额度。未登录时，请在终端运行 `codex login`。软件会从 PATH 或 Windows Codex 安装位置查找 CLI；也支持将环境变量 `Z30_CODEX_PATH` 指向其可执行文件。

导入照片后，点击 **✦ Codex 辅助**，输入目标，例如“自然通透，提亮阴影，保持真实色彩”，然后点击“发送预览并生成建议”。生成建议不会自动改变当前编辑；试片、应用和恢复均由你控制。

该功能通过 `codex exec` 的图片输入与结构化 JSON 输出接入，使用 CLI 的已保存登录状态和默认模型。单次分析不加载用户 config.toml，使用只读沙盒并禁用 shell 工具及网页搜索。程序不读取或复制 `auth.json`，网页不接收 API 密钥。

## 照片与隐私

手动显影只在本机执行，服务仅监听 `127.0.0.1`，原始 NEF 不会被覆盖。

**Codex 辅助需要联网。** 只有点击生成建议后，才会发送两张去除 EXIF / GPS、最长边不超过 1600 像素的 JPEG 预览及你的文字目标；NEF 原片不上传。画面内容仍会交给 Codex 分析，请按照片内容自行选择是否启用。试片强度调整不再次调用模型。

原片临时副本在移除照片或服务正常退出时清理；强制结束可能留下系统临时文件。编辑状态保存在当前页面会话中，关闭前请按需下载调色参数。

**后台导出另存本机快照。** 加入队列后，输入快照、参数和结果保留在项目的 `.data/exports/`，关闭照片不影响导出，重启后可手动重试未完成项。在“后台任务”中删除任务才会清除这些副本；原始照片保持不变。上限为 20 个任务、总计约 1 GiB，每批最多 8 张且输入合计不超过 512 MiB。该目录已排除在 Git 和发布源码包之外。

## 测试与边界

已用公开的 Z30 12 位、14 位无损压缩 NEF 验证解码、方向、有效画面裁切和导出；已真实调用 Codex 验证建议、试片、应用、恢复及全尺寸导出。详细过程见 [验证记录](验证记录.md)。

运行本地接口契约测试，不会调用 Codex 或消耗模型额度：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

当前只实现全局调色，不含局部蒙版、AI 降噪、镜头配置校正或自由裁剪框。色温 / 色调是相对偏移，不是精确 Kelvin 校准。RAW 外观不会完全复现尼康 Picture Control；预览与全尺寸导出的细节可能有差异。建议效果需逐张评估。

修改阶段、测试范围、实机验证限制及耗时 / 内存比较见 [分阶段修复报告](docs/reports/staged-repair.md)。

本轮修复的本地测试为 63 项，保留原有 11 项，并提供真实 12/14 位 Z30 NEF 回归。其他 RAW 方向、活跃边距及暖色保护使用合成夹具；没有实拍人像色卡、打印 CMYK 或更多方向样片验证。画质检测是预览上的启发式指标，不是审美评估、精确噪声测量或人脸检测。CI 未提供 NEF 时明确跳过两项真实 RAW 测试。

## 文档与依赖

- [完整使用说明](使用说明.md)
- [rawpy](https://github.com/letmaik/rawpy) / [LibRaw](https://www.libraw.org/)
- [NumPy](https://numpy.org/) / [Pillow](https://python-pillow.org/)
- 参考 [darktable](https://www.darktable.org/about/features/) 的无损编辑工作流与 [RawTherapee](https://rawtherapee.com/) 的高精度处理思路；界面和调色代码独立编写。
- Codex 官方文档：[非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[命令行参数](https://learn.chatgpt.com/docs/cli/reference)。

仓库不包含第三方运行库二进制；依赖由 pip 安装，各依赖适用其自身许可。
