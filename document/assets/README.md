# 文档图片来源

图片是经授权入库的少量说明素材，原始日志、结果表和临时页面仍留在 NAS；不把整个运行目录加入仓库。

## 流程图

- [QSim 流程 HTML](qsim_workflow.html) 与 [Operations 顺序 HTML](operations_order.html) 保存用户提供的文案和样式，HTML 可在浏览器直接打开。
- README 引用对应的 [QSim SVG](qsim_workflow.svg) 与 [Operations SVG](operations_order.svg)。QSim 总流程保持竖向；Operations 按用户追加要求改为两行蛇形（01→04、向下到05、再向左至08），保留文案和灰底圆角样式。两图分别按748px／1008px页面宽度的实际浏览器布局导出；无需 GitHub 执行内嵌 HTML/CSS。
- SVG 仅含矩形与文字，不包含脚本、外部资源或 foreignObject；可缩放查看。修改 HTML 后需同步导出 SVG。

## 真实回测输出

### 当前五年示例

- 配置：[日频](../../configs/readme_daily_5y.toml)、[30分钟](../../configs/readme_30m_5y.toml)；2021–2025年历史全A池，10分组。完整指标与验证见[五年示例](../FIVE_YEAR_EXAMPLES.md)。
- 运行ID：`readme_daily_5y_20261029_171342_503890_f9ae4c10`、`readme_30m_5y_20261029_171352_496764_b43399ef`；两者均COMPLETE，分别有1／8套StatsEvery时点策略。
- `readme_daily_5y_*.png`、`readme_30m_5y_*.png` 中的七张结果图逐字节复制自各自运行目录；没有为了改善收益调整曲线、因子方向或筛选年份。
- terminal／terminal_details分别是同一次CLI实际stdout的上／下半摘要截图，包含所有年度、ALL和四块指标。仅省略中间逐日进度行、增加标题及排版，数值和指标未改写；原始完整日志留NAS。

### 历史短样本素材

以下旧图片保留为历史记录，当前README长区间结果不再引用。原配置见[对应历史版本](https://github.com/YSZN-Ainvest/Qsim/blob/88c74979977fa9ea242e1186e000b18dae32fcd4/configs/tutorial.toml)，不等同于现在已改为10分组的快速配置。

- 配置：历史tutorial.toml，日频 StatsEvery，2024-01-02 至 01-19，6 个示例标的、30 个交易日预热。
- 实际命令：`bash run.sh configs/tutorial.toml`。
- 运行 ID：`tutorial_20261029_163103_153463_fc34a258`；完成标记 COMPLETE，14 天运行、9 天有效 IC，末尾标签按规则右删失。
- 七张结果 PNG 直接复制自该次运行，没有修改图内数据；文件名前加 tutorial_ 方便引用。
- tutorial_terminal.png 是保存的完整 CLI stdout 的浏览器排版截图，不是终端交互录像。只增加命令说明与外框，输出正文未改写；没有用生成式图像制作结果。
- 小股票池、短时间窗口仅演示功能；不表示因子具有预测能力、分组应该单调或策略可盈利。

完整运行证据路径和资源记录见[开发交接](../QSIM_DEVELOPMENT.md)本次记录。重新生成素材时，先跑同一配置，再核对 config、COMPLETE、图表和日志属于同一次运行。
