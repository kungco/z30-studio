# 阶段6A：Windows窗口重绘可靠性（提前修复）

2026-10-28，基线 `10c13472986b95e1ab4c4ff9e8d05d5545f4bd99`。针对恢复/遮挡后可能出现底层窗口或旧画面的风险，先修复已证实的重绘路径；阶段5人像和阶段6完整迁移均未据此标记完成。

Slint 1.18.1未将Windows请求重画的更新区域纳入软件渲染脏区域。逻辑界面未变、后备缓冲区报告可复用时，已丢失的窗口表面可能不重新提交。原依赖与锁定官方crate逐字节一致；已核对[上游修复#13568](https://github.com/slint-ui/slint/pull/13568)及[问题#13537](https://github.com/slint-ui/slint/issues/13537)，当前正式crate仍是1.18.1。

`native/vendor/slint-winit`保留官方源码/版权，仅回移 `winitwindowadapter.rs` 的33行修改：Windows绘制前读取GetUpdateRect，将物理区域按缩放转换为逻辑脏区域，沿原渲染路径提交。普通程序重画无系统更新区域时保持局部渲染；没有定时全屏重绘、图像拷贝或模型依赖。Cargo和锁文件显式绑定应用内补丁，SOURCE.json记录官方archive校验和、基础/补丁提交及逐文件哈希。顶层AboutSlint保留。

`repaint_probe.rs`只创建/操作自己的静态Slint窗口：在客户区写入已知错误像素、确认写入，再使该区域失效，验证原始色恢复。不枚举其他窗口、不捕获桌面、不发键鼠或读取照片。相同探针和版本，三个区域、100/125/150/200%合成缩放：**旧后端12例全部残留错误像素，修正后12例全部恢复**。基线模式严格要求错误像素仍在，不能把异常或未完成绘制算作复现。早期探针过早取句柄的问题已按Slint要求移入事件循环回调，日志保留。

`tools/native_gui_patch.py`在构建前验证固定源码；最终包携带补丁源码/许可，package_native.py拒绝缺失、篡改或未声明的补丁。两项新增分发回归覆盖缺源码、改源码及删除/替换声明。native_build.ps1的-RepaintProbe编译探针；Windows Actions执行12例回归。探针和Python开发脚本不随应用运行或打包。

提交前Python182项（含公开12/14位RAW、无跳过）、Node31项、Rust134项及all-targets通过。最终干净提交须显式跑2项外部fixture、414项应用流程、12项重绘探针、30组冻结数值、实际恢复窗口与同提交CI；对照11份旧RAW TIFF、耗时/内存/包大小。最终来源/ZIP/结果以本机 `.development/native-6a-final-artifact.json`和checkpoint为准，开发构建不能当最终包。

后续升级Slint需验证并移除正式版本已包含的覆盖，不改写Cargo缓存。合成缩放不等于混合DPI多屏实机；长时间休眠/驱动重置、锁屏、多屏移动仍待验证，上游注明少见winit重入缓冲路径限制。真实Z30人像自然度、自动轮廓/GPU、高级暗房及其他旧功能仍待。原片/Codex CLI登录保持，不调用付费API，不合并main、不正式发布、不自动续跑或关机。
