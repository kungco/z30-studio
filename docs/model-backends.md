# DeepSeek / OpenAI 兼容视觉接口

在“模型设置”中选择后端：

- **Codex CLI**：默认值，保留原有命令、只读约束、结构化输出和已登录账户；程序不读取其认证文件。
- **DeepSeek 官方视觉接口**：固定 `https://api.deepseek.com` + `deepseek-flash`。这是 2026-10-24 查阅官方文档确认支持图片的接口；不要换用未确认支持图片的纯文本模型。
- DeepSeek 预设关闭扩展思考，直接输出结构化参数；兼容模式不发送该厂商专有字段。
- **自定义兼容接口**：填写 Base URL、支持图片输入的模型 ID 和自己的 Key。程序调用 `POST /chat/completions`，使用两个 JPEG data URL、`response_format: json_object`、非流式响应及最多 2048 输出 token。

Key 在软件的密码框填写，不必发送到聊天。软件仅在当前运行的内存中保留 Key，GET 配置不会回显；保存、项目导出和日志都不包含它。地址或模型名称可保存到本机 `ai-provider.json`。更换地址不会复用旧地址的内存 Key。重启后要重新填写 Key，或自行为该进程设置 `Z30_API_KEY`；自定义地址同时设置匹配的 `Z30_API_BASE_URL`，否则不会将环境密钥发送到另一个地址。软件不自动读取其他产品的 API Key。

“测试连接”只发送认证请求读取 `GET /models`，不发送图片、不请求推理。读取成功仅证明当前认证和列表接口可用，不代表视觉与调色已经验证。有些兼容网关不提供模型列表；应核对其接口说明。

点击分析、一键 AI 或启动逐张 AI 队列，才会向所选后端发送默认显影 / 当前调整两张缩小 JPEG 和调色目标。原始 NEF/JPEG 不上传。缩小图最长边 1600，去除 EXIF/GPS，但画面内容仍会提供给模型。使用相应服务商的账户与额度。请求不自动重试、不自动切换模型、不改用另一个服务商；401、429、超时或无效 JSON 均明确失败。取消阻止本机应用迟到结果，并关闭本地连接，无法承诺服务商停止计费。

模型只返回九个已有全局参数，输出必须通过与 Codex 相同的严格字段/数值校验、本地强度约束及画质回退。白平衡模式、构图、蒙版、降噪和镜头配置仍由用户控制。每个任务冻结调用地址/模型/配置版本；过期配置不能从旧页面提交新分析。接口错误内容不回显，以免网关反射凭证。自动重定向不跟随，远程地址要求 HTTPS；HTTP 只用于本机回环接口。默认遵循系统的 HTTP CONNECT 代理设置。

## 验证范围

本地模拟服务覆盖真实 HTTP 请求的图片格式、JSON 输出、取消、迟到结果、越界拒绝、幂等请求、不重试、错误与重定向、密钥不落盘和跨地址不复用。另通过实际应用 HTTP 路由生成没有 EXIF 的图片、接收模拟建议并运行实际显影和原尺寸预览画质检查。

**尚未使用用户 DeepSeek API Key 完成云端视觉推理，因此不宣称实际 DeepSeek 调色效果已验证。** 本轮没有真实 DeepSeek 推理调用，也没有因此产生服务商推理费用。无需为测试提供 Key；可以直接在软件中填写后试用。

官方依据：[首次调用与地址](https://api-docs.deepseek.com/api/responses)、[视觉输入](https://api-docs.deepseek.com/guides/vision/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。

![DeepSeek 模型设置](images/deepseek-settings.png)
