# QSim

多因子回测系统，支持日频和 1/5/30 分钟，默认按 VWAP 成交。

- 因子：线性组合、MAD 截尾、标准化、风格和行业中性化。
- 交易：分组、多空、T+1、涨跌停、停牌、费用和成交量限制。
- 数据：全 A 或自定义标的池，按信息可用时间接入外部因子。
- 输出：净值、年度及全期统计、分组表现、IC 和图表。
- 计算：命名因子、表达式、因子线程池、多配置进程池；日期范围由使用者决定。
- 存储：默认逐日／按批写 Parquet，保留当前状态和待到期标签；大结果按需读取。

[统计口径](STATS.md) · [开发说明](CONTRIBUTING.md) · [版本记录](CHANGELOG.md)

可选 alpha 因果性与状态检查：`./run.sh configs/livetest_demo.toml --livetest all`。
支持 `f`（未来数据）、`p`（历史起点）、`c`（checkpoint）及组合；不传参数保持普通回测。见 [livetest 使用说明](document/LIVETEST.md)。

AI 接手与迭代记录：[开发交接文档](document/QSIM_DEVELOPMENT.md)。

v0.3 新功能及兼容说明：[流式与并行使用说明](document/STREAMING.md)。

## 安装和运行

需要 Python 3.11+。

```bash
python -m pip install -e .
python -m unittest discover -s tests -v

python -m qsim --config configs/daily_demo.toml
python -m qsim --config configs/minute_demo.toml
python -m qsim --config configs/interval_demo.toml
```

`--validate` 只检查配置，`--no-dump` 运行但不保存结果。也可使用 `./run.sh configs/daily_demo.toml`；解释器可通过 `QSIM_PYTHON` 指定。

```bash
# 三个命名因子、表达式、独立图表及因子值导出
./run.sh configs/streaming_demo.toml
# 因子线程池（实际并发按可用资源收缩）
./run.sh configs/streaming_demo.toml --factor-pools 2
# 多配置回测进程池
./run.sh configs/streaming_demo.toml configs/simple_factors.toml --pools 2
```

多配置任务共享版本化输入缓存并限制嵌套并发；同一账户按时间顺序处理。较大运行仍需资源预检与 cgroup 等硬限制。

## 三个简单因子试跑

```bash
./run.sh configs/simple_factors.toml --compare-factors
```

2023 年一季度、全 A、日频、5 分组，串行运行三个单因子和等权组合：

| 因子 | 日频公式（C 为已完成交易日的复权收盘价） |
|---|---|
| 20 日动量 | `C[t-1] / C[t-21] - 1` |
| 5 日反转 | `-(C[t-1] / C[t-6] - 1)` |
| 20 日低波动 | 最近 20 个日收益的标准差取负，`ddof=0` |

各因子先 MAD 截尾、横截面标准化，再等权相加；组合只使用三者都有值的证券。
30 个交易日预热，08:00 取信号，当日 VWAP 成交，5 日 IC；费用每次成交 5 bps，成交量上限 1%，执行 T+1。
历史 ST 未知值允许通过，空头为合成仓位，本例只验证流程能否跑通。

结果保存在 `/mnt/nas/qinml_cache/qsim/result/` 的独立目录。`comparison.csv` 汇总多空收益、回撤、IC、信号数和成交天数，各子目录保留完整报告和图表。
出现无信号、无成交、无有效 IC 或损益对账失败时，命令报错并保留 `INCOMPLETE`；全部通过才写 `COMPLETE`。
`--compare-factors` 也可用于含 2–5 个非零权重因子的其他配置；组合沿用配置权重。

启动脚本将缓存和临时文件放入 NAS，数值库及 Arrow 线程设为 1。较大运行仍须先检查资源配额、设置硬限制并监控。

[独立验证脚本](scripts/verify_simple_factors.py) 可核对本例输入、信号、IC、参考统计和实际账户，不调用 QSim 引擎。2026-10-29 四组账户结果复算一致；目标参考多空收益仍有 30–36 天缺失，不能当成完整收益曲线。范围与证据见[开发记录](document/QSIM_DEVELOPMENT.md#102-三因子结果独立验证2026-10-29)。

## 配置

完整参数见 [config.py](qsim/config.py)，示例见 [configs](configs)。

| 配置 | 用途 |
|---|---|
| `data.root` / `raw_root` | 生成数据缓存 / 原始行情目录 |
| `data.warmup_days` | 起点前的预热交易日数，预热不交易 |
| `backtest.start` / `end` | 回测起止日期，两端包含 |
| `backtest.frequency` | 日频 `0800`；分钟如 `0930-1450-5` |
| `backtest.mode` | `StatsEvery`、`StatsInterval`、`Cohort` |
| `backtest.groups` / `capital` | 分组数 / 每个独立组合的初始资金 |
| `backtest.forecast_period` | IC 预测期，单位为所选网格的 bar |
| `backtest.holding_period` | 仅 Cohort 使用；其他模式设为 1 |
| `universe.kind` | `all_a`、`symbols`、`table` |
| `execution.fee_bps` | 每次成交的费用，5 表示 0.05% |
| `execution.participation` | 成交量占比上限，默认 1% |
| `execution.enforce_t1` | 实际账户的 T+1 限制，默认开启 |
| `operations` | ST 过滤、截尾、标准化和中性化 |
| `output.dump` / `directory` | 是否保存 / 输出目录 |
| `output.stream` / `materialize` | 默认流式写入／是否显式把全部结果加载为 DataFrame |
| `output.factors` / `factor_batch_rows` | 原值、处理后值、组合值导出／写入批大小 |
| `compute.pools` / `batch_pools` | 因子线程数／多配置进程数的请求上限 |
| `compute.memory_per_job_mb` | 单任务内存预算；用于任务准入和运行中检查 |
| `combine.expression` / `stage` | 合成表达式／使用 processed 或 raw 分量 |

`0930-1450-5` 从 09:30 开始，最后一根 bar 为 `(14:45,14:50]`，跳过午休，每天共 46 根。因子只能使用决策时已知的信息，不能使用本根完整行情。

## 外部因子和标的池

CSV、Parquet 均可。必须有 `order_book_id`、`available_at`，分别表示带交易所后缀的代码和信息实际可用时间。无时区时间按上海时间处理。

| 数据 | 其他字段 |
|---|---|
| 因子 | 自定义数值列，在 factors 中指定 `kind="table"`、`path`、`column`、`weight` |
| ST | `is_st`，通过 `operations.st_path` 接入 |
| 标的池 | `in_universe`，使用 `universe.kind="table"` 和 `path` |
| 中性化暴露 | 数值风格列、分类行业列；配置 `exposures_path`、`neutralize_styles`、`neutralize_industries` |

按 `available_at` 向后延续至下一条更新；重复时点/证券会报错。外部池与全 A 生命周期取交集。内置因子有 momentum、reversal 和低波动方向的 volatility。

## 数据与结果

当前服务器的缓存为 `/mnt/nas/qinml_cache/backtesting/20260930`，覆盖 2020-01-02 至 2026-09-30；原始行情在 `/mnt/nas/data/whole_history`。代码仓库不包含这些数据。

分钟数据按日读取，数组形状为 `[day, security, bar]`。默认流式模式每天落盘并清理事件／参考统计缓冲；因子明细按批写入。外部因子文件按所需列、分块构建共享 PIT 索引，不再为每个因子重复加载整张表。

默认结果写入 `/mnt/nas/qinml_cache/qsim/result`，每次生成独立运行 ID 目录；旧结果不迁移：

- `daily`、`summary`、`events`：账户净值、统计和逐次损益。
- `slot_daily`、`slot_summary`：各固定时点的独立结果。
- `stats_*`：目标 alpha 的公式统计，详见 [STATS.md](STATS.md)。
- `diagnostics`、`final_positions`：因子评价和期末持仓。
- `summary.json`、`config.json`、`audit.json`、`manifest.json`：严格 JSON 汇总、配置、版本、异常与输出清单。
- `run.log`、`run.jsonl`：运行阶段、进度、覆盖率、拒单原因、资源／落盘进度和异常堆栈。
- 单独的多空净值／金额 PnL、分组净值／收益／超额收益、IC、换手 PNG；`COMPLETE` 或失败时的 `INCOMPLETE`。

流式模式的 `events.parquet` 等是包含多个 Parquet 分区文件的目录，`pandas.read_parquet` 可直接读取整个目录。`summary` 仍返回 DataFrame；大明细返回 `DiskTable`，支持 `iter_batches()` 和显式 `to_pandas()`。`output.materialize=true` 可恢复全量内存返回方式，但会放弃最终读取阶段的内存限制优势。

Python 接口仍为 `from qsim import run`。`dump=false` 或 `stream=false` 保留小规模内存参考路径；`--no-dump` 不保存结果与运行日志，输入缓存仍可能生成。

QSim 不再强制划分 IS/OS，也不屏蔽跨 2024 年的标签；旧 `allow_os`、`os_start` 参数兼容读取但忽略。研究划分由调用方管理；超过回测终点的标签仍保留 NaN。

## 当前限制

历史 ST 数据尚未补齐。示例允许未知 ST；正式配置默认缺失即报错，见 [strict_template.toml](configs/strict_template.toml)。行业中性化需历史时点暴露，不能使用当前行业倒推。

空头为合成研究仓位；未模拟真实券源、整手规则和逐笔排队。复权单位用于总收益研究，不等同于券商股数账本。缺失行情不能成交，已有仓位按最后有效价格估值并记录异常。回测结束不强制平仓。
