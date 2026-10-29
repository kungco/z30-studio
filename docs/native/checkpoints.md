# 额度与断点续作边界

本轮桌面工具能读取当前账户共享的 5 小时/周窗口 usedPercent 与 resetsAt。每次大任务前和阶段边界重新读取；低于约 10% 剩余时收尾、记录、停止新大任务。数据未知/陈旧时不猜百分比。

`tools/development_checkpoint.py` 原子保存 `.development/checkpoint.json`（Git 忽略）：当前分支/SHA、文件状态名称、阶段、下一步、测试日志 SHA，以及可选、15 分钟内的额度快照。不会读认证文件、复制照片或启动 Codex。坏快照不能阻止代码检查点保存；未知与过期额度有明确状态。

```powershell
python tools/development_checkpoint.py --stage stage-1 --next '完成原生构建和窗口验收' --report '测试日志的路径'
```

额度必须由当前可信 app 工具读取，再提供带 `observed_at` 的 snapshot JSON；脚本自身没有账户额度 API，不声称已实现独立后台自动监控。只保留 primary/secondary 的窗口计数和重置时间，不保留 accountId/Key。不要把猜测数据或历史快照当成当前读数。

恢复先读 PROJECT_PROGRESS.md、DEVELOPMENT_PLAN.md，然后核对 Git、检查点与日志；对未验收代码重新构建/测试，不重新设计整个架构。CLI 的 `codex resume` / `codex exec resume` 有官方会话恢复能力，但当前桌面聊天能否直接作为 CLI 会话恢复未做验证，不通过启动推理试探。

当前没有创建定时自动续跑任务。官方桌面定时任务可回到已有聊天；本地项目任务需要电脑开机和应用运行。没有查到“账户额度重置事件”作为触发器的保证。未来可靠方案应是一次性或有截止时间的官方聊天任务：重置时间后重新读取额度、仅在窗口允许且工作区可恢复时继续一个阶段；最多有限次数，完成/失败/用户停止后停用，不创建无限轮询/系统提权服务，不绕过额度或自动改用收费 API。启用前应把具体时间/次数/预算/停止条件交给用户审阅。

来源：[官方定时任务](https://learn.chatgpt.com/docs/automations?surface=app)、[官方开发命令与恢复](https://learn.chatgpt.com/docs/developer-commands?surface=cli)。这些能力与检查点持久化不同；不能把提示词写了“自动恢复”当成调度已部署。
