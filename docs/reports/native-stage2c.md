# 阶段 2C：原生 JPEG/PNG 浮点输入与 ICC 查看

2026-10-25。ICC 输入已实际接入原生查看器；RAW 调色/导出界面仍关闭，阶段 2 整体尚未完成。

## 修改文件与行为

- native/crates/core/src/raster.rs：只读、限长读取 JPEG/PNG，八种 EXIF 方向、含 IDAT 后 eXIf 的 PNG；RGB/灰度/调色板/透明、原始 16 位 PNG。ICC 优先，其次 PNG sRGB/gAMA/cHRM、EXIF Adobe RGB/R03；无声明时明确提示 sRGB 假设。空/损坏/错通道/不完整 ICC 拒绝，透明在线性光合成到白色。JPEG 预扫描限制尺寸、ICC 分段/总量和 100 次渐进扫描，截断文件拒绝。
- colour.rs：固定 LittleCMS 2.19（lcms2 6.2.0/sys 4.0.7），直接转浮点线性 sRGB，保留宽色域负值，最后显示才量化；工作空间使用与导出 ICC 相同的原色/白点并将 TRC 改为线性。ICC 中 16.16 曲线系数与理想 sRGB 公式的微小差异按实际文件解释，不人为当成完全相同。
- JPEG 使用静态 MozJPEG（mozjpeg 0.10.13/sys 2.2.3），IntegerSlow DCT 和精细色度上采样。最初 zune-jpeg 全尺寸与旧版最多相差 3 个 RGB8 码值，替代纯 Rust 解码器也未消除此差异；最终路径的两组全尺寸无 ICC JPEG（4:4:4/4:2:0）与固定旧 Python **逐像素相同**。新增 DCT/色度抽样/渐进回归，而非放宽测试。
- frame.rs：原地 EXIF 置换仅用一 bit/像素标记；线性预览缩放复用按需行缓存，不分配全尺寸横向中间帧。preview.rs 已调用这些真实接口，不再一律拒绝 ICC。离散 8/16 位 sRGB 输入用精确查表，RAW/浮点调色不进入此表。
- tools/generate_raster_reference.py、raster_probe.rs、native_raster_benchmark.py：可再生的原始合成文件、独立 W3C/ICC TRC/浮点缩放参考、固定旧 Git 基线和实际 Windows worker 内存计数。Cargo.lock 固定新增依赖；native/licenses 保留上游许可。Python 仅作开发验证工具，不是 EXE 运行依赖。

## 测试结果

23 项 Rust 通过，1 项实拍 RAW fixture 默认明确 ignored；其中语料用例覆盖 **52 个输入案例**和 5 个独立浮点缩放参考。JPEG/PNG 各八方向、sRGB/宽色域/灰度 ICC、线性 gAMA/cHRM、Adobe EXIF、透明/调色板、ICC 正反顺序分段/损坏拒绝、渐进与截断文件均覆盖。RGB/灰度 16 位 PNG 及带 ICC RGB16 均保留 1024 个不同层次。所有原有导出 ICC/不覆盖断言保留；过去的“查看器拒绝 ICC”测试改成检查实际成功解码、ICC 策略、尺寸与原片字节不变。

完整 Python 163 项（含公开 RAW 与原 11 项 Codex 测试）、Node 31 项通过。37c15dd 的 MSVC 原生及原有双 OS CI 已成功；本阶段新提交需看自己的 CI，不能沿用。52 案例外部探针与四组全尺寸合成测量也通过，报告 native-raster-icc.json。无 ICC/PNG sRGB 最大误差约 1e-7，矩阵/色度独立参考最大约 1.59e-5；精度阈值未放宽。

查看器用带 ICC 的全尺寸合成 JPEG 实际启动两次，退出均为 0，关停约 .071/.054 秒；重复实例拒绝、草稿恢复、中文/空格路径、移除开发工具/Python PATH 后运行均通过，原图 SHA 不变。这是进程与预览数据验收，不是屏幕截图/专业显示校准验收。

## 耗时与内存

同机单次 5568×3712 合成输入，峰值为真实 worker 的 Windows peak working set，排除 Python venv 重定向进程：

| 输入 | 原生读取+转换 / 峰值 | 固定旧 Python / 峰值 | 数值 |
|---|---|---|---|
| JPEG 4:4:4 无 ICC | .324 秒 / 302 MiB | .918 秒 / 1198 MiB | float 逐位相同 |
| JPEG 4:2:0 无 ICC | .312 秒 / 300 MiB | .803 秒 / 1199 MiB | float 逐位相同 |
| JPEG 带 sRGB ICC | 11.799 秒 / 302 MiB | 1.120 秒 / 1357 MiB | 最大 4.18e-6，RGB8 回编码一致 |
| RGB16 PNG | .656 秒 / 487 MiB | 原实现明确拒绝此输入 | 与独立解析梯度最大 2.69e-7 |

**ICC 路径明显过慢，未达到交互目标**。当前一般浮点 CMS 没有 8 位中间数据，代价不能隐藏。下一步对纯矩阵/TRC 输入建立可验证的离散输入缓存；LUT/复杂 ICC 保留通用 CMS。上述仅一次测量，不能宣称普遍提速。RAW 的旧速度不足也未因此解决。

## 残余风险与后续

CMYK/YCCK JPEG、PNG cICP/HDR、动画 PNG 当前明确拒绝；原 Python 的带 ICC CMYK 能力尚待迁移，不能把本小阶段称作完整色彩管理。显示输出仍为 sRGB，未使用显示器 ICC；显示器转换、广色域导出、软打样待后续移植。通用 ImageReader TIFF 元数据入口曾返回 None，底层 TIFF/类型化读取和文件 ICC 已验证，尚未定位其历史差异。

未验证用户实机样片、校准屏幕、肤色灰卡/打印、干净 Windows 分发或云推理。正式全功能依赖许可清单、RAW DLL/运行库/对应源码交付仍属后续分发门槛。新 EXE 约 15.94 MB 仍是查看器，不代表完整产品达到 50 MB。不覆盖原片、不改 CLI 登录，不新增必需付费 API，不合并 main、不正式发布、不关机。
