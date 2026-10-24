# Z30 Studio：其他 AI 接手说明

交接日期：2026-10-24（Asia/Hong_Kong）。这是项目事实和用户约束的交接记录；新的用户指令优先。**当前明确未完成项为退出超时的根因定位与回归验证；用户已撤销此前的暂缓要求。其他新功能应根据用户最新目标决定。**

## 1. 从哪里接手

- 仓库：https://github.com/kungco/z30-studio
- 开发分支：`fix/staged-image-pipeline`
- PR：https://github.com/kungco/z30-studio/pull/1
- PR 尚未合并到 `main`。**只克隆默认 main 会遗漏当前工作。** 接手时重新查询 PR 状态和最新分支，不能把这份日期快照当作实时状态。
- 已验收运行时代码基线：`67015ba67aaa6bf442e060037d4d408279e91d74`，版本 1.3.0。本交接文档之后的纯文档提交不代表重新构建了 Windows 二进制。
- 本机源码：`D:\codex\2026-10-23\ni-k\outputs\Z30-Studio`。
- 给其他 AI 的源码压缩包不含 `.git`；需要历史时从仓库克隆该分支，不要直接在没有历史的目录中假设提交记录存在。

```powershell
git clone --branch fix/staged-image-pipeline https://github.com/kungco/z30-studio.git
cd z30-studio
git status --short
git log -8 --oneline
```

## 2. 用户目标与必须保留的约束

这是面向 Nikon Z30 的中文本地照片暗房，用户选择了“电脑本地网页”和“NEF RAW 解码/调色”。现已提供 Windows EXE 启动器，编辑工作台仍在默认浏览器中运行。

1. 不覆盖原始 NEF/JPEG/PNG。编辑非破坏性，导出另存。
2. 保留原有 Codex CLI 登录、默认模型和受限调用方式；程序不读取或复制 `auth.json`。
3. 不新增必需的付费 API 依赖。后来用户明确选择增加 **可选 DeepSeek / OpenAI 兼容接口**，费用使用用户自己的服务商账户；不强迫启用。
4. 修改必须基于实际源码，不得只改 README；原有测试要运行，不得改测试掩盖问题。
5. 较大阶段独立 Git commit，并说明修改文件、测试结果和剩余风险。
6. 没有实际样片或真实服务验证的功能要明确标注。
7. **用户最新说明“退出问题不用暂缓”，此前的暂缓要求已撤销。退出超时仍待定位，接手 AI 可以继续排查；不能擅自声称修复，也不要强行结束用户正在运行的程序。**
8. API Key 在软件本机设置中填写，不要要求用户把真实 Key 发到聊天。不要为回归测试自行消耗模型额度。

## 3. 已完成的功能

- RAW 有效画面、方向与半尺寸预览的几何修复；JPEG/PNG ICC、无 ICC、透明合成和部分颜色标记处理。
- float32 中间数据；平滑亮度曲线、高光肩部、阴影处理、OKLab 色度/色域控制；真正 RGB16 TIFF 导出。
- 原图对比、直方图、预设、旋转及居中比例裁剪。
- 单张建议/试片/应用/撤销、一键自动调色、逐张独立 AI 批量调色；风格、强度、曝光/颜色改动上限；本地画质检查与回退。
- 每张最多 50 步历史，撤销/重做、分支和滑块合并；嵌套蒙版状态使用深拷贝。
- `.z30proj` 便携项目：包含原片副本、设置、历史、分析及 AI 撤销点，SHA256 校验，重启服务后可打开。最多 8 张、总计 512 MiB、单张 150 MiB。
- 从全尺寸显影结果读取无损区域的原尺寸检视，有界缓存和区域导航；1 图片像素对应 1 CSS 像素，受系统缩放影响。
- 渐变、径向和分段画笔蒙版；亮度/色彩传统降噪；手动畸变、径向色差、暗角和 JSON 配置。
- 后台导出、逐文件/流式 ZIP 下载、失败重试和重启恢复；缓存容量限制。
- Windows x64 文件夹式便携 EXE，原生 Tk 启动/退出窗口，内置 Python 和图像依赖，不要求安装 Python。
- 可选 DeepSeek 官方视觉预设和通用 OpenAI 兼容视觉接口，复用原有参数校验、画质回退和任务生命周期。

## 4. 代码地图

| 文件 | 职责和修改时需要注意的点 |
| --- | --- |
| `server.py` | 回环 HTTP 接口、照片会话/所有权、预览/导出、项目和原尺寸检视，绑定/服务生命周期；请求必须遵守已有 Host/Origin/自定义请求头约束 |
| `imaging.py` | RAW 有效区域/翻转/半尺寸几何；ICC、无配置输入和线性化 |
| `processing.py` | 亮度、高光、OKLab 色度/色域与分块渲染；8/16 位最终量化 |
| `spatial.py` | 光学校正、边缘感知降噪、蒙版权重与局部浮点处理；画笔 `points` + `breaks` 保持分段 |
| `quality.py` | 启发式画质指标与建议强度回退，不是人脸/审美/真实噪声模型 |
| `cache.py` | 按字节预算的 LRU；128 MiB 缓存不是整个程序内存上限 |
| `projects.py` | 便携 ZIP/JSON 格式、源文件哈希、路径/大小/历史/分析/撤销点验证 |
| `export_jobs.py` | 有容量限制的本地快照、导出队列、结果恢复、下载锁与流式 ZIP |
| `codex_assist.py` | 原 CLI 契约、结构化输出、异步任务/取消/幂等与共用结果发布；默认 CLI 行为必须保留 |
| `automation.py` | 风格目标、强度与改动上限的本地约束 |
| `api_backend.py` | 可选 HTTP 模型配置、仅内存 Key、两图请求、代理/TLS、超时/取消与严格 JSON 解析；不自动重试/重定向 |
| `web/app.js` | 照片、编辑、建议、队列、会话恢复及 UI 连接；异步结果需校验来源/版本，嵌套设置必须深拷贝 |
| `web/history.js` | 历史、深拷贝、浏览器存储压缩和只改全局参数的预设应用 |
| `web/auto-grade.js`、`web/batch-grade.js` | 单张及批量状态机；调用预算、取消、旧结果拒绝、未确认结果恢复 |
| `web/model-settings.js` | 后端选择、密码框、配置保存和不发照片的模型列表测试 |
| `desktop.py`、`app_runtime.py` | Windows 启动器、实例锁、资源/用户数据路径和外部子进程 DLL 环境 |
| `Z30-Studio.spec`、`tools/build_windows.py` | Windows 构建、许可、构建信息、ZIP 与校验值；构建使用新的 `dist/release-*`，不替换运行实例 |
| `tools/smoke_windows.py` | 实际 EXE 的独立进程验收，包含本地模拟视觉服务；默认不调用云端推理 |
| `test_codex_assist.py` | 最初 11 项 CLI 契约测试，未改动；其他回归位于 `tests/` |

`server.develop()` 的手动空间处理、几何和渲染必须与原尺寸检视、TIFF 路径一致。自动画质保护只回退 GPT/模型可改的九个全局参数，不能丢掉用户手工蒙版、镜头与降噪设置。

## 5. 启动、测试与构建

源码需要 64 位 Python 3.12+。当前本机 `.venv` 使用 Python 3.14.6，Windows 包内也是该版本。Node 用于测试，网页无需 Node、前端构建或 CDN。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe server.py --no-browser --port 8730
```

也可运行 `start.bat`。服务只监听回环地址，端口占用时自动选空闲端口。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
node --check web/app.js
node --check web/model-settings.js
node --test tests/*.test.cjs
```

已记录本地 **102 项 Python、25 项 Node 通过**，包含两个实际 RAW 方法；新增的 `tests/test_shutdown.py` 在 Windows 上启动真实无头启动器并校验关停阶段。已验收代码基线的 Windows/Linux CI 通过。CI 没有外部 NEF 时跳过两项实 RAW，Linux 另跳过 Windows 互斥锁和启动器进程退出测试。交接之后应自行复核当前分支，不要把历史结果冒充修改后的结果。

可选真实样片（不在交接包里）：本机 `D:\codex\2026-10-23\ni-k\work` 中公开 CC0 的 `Z30-12bit.NEF` 和 `Z30-14bit.NEF`。其他电脑需要自行获取 `tests/README.md` 指定的公开样片。

```powershell
$env:Z30_RAW_SAMPLES = 'D:/your-samples'
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools/build_windows.py
```

打包必须在 Windows x64 上进行。构建目录为新建的 `dist/release-*/Z30-Studio/`，ZIP 默认位于源码目录的上一级。ZIP 完整解压后运行 EXE，不能只拿 EXE 或删除 `_internal`。

```powershell
.\.venv\Scripts\python.exe tools/smoke_windows.py --zip ../Z30-Studio-Windows-x64.zip --samples D:/your-samples --out smoke.json
```

此验收运行实际 EXE，使用中文/空格路径、独立工作目录和移除 Python 的 PATH，验证 RAW/导出/项目/模拟接口及重启恢复。`--deepseek-network-check` 是显式可选项：用无效测试字符串访问官方模型列表，预期 401；不上传照片、不发起推理，不能把它当作有效认证。

## 6. 数据、模型和当前交付包

- 源码运行数据默认 `.data/`；EXE 默认 `%LOCALAPPDATA%\Z30 Studio\`。`Z30_DATA_DIR` 可显式覆盖。
- 后台快照/结果在数据目录 `exports/`；配置为 `ai-provider.json`，日志在 `logs/`。原片临时副本和持久导出快照不同，不要擅自删除正在用的数据目录。
- Codex CLI 不附带在 Windows 包里；选择该后端需本机安装登录。
- DeepSeek 预设为 `https://api.deepseek.com` + `deepseek-flash`，按 2026-10-24 官方文档支持图片；实际云端推理尚未用用户 Key 验证。
- Key 仅当前进程内存；配置、项目、日志和浏览器存储不保存 Key。可选环境变量为 `Z30_API_KEY`，自定义地址需匹配 `Z30_API_BASE_URL`。不要自动改为读取别的软件密钥。
- 只有明确点击分析才发送两张去除 EXIF/GPS 的缩小 JPEG；“测试连接”只读取模型列表。API 取消不能保证服务商不计费。
- 旧配置版本和旧页面不能向新 API 后端悄悄提交照片；修改配置必须保留这个约束。

已有 Windows ZIP：`D:\codex\2026-10-23\ni-k\outputs\Z30-Studio-Windows-x64.zip`。

- 构建源码：`67015ba67aaa6bf442e060037d4d408279e91d74`。
- ZIP SHA256：`922b17183353e3a97ca201b78616c9b0e544ffe06ec256790f3949cefb63a310`。
- EXE SHA256：`f776108af41ec8b613bdb55c7bfeb2b310dbf8aed100b8480db2862f2178d927`。
- 实际依赖/来源见包内 `build-info.json`。本文档的加入不会自动更新这个二进制，修改运行时代码后应重新构建验收。

## 7. 剩余限制与不要误报的结论

1. **退出超时：仍未定位根因。** 早期两次全 RAW 验收退出等待超 30 秒。2026-10-24 晚在同一构建上又追加 8 轮完整 EXE 验收（连同原记录共 11 轮）及 30 次空载启停，均通过；完整流程停止约 0.30–0.54 秒，空载停止约 0.43–0.53 秒。新增关停阶段耗时、10 秒重复线程转储（`logs/shutdown-stacks.log`）、smoke 每轮 `shutdowns` 秒数和无头退出回归。**仍未复现原 30 秒超时，不能宣称已修复。** 另发现后台全尺寸 RAW 导出进行中停止会等待当前单张编码约 18–20 秒（当前唯一可复现的长等待）；两次历史超时运行在停止时没有后台导出任务，故不能把这条路径认定为历史超时的根因。
2. 没有真实 DeepSeek Key 的云端视觉推理。已验证的是本地模拟完整协议/显影路径，以及官方 HTTPS 返回预期 401；不是云端调色质量验收。
3. 只有当前 Windows x64 主机验证；没有干净虚拟机、ARM64 或其他系统版本验证；EXE 未代码签名。
4. 真实 Z30 样片为公开静物，12/14 位两张均为 flip=5；其他方向/边距主要用合成夹具。没有实拍肤色/色卡验证。
5. 镜头修正为手动系数，没有自动实镜标定库；降噪是传统滤波，可能柔化纹理，不是 AI 降噪。
6. 蒙版相对最终构图，旋转/裁剪后需要检查位置；画笔分段不能被连线或历史合并破坏。
7. 原尺寸检视显示 RGB8 区域，TIFF 仍走独立的高位深最终量化路径；不能因为检视是 8 位就将整个显影降到 8 位。
8. 示例启用空间处理后，14 位 NEF 全尺寸显影约 15.9 秒 / 636 MiB，TIFF16 约 20.2 秒 / 695 MiB；是本机采样，不是性能保证，详见报告。

## 8. 优先阅读与建议接手顺序

先读 `README.md`、`docs/model-backends.md`、`docs/windows-portable.md`、`docs/reports/windows-portable-validation.md` 和 `docs/workbench.md`。几何/ICC、色彩和内存的原始对比见 `docs/reports/staged-repair.md`；测试范围见 `tests/README.md`。

接手后先确认分支、工作区和最新用户任务，再跑适当的基线测试。若用户要求验证 DeepSeek，指导其在软件里填 Key 后按明确授权使用样片，不要把模拟测试改称真实云端测试。用户已撤销退出问题的暂缓要求，可将根因定位和回归验证作为接手后的待办；若用户给出其他优先目标，以最新指令为准。
