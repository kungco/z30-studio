# 原生阶段 2A：RAW 核心限定验收

2026-10-25。阶段 2 整体尚未完成，界面 RAW 编辑、ICC、调色和导出未开放；Python 基线保留。

## 修改与验证

- native/vendor/libraw：官方 0.22.2 头文件和原许可证、下载/内容 SHA；DLL/NEF 均在仓库外，不提交照片。raw.rs 动态加载绝对 DLL 路径，依赖仅查 DLL 目录/System32，严格版本检查；C 布局桥使用原头文件并核对两端尺寸。C 分配由同 DLL 释放，提前返回也有 RAII 清理。
- frame.rs/raw_geometry.rs：保留 RGB16 → float32 线性 sRGB，不经过 8 位；传感器相对裁剪、奇数半尺寸插值、八种方向，尺寸/缓冲不一致明确拒绝。原片只读，未加载 LibRaw writer。
- 12 项 Rust 单元回归通过，含 24 组冻结 Python/Pillow 半尺寸几何；另显式运行真实样片测试通过（12/14 位各全/半尺寸）。普通 CI 无样片时该项明确 ignored，不冒充实拍验证。
- tools/native_raw_benchmark.py 与 raw_probe.rs 实际逐像素比较两份公开 Z30 NEF；四组输出完全一致，max_abs_error=0，原始 SHA 保持不变。Python oracle 为保留的 imaging.py/rawpy 0.27.1/LibRaw 0.22.1；新 DLL 为 0.22.2。source_bits 分别确认为 12、14；全尺寸 3712×5568，半尺寸 1856×2784。
- GNU Windows 实际 Release/测试/探针编译通过；新源码 MSVC CI 要以当前提交 Actions 为准。阶段 1 的 CI 不能作为新桥接代码的 MSVC 验收。

## 初次性能基线

小报告 native-stage2a-initial.json。每项一次运行，排除浮点文件落盘，包含解码/几何/float，进程峰值工作集使用 Windows 计数器，追踪实际 Python worker 而非 venv 启动器。

| RAW | Python 秒 / MiB | 原生初版秒 / MiB | 最大像素误差 |
|---|---:|---:|---:|
| 12 位全尺寸 | 0.905 / 394.9 | 2.152 / 561.1 | 0 |
| 12 位半尺寸 | 0.397 / 145.8 | 0.407 / 174.4 | 0 |
| 14 位全尺寸 | 0.906 / 394.8 | 2.198 / 561.1 | 0 |
| 14 位半尺寸 | 0.388 / 145.7 | 0.411 / 174.4 | 0 |

这是正确性基线，初版原生性能/内存落后，不能宣称加速。Python 使用 OpenMP；新旧库构建、连续输出与 NumPy 视图策略不同，尚未完整分解原因。已从官方 mem_image.cpp 确认 make_mem_image 独立 malloc/copy，下一步提前释放解码上下文，避免其工作缓冲与 float 输出同时驻留，并添加分段计时重新量测。

## 已实施的内存优化

在独立 RGB16 bitmap 验证完成后提前 drop LibRaw 上下文，再分配 float 输出；bitmap 继续由其独立 RAII guard 持有并交回同 DLL 释放。分段计时随探针记录。优化报告 native-stage2a-memory.json，四组 max_abs_error 仍为 0，原片 SHA 未变。

| RAW | 原生优化后秒 / MiB | 相对原生初版内存 |
|---|---:|---:|
| 12 位全尺寸 | 2.172 / 361.3 | -35.6% |
| 12 位半尺寸 | 0.420 / 115.2 | -34.0% |
| 14 位全尺寸 | 2.226 / 361.3 | -35.6% |
| 14 位半尺寸 | 0.419 / 115.2 | -33.9% |

同次 Python 全尺寸约 395 MiB、半尺寸约 146 MiB，原生核心峰值已更低。单次运行仍不能当成稳定性能结论。全尺寸约 1.72 秒位于 LibRaw process，约 0.13 秒位于 Rust 几何/float；全尺寸总耗时仍慢于 Python 的约 0.9 秒。库构建和 OpenMP 差异尚需专门验证，不归咎于未量测因素。

原有 Python tests/ 150 项复跑（2 项实拍依赖另行显式运行），根目录原 11 项 Codex 测试通过，Node 31 项通过。原 Python 调色/导出/CLI 登录方式未改；新 RAW 模块没有付费 API 或云推理。

## 残余风险

仅公开样片、Camera WB、线性 sRGB 通过本阶段限定对照；用户实机 NEF、自动/日光 WB、广色域/ICC、肤色/灰卡、GPU、最终导出均未验证。所有八方向来自合成回归，真实公开样片方向为 flip=5。官方 DLL 依赖 VC++ x64 运行库；干净机器、最终 LGPL 动态库/对应源码和运行库分发尚待完成。当前基础 EXE 仍是查看器，不用部分功能体积冒充完整软件 50 MB 验收。
