# 导出与色彩

每张照片在“输出色彩”选择 sRGB、Adobe RGB (1998) 或 ProPhoto RGB。后两者对 RAW 使用 LibRaw ProPhoto 线性解码，再转换到可表达超出 sRGB 颜色的浮点工作值；最终在目标色域进行 OKLab 色度压缩，嵌入对应 ICC。默认 sRGB 保持原解码/数值行为。生成的矩阵/TRC ICC 不复制第三方厂商二进制配置。

常见 RGB 矩阵/TRC 输入 ICC 在广色域路径按浮点解码，保留超出 sRGB 的值。LUT、GRAY、CMYK 输入仍由既有 LittleCMS 8 位 sRGB 转换，明确提示其广色域限制；16 位彩色 PNG 仍拒绝静默降位。

显示器 ICC 可从本机导入，或使用默认的浏览器/系统色彩管理。软打样用 LittleCMS 的 proof transform，可选择 RGB/CMYK 打印 ICC、意图、纸白和超色域灰色标记。此路径只生成 8 位屏幕预览；导出从不读取/烘焙显示或打样转换。ICC 在本机 data/profiles 按内容哈希保存；项目携带引用，换电脑需另行导入。尚未对校准显示器、实际打印品或硬件色差仪进行验证。

导出统一应用输出尺寸、水印、命名和元数据策略，但每张照片使用自己的调色/裁剪/色彩空间。原尺寸、2048px 分享、1600px 网页和 TIFF16 打印预设可手动调整。尺寸保持比例，默认不放大，可设置长边或宽高边界；宽高边界优先。在线性光浮点通道缩放后，按最终尺寸锐化，再加文本水印，最后一次量化到 8/16 位。

命名支持 {name}、{index:03d}、{date}（导出日期），清除路径/Windows 保留字符并消除重名。默认清除源 EXIF；基础保留只包含相机/镜头/拍摄时间/曝光参数/作者/版权，始终不复制 GPS、机身序列号、MakerNote、缩略图。输出方向归一化。TIFF ASCII 字段不支持的字符以 ? 替换；原片始终不变。

原有 API 不传 options/recipes 时保持兼容。新队列快照深复制每张的参数和公共输出选项；关闭照片、修改参数或重启不改变已排队配方。

参考：https://www.w3.org/TR/css-color-4/#color-conversion-code 与 https://pillow.readthedocs.io/en/stable/reference/ImageCms.html 。
