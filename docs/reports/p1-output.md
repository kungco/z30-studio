# P1 导出与色彩管理验收

文件：新增 colour_management.py、export_tools.py、web/output-controls.js、tests/test_output.py；修改 imaging.py、processing.py、darkroom.py、quality.py、server.py、export_jobs.py、web/app.js/index.html/assistant.css、tests/test_http.py。

验证：148 Python（含真实 12/14 位 RAW）与 30 Node。生成 ICC 与独立 LittleCMS 转换对照；超 sRGB 的 Adobe RGB 输入保留负工作值；强色彩/分级输出有限且有界。TIFF 精度、最终尺寸锐化/水印、GPS 清除/方向归一化、软打样不进入导出、无效 ICC、冻结逐张配方/命名回归通过。发现并修复新 JS 未列入静态路由的问题，加入遍历 HTML 脚本的实际 HTTP 回归。

真实 14 位 Z30 NEF 导出 ProPhoto RGB 原尺寸 3712×5568 uint16 TIFF，包含正确 ICC、基础相机信息与非 8 位阶梯数据；原片哈希不变，单次约 8.157 秒（详见 p1-output-validation.json，不作为普遍性能结论）。浏览器验证旧草稿恢复、ProPhoto 预览、自建 ICC 导入、打样和两张独立参数的 1024px 带水印 TIFF 后台导出。

风险：没有校准屏幕/打印机实测；本机 UI 的打样 ICC 为生成的 RGB 测试配置，不代表印刷校样精度。LUT/CMYK 输入保留已有 8 位 sRGB 转换边界，16 位 RGB PNG 尚不支持。ProPhoto 8 位 JPEG 易出现量化，建议用 TIFF16。文本水印依赖本机字体，Windows 优先微软雅黑。性能优化阶段将进一步降低全尺寸浮点中间数组的内存占用。
