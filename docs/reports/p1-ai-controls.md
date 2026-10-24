# P1 模型控制、离线入口与发送预览

文件：ai_preflight.py、api_backend.py、codex_assist.py、server.py、web/ai-controls.js/model-settings.js/app.js/index.html/batch-grade.js/assistant.css、tests/test_ai_controls.py、tests/batch-grade.test.cjs。

新增本机 OpenAI 兼容视觉服务预设（Ollama 回环 URL），无 API Key、不转发云环境密钥、拒绝远程主机/云模型标签；连接后检查实际 peer 为回环。服务本身是否离线须由其配置保证，软件不下载大语言模型。模型列表测试只 GET /models，不发图片或推理；返回列表可选择模型，不等于验证其视觉能力。Codex 沿用现有 CLI 当前模型/登录/命令/Schema，不引入 SDK 或必需付费 API。

“查看发送内容”在本机生成与实际调用共用的两张 sRGB JPEG、文本和 JSON。显示尺寸/字节/SHA、目的服务/模型，可下载无密钥请求 JSON；10 分钟/32 MiB 有界内存票据绑定源、页面、参数、目标、模式和后端版本。提交可复用已查看的完全相同图片；变更后拒绝旧票据。屏幕/打印模拟不发送。明确原片文件名/EXIF/GPS/路径不自动附加，但目标文字、照片可见内容会发送。批量只展示当前样本，其余逐张生成。

费用仅按用户自填单价与标注为不保证的 token 情景计算；未知单价显示未知，CLI 不杜撰单次费用，也不宣称其有 2048 输出上限。耗时以同后端成功任务历史作范围；无历史只给 180 秒推理超时，准备/结果检查可能额外耗时。不调用真实模型来测估价。失败可显式创建新请求；不确定的旧请求必须先恢复/取消，不自动重试消费。批量默认最多 8 次，用户可显式设置 1–256 上限，保留顺序分析与取消。

验证：157 Python（含 P0 退出补修与真实 12/14 位 RAW）、31 Node；11 项原有 CLI 契约测试文件未修改。新增实际本机 HTTP 模拟服务证明预览零推理、改变参数拒绝、提交与预览 payload 完全相同、本机无 Authorization、云环境密钥不外送，以及票据/单价/预算边界。修复耗时字段对旧任务结构的兼容，不改旧断言。浏览器验证真实 RAW 双图、准确目标、请求文本入口、本机预设和明确未验证提示。

剩余风险：此设备未安装 Ollama，未运行其真实视觉权重；DeepSeek 无真实 Key，云端效果仍未验证。协议模拟不等于模型调色质量。图像 token/隐藏推理/缓存折扣因服务商而异，情景范围不能作为账单。

官方资料（2026-10-25）：https://docs.ollama.com/api/openai-compatibility 与 https://api-docs.deepseek.com/guides/vision/ 。
