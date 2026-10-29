# Alpha livetest

检查对象是生产 `FactorModel.compute()` 输出的 `combined_score`，即分组前的组合因子分数。
复用行情加载、PIT 表、决策网格和标的过滤；不比较收益标签、账户收益或分组后的目标权重。
通过只说明本次覆盖范围内计算一致，不代表有预测能力或可以盈利。

## 使用

在项目目录运行。未传 `--livetest` 时保持普通回测；配置 `[livetest]` 本身不会开启检查。

```bash
./run.sh configs/livetest_demo.toml --livetest f
./run.sh configs/livetest_demo.toml --livetest p
./run.sh configs/livetest_demo.toml --livetest c
./run.sh configs/livetest_demo.toml --livetest f,p
./run.sh configs/livetest_demo.toml --livetest all
```

示例使用现有 NAS 行情的 6 个标的、14 个交易日。检查模式只运行 alpha 重放，不另跑账户回测。
每次自动生成独立报告目录；只有请求的检查全部 PASS 才退出 0，其余状态退出 1。
参数错误退出 2。`--compare-factors`、`--validate` 与 `--livetest` 不可同时使用。
检查始终保留 JSON 报告，`--no-dump` 不取消它。

```bash
./run.sh configs/livetest_demo.toml --livetest all \
  --livetest-times "2023-01-06 08:00,2023-01-13 08:00" \
  --livetest-warmup-days 10 --livetest-atol 1e-10 --livetest-rtol 1e-8
```

`--livetest-report /mnt/nas/qinml_cache/qsim/result/custom_livetest.json` 可指定新 JSON 文件，已有文件不会覆盖。
启动脚本配置 NAS 缓存并限制库线程；较大运行仍需按[交接文档](QSIM_DEVELOPMENT.md)执行资源预检、硬限制和监控。
重放串行执行，每次最多一个恢复子进程；默认最多 100 万条 alpha 记录，超过返回 UNSUPPORTED，需缩短区间／标的池／网格或显式调整预算。

v0.3 的因子计算可使用 `compute.pools`，PIT 在主线程推进后共享给数值线程；恢复进程重新创建线程池。外部输入改为按批建立共享索引，未来截断／扰动仍发生在取值和特征计算之前。代码或状态格式变化时旧 checkpoint 会拒绝恢复。QSim 已取消 OS 日期门禁，检查范围由配置起止日期决定。

## 配置

所有选项可放在 TOML 的 `[livetest]` 中；上述 CLI 同名选项优先。

| 选项 | 默认值与含义 |
|---|---|
| `times` | `[]`；f/c 的决策时间列表，上海时区。省略时按日历顺序均匀选取多个有有限 alpha、且有后续评估的时点 |
| `samples` | `3`，自动时点数，最少 2；数据不足不会降成一次检查后通过 |
| `starts` | `[]`；p 默认从评估区间前半段选 3 个交易日，显式指定至少 3 个不同交易日 |
| `warmup_days` | 省略则沿用 `data.warmup_days`；每次独立运行都必须获得完整预热交易日 |
| `atol` / `rtol` | `1e-10` / `1e-8`；适用于当前 float64 因子分数的舍入误差，非收益容差 |
| `seed` | `0`；随机扰动及每次运行的 Python／NumPy 全局 RNG 种子 |
| `max_samples` | `5`；每次比较最多保存的差异样例数 |
| `max_records` | `1000000`；单次重放的评估时点数 × 标的数上限 |
| `worker_timeout` | `300` 秒；恢复子进程超时会终止并记录 ERROR |
| `report_directory` | `/mnt/nas/qinml_cache/qsim/result/livetest` |

## 检查语义

- **f**：先完整运行作基准。对每个决策时间 t，分别从加载层删除、可复现地扰动可用时间晚于 t 的行，再建立独立实例重放。干预在行情 pivot 和因子预处理之前；比较所有截至 t 的共同评估时间及完整标的键。
- **p**：更改配置中的回测起点，各自初始化并获取预热数据，只比较最晚起点之后的全部共同评估区间。有限窗口至少需要 `lookback+1` 根已完成行情；递归模型须声明 `history_kind="recursive"` 并显式配置预热。声明依赖全部历史的模型返回 UNSUPPORTED，训练产物作为固定 PIT 输入，其生成训练过程不在覆盖范围内。
- **c**：每个检查点均从起点运行至 t 对应 bar，完成 `compute` 和 `observe` 后保存，销毁实例，再在新 Python 进程中恢复。报告区分决策时间 t 和实际 checkpoint 时间 `bar_end`；恢复从下一条事件开始，对比之后的所有 alpha，而不是只比较末值。

保存内容包括滚动历史、因子／ST／标的池／暴露 PIT 的状态和游标、Python／NumPy 全局 RNG、事件游标及代码／配置／已读输入文件指纹。恢复不重算前缀，并验证没有重复或跳过事件。
接口为 [AlphaReplay](../qsim/alpha_replay.py) 的 `save_checkpoint` / `load_checkpoint`；模型与 PIT 表提供 `state_dict` / `load_state_dict`。
checkpoint 使用 pickle 保存 pandas/NumPy 状态，仅加载本程序生成、可信的本地文件。自定义模型必须保存自己的全部状态；账户依赖模型目前返回 UNSUPPORTED。

## 对齐与报告

按 `(timestamp, instrument, alpha_name)` 的**键并集**核对，重复、丢失、额外输出均失败；不会只比较交集。
有限数值比较 `abs(test-reference) <= atol + rtol*abs(reference)`。
双方 NaN 仅记录并跳过；单侧 NaN 或任意无穷值失败。无有限配对值、全无效或无共同评估点为 INSUFFICIENT_DATA。
相对误差为 `abs(test-reference)/abs(reference)`，`0/0=0`，非零除以 0 记字符串 `"Infinity"`，保证 JSON 符合标准。

报告含配置、种子、实际选中时间／起点／预热、checkpoint 边界、比较区间、代码与输入 SHA256、计数、最大误差、首个差异及样例、耗时和覆盖限制。
状态为 PASS、FAIL、INSUFFICIENT_DATA、UNSUPPORTED、ERROR；子比较任一非 PASS 就不能整体通过。
报告旁保存检查点、恢复输出、worker 日志；失败保留可诊断信息。

2026-10-29 实跑摘要（完整 JSON 保存在 `/mnt/nas/qinml_cache/qsim/result/livetest_acceptance_20261029/report.json`）：

| 检查 | 状态 | 有效比较点（含多次运行） | 最大绝对误差 |
|---|---|---:|---:|
| f | PASS | 252 | 0 |
| p | PASS | 84 | 0 |
| c | PASS | 126 | 0 |

56 项构造数据及回归测试通过；未来泄漏、起点依赖、漏存滚动状态的故意错误实现均被检出。

## 覆盖边界

- 行情 bar 字段按 `bar_end` 可用；原始日线按 15:00；PIT 按 `available_at`，恰好等于 t 的记录保留。
- 日历和证券轴固定。缓存中没有发布时间的生命周期、停牌、ST 状态暂按当日 08:00 可用；不认证其历史发布时间。
- 不重新构建上游复权／行情缓存，不认证预先训练的表因子。绕过加载器的隐式 I/O、外部服务、自定义 RNG 对象、模型训练和账户依赖 alpha 需要专门适配。
- 当前导出一种 alpha：生产组合分数。未单独导出每个因子分量，也不验证账户恢复。
