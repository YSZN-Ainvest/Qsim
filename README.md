# QSim

多因子回测系统，支持日频和 1/5/30 分钟，默认按 VWAP 成交。

- 因子：线性组合、MAD 截尾、标准化、风格和行业中性化。
- 交易：分组、多空、T+1、涨跌停、停牌、费用和成交量限制。
- 数据：全 A 或自定义标的池，按信息可用时间接入外部因子。
- 输出：净值、年度及全期统计、分组表现、IC 和图表。
- 样本：2024-01-01 起为 OS，默认禁止，须设置 `allow_os = true`。

[统计口径](STATS.md) · [开发说明](CONTRIBUTING.md) · [版本记录](CHANGELOG.md)

可选 alpha 因果性与状态检查：`./run.sh configs/livetest_demo.toml --livetest all`。
支持 `f`（未来数据）、`p`（历史起点）、`c`（checkpoint）及组合；不传参数保持普通回测。见 [livetest 使用说明](document/LIVETEST.md)。

AI 接手与迭代记录：[开发交接文档](document/QSIM_DEVELOPMENT.md)。

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

分钟数据按日读取，数组形状为 `[day, security, bar]`，每次加载一天。完整长区间的统计结果仍保存在内存中。

默认结果写入 `/mnt/nas/qinml_cache/qsim_runs`，每次生成独立目录：

- `daily`、`summary`、`events`：账户净值、统计和逐次损益。
- `slot_daily`、`slot_summary`：各固定时点的独立结果。
- `stats_*`：目标 alpha 的公式统计，详见 [STATS.md](STATS.md)。
- `diagnostics`、`final_positions`：因子评价和期末持仓。
- `config.json`、`audit.json`：配置、代码指纹和异常计数。
- PNG 图片及 `COMPLETE` 完成标记。

Python 接口为 `from qsim import run`。`run(config)` 接受配置字典或 TOML 路径，返回 DataFrame 等结果；`dump=false` 时不写文件。

## 当前限制

历史 ST 数据尚未补齐。示例允许未知 ST；正式配置默认缺失即报错，见 [strict_template.toml](configs/strict_template.toml)。行业中性化需历史时点暴露，不能使用当前行业倒推。

空头为合成研究仓位；未模拟真实券源、整手规则和逐笔排队。复权单位用于总收益研究，不等同于券商股数账本。缺失行情不能成交，已有仓位按最后有效价格估值并记录异常。回测结束不强制平仓。
