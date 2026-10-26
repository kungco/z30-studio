# 阶段 5A：可选本机人脸与五点定位核心

日期：2026-10-26。新增原生 `z30-vision` 开发模块和探针，目前尚未接入桌面操作。磨皮、瑕疵、肤色、轮廓与 GPU 均不属于本次已完成功能。

## 实际修改

`native/crates/vision` 使用固定 YuNet 2023mar 与 ONNX Runtime 1.31.0 CPU，`ort = 2.0.0-rc.15` 精确锁定并关闭自动下载/默认运行库链接。模型真实输入为 1×3×640×640 的 BGR float；实现保留比例、右/下补零、五点映射、置信度 0.9、IoU 0.3 去重和最多 16 张脸。坐标归一到已经定向的输入图，供后续预览/全尺寸处理共用。模型和运行库按固定大小、SHA-256 验证后加载；运行库仅从显式绝对路径读取，依赖限制到 System32。模型从验证后的内存创建，DLL 验证期间持有只读锁。

`native/vendor/vision` 保存模型/运行库版本、来源、哈希及上游许可。YuNet 模型目录声明 MIT，ONNX Runtime 声明 MIT；官方 ThirdPartyNotices 原样保留。该组件使用定位框与五个点，没有身份识别或人脸嵌入功能。

`tools/prepare_native_vision.py` 从用户明确提供的固定模型和官方 SDK ZIP，创建一个新组件目录；拒绝重用输出目录、坏哈希和许可不一致。独立 PE 审计读取普通/延迟导入，基础软件打包器原有的延迟导入拒绝规则继续生效。实际延迟导入为 `shell32.dll`；普通导入还需要本机 `MSVCP140/MSVCP140_1/VCRUNTIME140/VCRUNTIME140_1`。组件没有安装这些系统依赖，也没有通过干净 Windows 验收。

## 实测

`face_probe` 是实际 Windows 原生程序；加载模型约 80 ms，512 像素公开样片的重复 CPU 推理约 5.5–6.0 ms，进程峰值约 54–56 MiB。读数只覆盖该开发机和这组小图，不代表完整 Z30 人像工作流性能。

`tools/validate_native_faces.py` 用 NASA 的公开 astronaut 图片生成原图、宽/长画布、镜像与空白五组；前四组各检出一张脸，空白零张。NumPy 独立实现预处理/解码/去重，通过 Python ONNX Runtime 1.31.0 对照相同浮点输入。原生与参考的框、五点及置信度最大误差约 1.8e-8，预定门槛 2e-4 未调整；重复运行结果相同。同尺寸篡改的模型、运行库均被 SHA 校验拒绝，公共原始 PNG 哈希不变。此方法核对桥接/几何和数值一致性，不是独立的模型精度研究。数据见 `native-stage5a-face.json`。

初轮参考直接使用 PNG 字节，而原生输入经过既有显影链的 ICC/高光肩部处理；两边像素不一致，得到约 8.7e-4 的框/分数差异，未通过门槛。保留该失败记录，探针导出真实 float 输入后再比较；真实显影输入与 PNG 最大通道差约 .081，未通过修改模型或放宽测试掩盖。

Rust 常规 55 项通过、2 项外部 RAW/人脸素材按设计 ignored；显式公共人脸 fixture 和取消检查通过。新增素材准备 Python 3 项数据安全测试通过；完整 Python 174 项（172 通过、2 跳过），Node 31 项最近在阶段 4 全部通过。基础桌面尚未依赖 `z30-vision`，普通启动不会加载该可选 DLL。

## 复跑与剩余范围

模型和 SDK 的精确下载 URL/哈希在 `native/vendor/vision/manifest.json`；测试图片地址及 SHA 在 JSON 报告。准备组件与探针只用于开发，不要求最终用户安装 Python。

```powershell
python tools/prepare_native_vision.py --model 下载的固定模型.onnx --runtime-archive 官方固定SDK.zip --out 新组件目录的绝对路径
./tools/native_build.ps1 -Test -VisionProbe
# 显式 fixture：设置 Z30_VISION_DIR / Z30_VISION_IMAGE 后加 -VisionFixtures
python tools/validate_native_faces.py --probe native/target/release/examples/face_probe.exe --assets 组件目录 --image 固定公开图片.png --out 新报告.json
```

桌面导入/下载入口、会话绑定、皮肤区域/纹理保留修饰、污点与受限轮廓尚待后续。单一公开人像的画布/镜像变体不能替代多人、侧脸、肤色/光照、眼镜/遮挡和用户 NEF 验证；五个点不代表精细皮肤或头发分割。VC++ 随组件完整分发、真正干净 Windows、GPU 与完整模型/依赖体积仍未验收。没有收费推理请求。

来源：[YuNet 模型与许可](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet)、[OpenCV YuNet 头解码](https://github.com/opencv/opencv/blob/4.x/modules/objdetect/src/face_detect.cpp)、[ONNX Runtime 许可](https://github.com/microsoft/onnxruntime/blob/v1.31.0/LICENSE)、[ort 固定版本](https://github.com/pykeio/ort/tree/v2.0.0-rc.15)、[公开测试图片说明](https://scikit-image.org/docs/0.25.x/api/skimage.data.html#skimage.data.astronaut)。
