# P2 缓存、分块与后台性能

文件：native_cache.py、processing.py、spatial.py、darkroom.py、lens_tools.py、server.py、export_tools.py、export_jobs.py、web/app.js/index.html/output-controls.js、tools/benchmark_pipeline.py、tools/validate_native.py、tests/test_native_cache.py。

152 Python（含原有 12/14 位 RAW）与 30 Node 通过。回归覆盖同键多读者只构建一次、只读租约/活动帧不驱逐、预算驱逐、损坏缓存重建、未完成条带不发布、Scratch 释放/无残留、内存/磁盘路径像素一致、检视后导出不重新解码、双路进度/ETA/低内存回退。修复了 Scratch 释放后文件名复用导致 Windows 映射失败，以及 TIFF ndarray 迭代器按整页解释的问题，改为明确的压缩 bytes 条带；没有放宽原有断言。真实浏览器完成两张 RAW 双路 TIFF 导出与缓存显示。

## 同机单次、每阶段新进程

对照 9b788bd 与本阶段，固定相同参数/源 SHA。下列 peak RSS 包含阶段所需前置工作。单次测量不代表所有机器或普遍加速。完整 JSON 保留解码、预览、调色、编码/导出分项。

| 14 位 NEF 情景 | 修改前 | 修改后 | 缓存后 |
| --- | --- | --- | --- |
| 普通 TIFF16 耗时 | 8.09 s | 8.71 s | 3.13 s |
| 普通 TIFF16 峰值 RSS | 633.8 MiB | 491.0 MiB | 492.0 MiB |
| 径向蒙版+降噪+镜头修正 TIFF16 | 17.67 s | 16.02 s | 2.98 s |
| 上述复杂路径峰值 RSS | 698.8 MiB | 545.1 MiB | 545.4 MiB |
| 原尺寸检视，普通路径 | — | 5.58 s | 0.0077 s |

普通冷启动增加约 0.62 秒；缓存以磁盘空间/I/O 换取复用和较低内存，不宣称所有阶段更快。底层 decode()/develop() 仍作为内存路径保留，其直接调用并非原尺寸 UI/导出的新缓存路径。12 位 NEF 同样记录在 p2-performance-before/after.json。

## RAW/色彩对照与剩余风险

原片 SHA 不变；普通路径 RGB8 通道均值/剪切比例与前版完全相同。复杂路径新旧 uint16 实际解压像素比较：99.95469% 通道值完全相同，平均绝对差 0.000509 个 16 位单位，最大 180/65535（小于一个 8 位级）。因此没有宣称逐位相同；差异与内存布局后的浮点计算有关，既有离散色域边界搜索可能放大极少数边界差异；尚未完成每个差异像素的逐模块归因。各自 RGB16 SHA、差异完整直方图和相同原片 SHA 均保存到 p2-rgb16-*.json、p2-pixel-comparison.json。

LibRaw 去马赛克、JPEG/PNG 首次输入转换与最终 8 位编码仍有整帧步骤，不能称全链条恒定内存。两路会提高内存，ETA 只是粗估。没有对数百张真实项目、机械硬盘或小内存 Windows 设备进行完整压力/实机验证。早期退出偶发根因仍未知，但 P0 独立看门狗与中断恢复测试继续通过。
