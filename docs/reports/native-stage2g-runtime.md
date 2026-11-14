# 2G：RAW 随包分发与干净打包门槛

2026-10-26；Rust + Slint 原生迁移仍未完成。此阶段解决 RAW 依赖分发，不把高级暗房、AI、批量或人像功能标为完成。

## 修改与分发

- `native/crates/core/src/runtime.rs`、`desktop/src/main.rs`：默认加载 EXE 旁的 `raw/libraw.dll`，保留绝对路径开发者覆盖，不搜索当前目录/PATH。组件缺失时 JPEG/PNG 仍能工作；NEF 提示完整解压。LibRaw 原有版本/ABI/安全加载和只读边界保持。
- `tools/build_native_libraw.py`：先验证固定 SDK SHA-256，从 79 个上游编译单元构建 GNU DLL，源码没有补丁；GCC 运行库静态链接，无 OpenMP/可选外部解码器。每次使用新构建目录，保留编译日志、编译器/文件哈希、标志、源码及许可。
- `tools/package_native.py`、`tools/native_build.ps1`：验证应用和 DLL 的实际 PE 导入、源码/构建脚本/许可哈希；完整 ZIP 必须来自干净 Release。ZIP 写入临时文件，复核压缩后内容，再以不覆盖方式发布；目标竞争或源文件在打包过程中改变均拒绝。
- `desktop/build.rs`：本轮发现 Cargo 未重新运行 build.rs 时可能沿用旧的 `source_dirty=false`。增加源码监听和构建脚本显式传入的提交/脏状态；构建中提交或清洁状态改变时拒绝分发。进程测试新增两轮内嵌来源与 build-info.json 一致性检查。
- `tools/smoke_native_editing.py`：新增 `--bundled-raw`，移除 RAW 环境变量，仅保留系统 PATH，从独立中文工作目录启动；测试依赖不会进入应用包。
- `tests/test_native_package.py`：真实 PE 结构解析、缺失来源/许可、脏构建、二进制损坏、目标竞争和压缩中变更回归。合成测试 SDK 的信任锚仅在单测内替换，生产 SHA 门槛未改变。

上游 SDK：`LibRaw-0.22.2-Win64.zip`，SHA-256 `ac64fa12bb00a7581332d4c6ab918c0533fb3f119d6b668d47a6875410dca948`。本分发选择 CDDL 1.0，并保留原始 COPYRIGHT、CDDL、LGPL 文本。完整对应 SDK 和构建脚本随包放在 sources；GCC GPLv3/Runtime Library Exception 3.1、MinGW 声明在 licenses。GCC 文本取自 gcc-mirror/gcc 的 releases/gcc-16.2.0/COPYING3 和 COPYING.RUNTIME。

验证的 GNU DLL 为 1,496,576 字节，SHA-256 `da6562e67df6f05d84081d0256594faae9c2d15af7a353e861c824f2c71bcdd1`，实际导入仅 KERNEL32.dll、msvcrt.dll、WS2_32.dll。未复制 System32 中的 VC 运行库，也无需运行安装器。校验和提供完整性检查，不是发行者数字签名。

## 画质与性能

保留官方 MSVC DLL 为固定基准，不改变原 RAW/Python 测试的 SHA 或误差阈值。`tools/compare_native_libraw.py` 对公开 Z30 12/14 位各测全尺寸、半尺寸：尺寸、方向、元数据及全部 float RGB 像素一致，四组最大误差均 0；原片 SHA 前后不变。详见 `native-stage2g-raw-comparison.json`。

| 样片 | 官方 DLL 解码秒 | 随包 DLL 解码秒 | 官方/随包峰值 MiB |
|---|---:|---:|---:|
| 12 位全尺寸 | 2.270 | 1.354 | 361.6 / 361.2 |
| 12 位半尺寸 | 0.402 | 0.357 | 115.2 / 115.0 |
| 14 位全尺寸 | 2.160 | 1.392 | 361.3 / 361.2 |
| 14 位半尺寸 | 0.412 | 0.373 | 115.2 / 114.9 |

以上是一次本机观测，受后台负载/缓存/频率影响，不是统计基准或整体性能承诺。工作区随包版本 RAW14 的完整 UI TIFF16 导出约 2.57 秒，关闭活动导出约 0.82 秒；这次初次工作区测试发现的旧来源标记不作为干净构建证明。修复来源标记后的复测与最终清洁 ZIP 记录分别保存在 `.development/native-stage2g-provenance-smoke.json`、`.development/final-artifact.json` 指向的报告中。

## 测试和剩余风险

42 个 Rust 常规测试通过，默认 ignored 的真实 RAW 测试用随包 DLL 另行显式执行通过。完整 Python 发现 171 项：常规运行 169 通过、2 个实拍测试跳过；设置公开样片目录后这 2 项另行通过。31 个 Node 测试通过。新增 8 个打包测试未删改既有断言。初次编译使用严格 c++17 导致上游数学常量未定义，改为 GNU gnu++17 后构建通过，未修改上游解码源码。

1c973d 的干净构建最终布局已在真实窗口目视复核，格式框和工具栏正常；该提交的 RAW14 回调/进程测试及 GitHub 两项工作流成功。2G 的新提交 CI 需独立核对，不能沿用前一提交成功状态。

尚未验证：真正没有开发环境的 Windows 10/11 虚拟机或另一台电脑、多 DPI、用户实拍/灰卡/肤色校准、其他相机及特殊 RAW 编码。系统 PATH 测试和 PE 导入审计不能替代干净系统验收。无 OpenMP、RawSpeed/DNG SDK 等可选组件；只声明已测公开 Z30 12/14 位范围。解码器/文件系统永久阻塞仍无进程级硬超时。AI、批量、完整高级暗房/人像与 GPU 未迁移；无云推理请求、自动续作或关机。
