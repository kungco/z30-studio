# 阶段 3B：可选图像模型接口

日期：2026-10-26。范围：原生 Rust/Slint 开发版的可选 AI 后端；默认 Codex CLI 及原片只读约束不变。真实付费推理请求：0。

## 源码修改

- `native/crates/desktop/src/ai_http.rs`：OpenAI 兼容 Chat Completions 图像请求。DeepSeek Flash 使用官方 `https://api.deepseek.com/chat/completions` 和 `deepseek-flash`；自定义 Base URL/模型名由用户选择。远端仅允许 HTTPS，回环地址可用 HTTP；拒绝 URL 凭证、查询、片段、控制字符 Key 和无效模型名。
- `native/crates/desktop/src/main.rs`、`ui/main.slint`、`src/qa.rs`：服务商选择、临时密码输入、建议/自动应用及 QA 注入；所有后端使用同一张中性和当前预览、严格 `Advice` 校验、参数边界、像素画质回退、取消和会话版本检查。切图/调参/退出会使旧响应失效并停止子进程。界面 Key 在启动请求后清空。
- `native/crates/desktop/Cargo.toml`/`native/Cargo.lock`：只加入已有锁定依赖 `base64`、`url`；没有新增付费 SDK/API 依赖。请求通过系统 `curl` 子进程发送，取消可杀掉请求。API Key 只写入子进程 stdin，作为 `--header @-` 读取；命令参数和临时 JSON 不含 Key。临时 JSON 含缩小预览，任务完成/取消后由临时目录清理。
- `tools/smoke_native_ai_http.py`：本机 HTTP 假服务，覆盖成功、越界响应、取消、切图后旧结果、退出和公开 RAW14；逐项检查请求内容、凭证传递、原片 SHA 与临时文件清理。

DeepSeek 官方文档确认 `deepseek-flash` 接受图像，Base URL 为 `https://api.deepseek.com`，Chat Completions 图像以 `image_url` 数据块发送，JSON 输出使用 `response_format: {"type":"json_object"}`：[图像输入](https://api-docs.deepseek.com/guides/vision/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。这是请求格式依据，不等于已验证该账号/服务当前实际响应。curl 的 `--header @-` 从标准输入读取请求头，见 [curl 官方手册](https://curl.se/docs/manpage.html)。

## 验证

| 检查 | 结果 |
|---|---|
| Rust `cargo test --workspace --locked` | 45 核心 + 2 桌面通过；真实 RAW 核心 fixture 默认 ignored |
| 本机模拟 HTTP 进程/真实 Slint 回调 | 6 组、55 项通过；其中公开 Z30 14 位 NEF；0 真实云端请求 |
| 内容边界 | 两张 JPEG 均最长边 ≤1024、无 EXIF/GPS；请求体不含原片名称/Key；假 Key 仅在 HTTP Authorization 头接收 |
| 任务生命周期 | 取消、切图后编辑、退出能停止请求；无旧参数误用；原片 SHA 未变，临时请求目录清理 |
| 坏模型响应 | 越界曝光被严格解析拒绝，照片参数保持原值 |
| 与 3A 相同显影 | 同一 RAW14、同一模拟九参数，最终编辑预览 RGB 指纹均为 `aa0ee731fc2307eb`，中性预览指纹也相同 |
| 实际窗口 | 目视核对默认/DeepSeek/自定义设置、密码框、滚动区域和可见的开始/返回按钮；关闭后进程退出码 0。未在实际窗口发起云端请求 |

本机最终 Debug EXE 的模拟 HTTP 自动建议 0.699 秒、公开 RAW14 4.118 秒；RAW14 峰值工作集 397.8 MiB。这只测本机回环假服务与 Debug 配置，不能估计真实网络延迟或模型费用。3A Debug 同一 RAW14 模拟 CLI 约 3.607 秒/397.6 MiB，后台任务和模拟服务不同，不能作为性能优劣结论。两种来源共享现有显影/导出核心，没有更改 RAW/ICC/TIFF16 算法。

## 限制与下一步

- **未验证**真实 DeepSeek、其他兼容云服务、真实本机视觉模型，以及用户实拍/肤色/灰卡效果。模型必须同时支持图像输入和 JSON 对象响应；即使 HTTP 成功，不合严格十字段/范围契约的输出也不会应用。服务商可能收费，只有用户在界面启动时才会调用。
- Windows 需系统 `curl.exe`；开发机具备，干净 Windows 10/11 未独立验收。临时请求 JSON 在进程崩溃时可能残留缩小预览，包含画面内容；正常完成/取消已验证清理。应继续补启动残留清理和私密目录防护。
- 自定义回环接口留空 Key 时不会从环境变量自动取云端 Key，避免误传给本机模型；输入栏明确区分 DeepSeek 与自定义接口的环境变量。
- 未实现精确请求预览、费用估算或高级局部/语义修图。默认 Codex CLI 本身的真实云模型仍未验收；本阶段无账号 Key，不会自动测试付费服务。
