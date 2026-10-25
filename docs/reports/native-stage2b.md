# 阶段 2B：部分实现，未通过完整画质验收

2026-10-25。RAW 调色和导出 UI 继续关闭。此检查点不是完成声明，不可跳过下面失败项直接进入 AI。

## 实际源码

- develop.rs：九参数范围/有限值/未知字段校验，float32 线性输入，平方间隔 tone LUT、黑点/高光肩部、归一化白平衡、OKLab 色度/暖色保护、十轮色域压缩、五行缓存浮点锐化。SrgbFrame 与线性 Frame 分型，不把编码 sRGB 冒充线性数据。未移植高级暗房、ICC 输入、广色域或蒙版。
- export.rs：JPEG/PNG/RGB16 TIFF，生成的通用 sRGB ICC；同目录 .part、flush/sync、persist_noclobber 发布。已有文件和 NEF 输出扩展名均拒绝，包括最后发布时的名称竞争。没有原片覆盖选项。
- 开发探针增加 --develop PARAMETERS.json；tools/native_raw_benchmark.py 同时记录 RAW 与调色耗时、实际 worker 峰值内存和误差，失败返回非零。core/assets ICC 由 LittleCMS 内建 sRGB 生成，版权字段为 No copyright, use freely；不是用户配置文件。

## 通过的范围

17 项 Rust 常规测试通过、真实 RAW fixture 项常规运行明确 ignored（阶段 2A 已显式通过）。新增八组冻结 Python 九参数输出、黑点/肩部单调/高光分离、源 float 不变、真 16 位 TIFF 和全格式 ICC/不覆盖回归。

native-stage2b-neutral.json：12/14 位全/半尺寸中性输出最大误差 1.79e-7，远低于一个 RGB16 码值；所有原片 SHA 不变。以 14 位全尺寸为例，Python RAW/调色约 1.04/2.45 秒、峰值 661 MiB；原生约 2.42/3.20 秒、479 MiB。仅一次运行，不能当成稳定速度结论。

native-stage2b-export.json：实际 Rust export_probe 输出，经独立 Python tifffile/Pillow 检查为 RGB uint16、16 bits/sample、1024 个不同红通道值、含非 257 倍数、ICC 相符，第二次输出同名被拒且 SHA 不变。该样本是 1024×1 合成梯度，**不是全尺寸 NEF 导出验收**。ICC 同时通过底层 TIFF IFD tag 和类型化 image TIFF decoder 校验；通用 ImageReader 元数据入口曾返回 None，读取限制/适配路径的差异仍应在阶段 2C 调查，未移除真实文件的 ICC 断言。

## 未通过：暖色完整样片

native-stage2b-warm-failed.json 的 success=false 必须保留。参数为曝光 .75、对比 15、高光 -35、阴影 20、色温 30、色调 10、饱和 30、自然饱和 25、锐化 35。四组样片最大误差约 .0073–.008，超过既定 2e-5；不能只因平均误差约 1.6–1.9e-7 就标为通过。

14 位半尺寸最差像素 (x=1016,y=1558) 的编码 sRGB：旧 [0.985744,0.809593,0.007902]，新 [0.985670,0.809304,0.000001]；3901 个通道超过 2e-5，坐标不在 128 行分段边界。追加诊断的最大 OKLab 距离 .003372、RMS .00000613，也不代替原验收门槛。**色域压缩边界的浮点决策是待验证假设，根因未闭环**，锐化和 halo/LUT 路径也须隔离对照。

原生暖色全尺寸调色约 10.6 秒、479 MiB，旧实现约 5.5 秒、711 MiB；性能同样未达标。不得通过放宽阈值或只改测试掩盖差异。下一步先隔离无锐化/仅 WB/仅饱和/仅自然饱和的真实像素路径，冻结最差区域和中间量，修复源码并复跑四组，再做速度优化。

## 未验证与下一阶段

用户实机样片、校准显示器/肤色灰卡、ICC 输入/无 ICC 完整策略、广色域、输出元数据/尺寸预设、全尺寸 NEF 的最终 TIFF/PNG/JPEG、GPU 和干净 Windows 分发均未验收。基础 EXE 仍是阶段 1 查看器，Core 探针通过不能当成 UI 交付。阶段 3–6 尚待完成；不调用付费推理、不改现有 CLI 登录、不合并 main、不发布正式版、不关机。
