# 文档图片来源

图片是经授权入库的少量说明素材，原始日志、结果表和临时页面仍留在 NAS；不把整个运行目录加入仓库。

## 流程图

- [QSim 流程 HTML](qsim_workflow.html) 与 [Operations 顺序 HTML](operations_order.html) 保存用户提供的文案和样式，HTML 可在浏览器直接打开。
- README 引用对应的 [QSim SVG](qsim_workflow.svg) 与 [Operations SVG](operations_order.svg)。QSim 总流程保持竖向；Operations 按用户追加要求改为两行蛇形（01→04、向下到05、再向左至08），保留文案和灰底圆角样式。两图分别按748px／1008px页面宽度的实际浏览器布局导出；无需 GitHub 执行内嵌 HTML/CSS。
- SVG 仅含矩形与文字，不包含脚本、外部资源或 foreignObject；可缩放查看。修改 HTML 后需同步导出 SVG。

## 真实回测输出

### 当前长区间示例

- 配置：[日频](../../configs/readme_daily_5y.toml)、[30分钟](../../configs/readme_30m_l1_4y.toml)。日频2021–2025年固定5日反转；分钟2022–2025年负向买卖一档不平衡度，均历史全A池、10组。
- 运行ID：`readme_daily_reversal5_candidate_20261030_135734_607571_82bfa4e9`、`readme_30m_l1_4y_20261030_160015_832678_7a6fb02b`；两者均COMPLETE，分别有1／6套StatsEvery时点策略。完整统计与验证见[长区间示例](../FIVE_YEAR_EXAMPLES.md)。
- `readme_daily_5y_*.png`、`readme_30m_l1_4y_*.png`的分组／多空净值、分组收益／超额四张图于2026-10-30从上述冻结结果重绘：净值为对数轴，收益柱为对称对数轴（±1%内线性），统一按组号渐变配色并标注柱值。原运行目录图片保留；其余三张结果图仍逐字节复制自原运行目录。没有修改净值、收益、组别顺序或裁剪年份。
- 重绘脚本为`scripts/replot_readme_log.py`，新图和逐值／文件哈希核验保存于`/mnt/nas/qinml_cache/qsim/result/log_plots_20261030_182545_477585/`。
- terminal／terminal_details为同次CLI真实stdout的上／下半摘要排版截图；标题显示可复现的公开配置命令，数值保持原样。原始日志与临时HTML留NAS。
- 原组合取负后的日频实跑仍亏损，随后固定测试5日反转，保留相同日期／池／费用；当前日频+30.06%，负向分钟−86.67%（原正向−64.01%），展示实际结果。
- 旧`readme_30m_5y_*.png`及旧价量配置保留为历史材料，当前README不再引用它们。原始运行记录与本次明细见开发交接。

### 历史短样本素材

以下旧图片保留为历史记录，当前README长区间结果不再引用。原配置见[对应历史版本](https://github.com/YSZN-Ainvest/Qsim/blob/88c74979977fa9ea242e1186e000b18dae32fcd4/configs/tutorial.toml)，不等同于现在已改为10分组的快速配置。

- 配置：历史tutorial.toml，日频 StatsEvery，2024-01-02 至 01-19，6 个示例标的、30 个交易日预热。
- 实际命令：`bash run.sh configs/tutorial.toml`。
- 运行 ID：`tutorial_20261029_163103_153463_fc34a258`；完成标记 COMPLETE，14 天运行、9 天有效 IC，末尾标签按规则右删失。
- 七张结果 PNG 直接复制自该次运行，没有修改图内数据；文件名前加 tutorial_ 方便引用。
- tutorial_terminal.png 是保存的完整 CLI stdout 的浏览器排版截图，不是终端交互录像。只增加命令说明与外框，输出正文未改写；没有用生成式图像制作结果。
- 小股票池、短时间窗口仅演示功能；不表示因子具有预测能力、分组应该单调或策略可盈利。

完整运行证据路径和资源记录见[开发交接](../QSIM_DEVELOPMENT.md)本次记录。重新生成素材时，先跑同一配置，再核对 config、COMPLETE、图表和日志属于同一次运行。
