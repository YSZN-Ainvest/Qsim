# 文档图片来源

图片是经授权入库的少量说明素材，原始日志、结果表和临时页面仍留在 NAS；不把整个运行目录加入仓库。

- 配置：[tutorial.toml](../../configs/tutorial.toml)，日频 StatsEvery，2024-01-02 至 01-19，6 个示例标的、30 个交易日预热。
- 实际命令：`bash run.sh configs/tutorial.toml`。
- 运行 ID：`tutorial_20261029_163103_153463_fc34a258`；完成标记 COMPLETE，14 天运行、9 天有效 IC，末尾标签按规则右删失。
- 七张结果 PNG 直接复制自该次运行，没有修改图内数据；文件名前加 tutorial_ 方便引用。
- tutorial_terminal.png 是保存的完整 CLI stdout 的浏览器排版截图，不是终端交互录像。只增加命令说明与外框，输出正文未改写；没有用生成式图像制作结果。
- 小股票池、短时间窗口仅演示功能；不表示因子具有预测能力、分组应该单调或策略可盈利。

完整运行证据路径和资源记录见[开发交接](../QSIM_DEVELOPMENT.md)本次记录。重新生成素材时，先跑同一配置，再核对 config、COMPLETE、图表和日志属于同一次运行。
