# Z30 Studio Windows 便携版

1. 将 ZIP **完整解压**到任意本机文件夹。
2. 双击 `Z30-Studio.exe`。无需安装 Python、Node 或 pip 依赖，也无需管理员权限。
3. 启动窗口会自动打开默认浏览器中的本地照片工作台；也可点击“打开照片工作台”。
4. 请保留同目录的 `_internal` 文件夹。不能仅复制 EXE，也不要从 ZIP 内直接启动。
5. 退出前确认网页显示已自动保存；重要工作另存项目备份，然后关闭启动窗口，或点击“停止服务并退出”。仅关闭浏览器页面不会停止本地服务。

手动显影和导出可离线使用。默认 AI 后端使用本机已安装并登录的 Codex CLI；也可在顶部“模型设置”选择 DeepSeek 官方视觉接口或其他 OpenAI 兼容视觉接口并填写自己的 API Key。兼容接口无需安装 CLI。程序不附带 CLI，不读取或复制其登录凭证。Codex 使用现有账户额度，接口模式使用相应服务商额度。未安装 CLI 时，手工处理照常使用。

后台导出快照和运行日志位于 `%LOCALAPPDATA%\Z30 Studio\`。移动或更新程序文件夹不会覆盖它们。日志保留最多三个约 1 MiB 文件。删除程序文件夹相当于移除便携程序，不会自动删除此数据目录或你另存的照片/项目。

便携版面向 Windows x64。本次构建在当前 Windows 电脑验证；不宣称验证过其他系统版本、ARM64 或无现有 Codex 安装的干净虚拟机。照片功能与源码版一致，镜头修正仍为手动且无实镜标定，传统降噪不等于 AI 降噪。

软件未附带代码签名证书。请从项目提供的包获取，校验同名 `.sha256` 文件；无需更改系统安全设置。

## 开发和诊断

源码：https://github.com/kungco/z30-studio 。对应提交和运行库版本见包内 `build-info.json`。第三方许可文本位于 `licenses/`，说明见 `第三方声明.md`。

在 Windows x64 Python 环境安装 `requirements-build.txt` 后运行：

```powershell
python tools/build_windows.py
```

每次构建在新的 `dist/release-*/Z30-Studio/` 目录生成程序，并在上级目录生成 `Z30-Studio-2.0.0-Windows-x64.zip`；不会替换当前正在运行的程序目录。打包器不包含 `.data`、原片、项目、虚拟环境或 Codex 登录信息。

`Z30_DATA_DIR` 可显式指定独立的数据目录。诊断参数 `--headless --no-browser --port 0 --ready-file status.json --stop-file stop.signal` 可不显示窗口启动；等待状态 JSON 出现后访问其中的 URL，创建停止信号文件可正常退出。停止信号文件必须在启动前不存在。普通用户直接双击即可。

打包资源定位和子进程 DLL 搜索路径按 [PyInstaller 运行时文档](https://pyinstaller.org/en/stable/runtime-information.html)及[外部程序说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application)处理，Codex CLI 命令和登录协议不变。

API Key 只保存在当前运行内存中，重启后需重新填写；模型地址和名称保存在本机数据目录的 `ai-provider.json`，其中没有密钥。DeepSeek 预设使用 `deepseek-flash`；其他模型必须支持图片输入和 JSON 对象输出。


## 2.0 新版

完整清单见 implementation-status.md。增加曲线/HSL/分级/WB/自由构图、本地分割和修复、镜头库、图库同步、导出预设/广色域/软打样，以及原尺寸缓存和双路导出。模型设置可选本机回环视觉服务，不需要 Key；服务和视觉权重须自行安装，实际离线由其配置保证。软件未运行真实 Ollama/DeepSeek 云推理。

分割权重不随 ZIP 打包；界面显式下载约 15 MB 的固定 SegFormer 模型，完成后分割可离线运行。下载前可查看模型来源与原许可。手动编辑、导出不依赖该模型。

诊断 stop-file 模式每 4 秒保留原生线程栈，停止后每 2 秒采样，包含最终化阶段；普通桌面运行只在停止时开始采样。独立看门狗只监督自己的确切父进程，不查杀其他程序。请求日志使用有大小上限的文件，不依赖启动方读取 stdout。
