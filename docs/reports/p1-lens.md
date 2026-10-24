# P1 镜头配置与色差

变更：lens_tools.py、data/、spatial.py、server.py、workflow.py、web/lens-controls.js、web/app.js、web/index.html、Windows 打包与第三方声明。

从 Lensfun Nikon 无反数据库提取 35 个直线投影 Nikkor Z 配置（CC BY-SA 3.0），记录源文件哈希。按明确镜头名、焦距与传感器裁切系数匹配；缺失数据不猜测。配置系数作为编辑快照保存，后续更新数据库不改变已有项目。实现 ptlens/poly3/poly5、配置 TCA 与可选 PA 暗角；自动填满同时考虑红绿蓝通道。没有暗角数据时禁用该项。使用独立公式实现，无 Lensfun GPL 程序代码。

自动 TCA 使用缩小预览上的近中性高对比边缘，要求至少三个象限一致改善与最小收益；弱证据/边界解不应用。它不能替代实镜标定，UI 明示可撤销并要求检查。

验证：141 项 Python（含原有测试与 12/14 位真实 NEF）与 30 项 Node 通过。增加精确匹配/插值、未知配置拒绝、公式与 RGB 无黑边、快照独立性、合成已知 TCA 恢复、平坦图无操作及 HTTP 回归。真实 14 位 NEF 识别 NIKKOR Z DX 16–50mm，50mm/f6.3；预览约 0.318 秒，TCA 约 1.338 秒，3712×5568 uint16 TIFF 导出约 10.261 秒，有 ICC 和非 8 位阶梯值。单次数据不代表普遍性能。原片 SHA 未改变，详细记录 p1-lens-validation.json。浏览器恢复旧草稿后正确补读 EXIF，应用配置产生可撤销历史与自动保存。

剩余风险：未进行本机 Z30/镜头实拍网格、暗角均匀场和色差靶标验证；第三方数据库精度随镜头/焦距而异。图像估计会受场景与真实色边影响；它只返回待检查的手动系数，没有宣称恢复量准确。配置暗角默认关闭。

来源：
- https://lensfun.github.io/manual/latest/group__Lens.html
- https://lensfun.github.io/calibration-tutorial/lens-distortion.html
- https://github.com/lensfun/lensfun/blob/master/data/db/mil-nikon.xml
- https://github.com/lensfun/lensfun/blob/master/data/COPYING.CC_BY-SA_3.0
