# P1 暗房第一阶段：曲线、颜色、白平衡、构图与细节

2026-10-24。在 P0 3d6f218 后独立实施；本阶段不重新发布 Windows 包，不触发关机，完整清单仍见 implementation-status.md。

## 代码与行为

- `darkroom.py`：RGB/红绿蓝独立控制点曲线，单调三次 Hermite 平滑插值；八个感知色相分区的 Hue/色度/明度；阴影/中间调/高光色调、明度和平衡，最终 OKLab 色域映射。
- 光源 Kelvin 数值（2000–15000 K）与原始线性图像上的中性灰取样、可命名灰卡增益；通过参数/草稿/便携项目保存。Kelvin 使用 Kang 2002 的黑体色度近似，相对 6500 K 校正。RAW 以 daylight 解码作为此模式起点；既有相机/自动/日光路径保持。JPEG/PNG 为已编码图像的近似校正，不声称重新获得相机传感器白平衡。
- 拉直 ±45°、无黑角内接矩形、任意归一化自由框裁剪与只显示的三分网格。旋转、拉直、自由框和固定比例共用 `server.geometry`；before/AI 参考图的几何及缓存键也包含新构图参数。
- float32 清晰度、纹理与去雾；多尺度细节通过 strip halo 处理，TIFF16 无 8 位中间步骤。去雾为近似的可控透射/对比调整，不声称重建遮挡内容。
- `processing.py` / `server.py`：预览、原尺寸检视与 JPEG/PNG/TIFF16 共享参数和显影。参数中性时与之前输出逐像素一致；现有 Codex CLI schema/调用方式仍保持九个受限全局参数，新手动控制在 AI 建议/回退时保留。
- `web/darkroom-controls.js` / `web/app.js` / HTML/CSS：曲线点拖动/数值编辑/删除、分色/分级控制、取样、拉直、自由框绘制、网格；编辑进入原有历史、自动草稿和项目。

## 验证

- 全量 124 Python、28 Node 通过（包括真实 12/14 位 NEF 的原有方法，原始 11 个 CLI 契约不变）。
- 回归覆盖：中性参数与旧路径 RGB16 完全一致、源数组不变、单调曲线及通道隔离、色相分区/中性色、Kelvin/灰卡增益、无黑角拉直、分块纹理/锐化/曲线至多 1 个 uint16 级差、16 位子 8 位步进、无效参数拒绝；before 自由框尺寸与缓存不可串用。
- 浏览器在真实 14 位 NEF 上从旧草稿恢复；新增曲线控制点、输入 55% 输出并进入历史；绘制自由框后得到 843×1145 的预览。测试时没有发送照片到模型。

## 来源与限制

曲线插值性质参考 [SciPy PCHIP 官方说明](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html)，实现不依赖 SciPy。Kelvin 公式出处及适用范围参考 [Colour 的 Kang 2002 实现说明](https://www.colour-science.org/api/0.3.1/html/_modules/colour/temperature/cct.html) 和 [Microsoft CCT 转 XYZ 文档](https://learn.microsoft.com/en-us/dotnet/api/android.graphics.colorspace.ccttoxyz)。本阶段没有添加付费依赖。

没有实拍灰卡/色卡或尼康厂商传感器标定，没有实拍肤色/打印检验；HSL 为感知色相软分区，颜色分级是手动创作功能，不是人脸识别。局部蒙版仍相对于最终构图，改变裁剪/拉直后需要检查位置。本阶段不冒充已经完成 AI 分割、污点修复、镜头配置、导出预设或广色域管理；这些仍在后续清单。
