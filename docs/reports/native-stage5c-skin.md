# 阶段 5C1：皮肤语义分割核心与原模型对照

2026-10-27。基线 `d1e20a6`，仍在 `feat/native-slint` / 草稿 PR #2；不合并 main、不发布正式版。此报告及 JSON 是提交前源码验证，最终干净构建、复测和 CI 另存 `.development/checkpoint.json`。

**本次完成的是实际 Rust 识别/蒙版/渲染核心与验证探针，不是桌面功能已交付。** 桌面继续使用 5B 几何柔化；语义蒙版的按钮、预览、会话/撤销/批量持久化和最终可选包安装待 5C2。没有改变既有配方格式，也没有把实验蒙版自动作用于用户照片。

## 源码与行为

- `native/crates/vision/src/skin.rs`：固定模型 CPU 推理；RGB/NHWC 256×256，逐轴像素中心双线性缩放，浮点输入从 0..1 映射到 −1..1。对六通道 logits 做稳定 softmax，选择 face-skin 类，高置信度平滑门控后收缩一个模型像素。不是把原始 logits 当作概率。
- `native/crates/vision/src/detector.rs`：复用既有 SHA-256 校验、受限 DLL 加载、两线程 CPU 会话和关闭遥测的运行库环境；未放宽任意模型/运行库限制。人脸和皮肤模型可以在同一进程使用。
- `native/crates/core/src/skin.rs`、`portrait.rs`、`rendering.rs`：不可变、共享内存的 256×256 蒙版；绑定原片路径摘要/大小/时间/尺寸，预览和全尺寸按相同归一坐标取样。仅量化蒙版权重，图像仍用浮点。实际柔化权重为语义区域与原有面部/眼嘴保护相乘；空蒙版不退回几何区域；错误原片绑定在修改像素前拒绝。旧入口使用 `None`，保持旧输出。
- `native/crates/vision/examples/skin_probe.rs`、`tools/native_build.ps1 -SkinProbe`：显式开发探针，输出中性图、概率、分类、蒙版和几何/语义柔化浮点数据，新目录独占创建；不触碰原片。
- `tools/prepare_native_skin.py`、`native/vendor/skin/*`：本地准备与校验独立可选组件，固定四个文件、模型摘要和完整许可/归属说明；拒绝覆盖、缺文件、多文件、同尺寸损坏及修改声明。没有下载、安装器、收费接口或 Python 应用运行时依赖。
- `tools/validate_native_skin.py`、`tests/test_native_skin_assets.py`：独立 NumPy + ONNX + 官方 TFLite 对照及组件回归；原有测试未削弱。

## 模型来源与许可

Google [官方模型卡](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Multiclass%20Segmentation.pdf) 明确列出 Apache 2.0。模型输出背景、头发、身体皮肤、脸部皮肤、衣物和其他物体；官方也说明它不能提供像素级精确选择，遮挡及不良照明会降低质量。该模型卡的评估不是本项目的验收结果。

ONNX 使用 [FreeHugsForRobots 的固定转换版本](https://huggingface.co/FreeHugsForRobots/ps-skin-seg/tree/f3a72025451688fb893083991fdfd4562814a4dd)，原图及转换归属见 `NOTICE.txt`。Z30 Studio 未修改权重图，也不执行其仓库代码。实际读取官方 TFLite 元数据，确认 mean/std 均为 127.5、激活为 SOFTMAX、类别顺序一致；审查 ONNX 不含外部权重、函数或自定义域节点。

| 资产 | 字节 | SHA-256 |
|---|---:|---|
| ONNX 转换 | 16,454,497 | `13961350bb2099688eb96182ffe26f24eb12c77b1df278a2ec95bbdc923136e1` |
| Google TFLite v1 | 16,371,837 | `c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0` |
| Apache 2.0 原许可文本 | 11,358 | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |

旧 Python SegFormer 引用的 [上游许可](https://github.com/NVlabs/SegFormer/blob/master/LICENSE) 限制研究/评估用途，因此此次没有把它作为原生皮肤功能模型，也没有捆绑它的权重。

## 验证结果

- Rust：71 通过、2 项外部样片测试默认 ignored；随后两项显式运行通过，分别覆盖公开 Z30 12/14 位全尺寸/半尺寸 RAW 和固定 YuNet 模型。新回归验证非正方形/半尺寸/全尺寸蒙版坐标、空蒙版和全白蒙版、局部排除像素不变、RGB 比例、错误来源无副作用、变化原片被拒绝、非有限输出/未知模型被拒绝。
- 原有完整 Python：179 项，177 通过、2 跳过；Node：31 通过。
- 原有桌面人像回调：18 通过；独立 YuNet 参考：5 组通过，损坏模型和 DLL 仍被拒绝；公开 RAW/批量/单张缓存导出：14 项通过。
- 语义分割：同一公开 NASA astronaut 图像的原图、横幅、竖幅、镜像、90/180/270° 实体旋转、脸部裁图、空白图共 **9 组**。比较使用 Rust 实际中性浮点图，不把原 PNG 字节误当作显影输入。对照原始 TFLite 与社区 ONNX 全部类别，再核对 Rust 脸部概率、分类和权重。
- 固定容差：最大概率误差 0.0002，均值 0.00001，分类一致率至少 99.98%，门控权重最多 1 个 8 位值。实测 Rust 对官方模型最大概率误差 **0.000010014**，门控权重与独立 ONNX 后处理逐字节一致，九组通过。没有事后放宽容差。
- 五个检出正向人脸的案例均确认：被语义蒙版完全排除的像素保持原值，输出有限且在色域内。相对于几何柔化，原图中 301 个原先会变化的排除像素被保留；这表示门控有效，不代表已经有人工作皮肤精确标注。
- 同尺寸破坏模型被原生 SHA-256 校验明确拒绝；取消后不返回结果；重复 CPU 权重一致；原始公开图片哈希不变。没有用户照片上传或云端请求。
- 已直接查看脸部裁图的模型蒙版及实际语义柔化输出：头发/衣领被模型排除，五官保护另由几何层完成。这是有限公开图检查，不是 Z30 人像主观画质验收。

详细数值及每组限制见 `native-stage5c-skin.json`。

## 耗时、内存与旧输出

同机单次观测：新模型重复 CPU 推理约 **45.9–50.6 ms**；含两模型、解码及浮点对照输出的独立探针峰值 **95.6–103.8 MiB**。输入为 150×180 到 800×512 的公开图，不能推算为全尺寸 NEF 人像性能，也不是原生窗口的驻留内存。

不开启实验语义入口时，5B→5C1 两种 RAW 的单张、重复与批量 TIFF16 共五份文件均保持原哈希：12 位 `ce4216394603464ac14395df75a2c8c9ee6a3ef9ab59a657c3e1d8a0a7eeae8f`；14 位 `8c10f8735d6cef2070e4fc5c6957d14bfd8dcb973196f83f94cea903fa0aafc5`。旧/新批量为 4.310→4.269 秒、峰值 553.129→553.277 MiB，重复单张为 1.015→0.989 秒。前后各一次观测，没有宣称速度提升。

## 复跑

显式准备固定权重，原始 TFLite 只用于开发对照；不会进应用运行时：

```powershell
python tools/prepare_native_skin.py --model ABS_MODEL.onnx --out NEW_COMPONENT
./tools/native_build.ps1 -ToolchainRoot D:/codex/toolchains/z30-native -Test -SkinProbe
python tools/validate_native_skin.py --probe native/target/release/examples/skin_probe.exe --runtime VISION_COMPONENT --model SKIN_COMPONENT --tflite ORIGINAL_MODEL.tflite --image PUBLIC_ASTRONAUT.png --out NEW_REPORT.json
```

本次验证环境：独立 Python 3.12.14，ai-edge-litert 2.3.0、onnxruntime 1.31.0、onnx 1.23.2、NumPy 2.5.4、Pillow 13.0.0；不是随包依赖。组件准备成功不等同于推理或画质通过。

## 未完成与残余风险

1. 5C2 尚需桌面动作/结果过期处理、蒙版可视化、会话版本/不可变缓存持久化、撤销与单张/批量/导出绑定一致性。不得只接按钮后宣称该功能交付；旧 5B 配方恢复效果应保持一致。
2. 实体倒置 180° 的公开图产生空皮肤门控；三种旋转图 YuNet 均未检出可用人脸。当前流程不替它猜测位置。EXIF 定向仍由既有解码器处理，实体旋转模型失败不是 EXIF 坐标测试通过的证明。
3. 多肤色、遮挡、眼镜、极端光线、多人人像和用户 Z30 样片未验收；模型只有 256×256，不能声称精细发丝/皮肤边界、无瑕疵修复或身份识别。
4. 模型图含弃用的 `tf_half_pixel_for_nn` Resize 属性。固定 ORT 1.31.0 实测通过；升级运行库时必须重新与官方原模型比较，不能仅消除警告。
5. 模型组件暂不随桌面包安装；运行库复用已有 CPU 组件及系统 VC++ x64 依赖。干净 Windows、GPU、高 DPI 未验证。本次没有新增 GUI 控件，也没有声称做过新 GUI 布局验收。
6. 来源绑定沿用路径/尺寸/大小/时间，不是完整原片内容哈希；同路径同长度且刻意保留时间戳的替换仍无法检测。瑕疵/肤色/轮廓及完整高级功能迁移也未完成。
