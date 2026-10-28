# QSim 回测系统

代码迭代和提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)，版本变化见 [CHANGELOG.md](CHANGELOG.md)。仓库只保存代码、配置、说明和测试；行情、因子及回测结果保留在 NAS。

基于 `document/nas_data_inventory.md` 和 `/mnt/nas/qinml_cache/backtesting/20260930` 的多因子研究系统（v0.2）。代码在 `/home/qinml/qsim`，运行输出默认在 NAS 的 `qinml_cache/qsim_runs`。原始行情和已生成的数据缓存只读。

已接通日频、1/5/30 分钟、VWAP 成交、分组与多空、T+1、涨跌停/停牌约束、可配置费用、标准化、历史时点回归中性化、年度/全期统计和图片。日频与 5 分钟全 A 示例已实际运行；这是研究模拟器，空头为合成空头。

2026-10-28 已按补充定义修正统计模式：**StatsEvery 是每天固定时点的独立策略，StatsInterval 是区间及仓位变化统计**。旧的滚动批次算法更名为 `Cohort`。详细公式、延迟和边界约定见 [STATS.md](STATS.md)。

## 运行

使用现有 alpha 环境，无须下载新行情：

```bash
cd /home/qinml/qsim
/home/qinml/miniconda3/envs/alpha/bin/python -m qsim --config configs/daily_demo.toml
/home/qinml/miniconda3/envs/alpha/bin/python -m qsim --config configs/minute_demo.toml
/home/qinml/miniconda3/envs/alpha/bin/python -m qsim --config configs/interval_demo.toml

# 仅检查配置 / 运行但不保存文件
/home/qinml/miniconda3/envs/alpha/bin/python -m qsim --config configs/daily_demo.toml --validate
/home/qinml/miniconda3/envs/alpha/bin/python -m qsim --config configs/minute_demo.toml --no-dump

# 测试
/home/qinml/miniconda3/envs/alpha/bin/python -m unittest discover -s tests -v
```

也可以使用 `./run.sh configs/daily_demo.toml`。依赖见 `pyproject.toml`；本机已具备 numpy、pandas、pyarrow、matplotlib。

**ST 数据限制**：现有全 A 池的历史 ST 字段全部为空。三个 `demo` 明确配置 `st_unknown="allow"`，用于验证完整程序链路，不能称为已经完全剔除 ST 的策略。`strict_template.toml` 和程序默认值采用 `st_unknown="error"`，缺失即报错；补入历史 ST 表即可严格运行。不能用当前股票名称、当前 ST 或行业快照倒推历史。

## 配置与接口

所有默认配置在 `qsim/config.py`；TOML 仅须覆盖需要改变的字段。拼错字段会报错。

| 配置 | 含义 |
|---|---|
| `data.root` / `raw_root` | 已生成缓存 / 原始行情根目录；原始日线提供涨跌停价 |
| `data.warmup_days` | 起点前读取多少个交易日预热；预热只更新因子，不下单、不计收益 |
| `backtest.start` / `end` | 日期两端包含，限定在缓存覆盖 2020-01-02 至 2026-09-30 内 |
| `backtest.frequency` | `0800` 为日频；分钟例如 `0930-1450-5`；当前步长支持 1、5、30 |
| `backtest.allow_os` | 默认 false；任何结束日在 OS 内的请求在读取数据前拒绝 |
| `backtest.os_start` | 默认 2024-01-01；允许更早，不允许把边界向后移动 |
| `backtest.groups` | 分组数，例如 5 或 10；G01 最低分，最高组做多、最低组做空 |
| `backtest.capital` | 每个独立时点/区间多空组合的初始资金；长、短两侧各一半。各分组是独立、同额初始资金的比较组合 |
| `backtest.mode` | `StatsEvery`（默认）、`StatsInterval`，或旧版 `Cohort` |
| `backtest.holding_period` | 仅 `Cohort` 的批次持有期；新统计模式固定为 1，Interval 跨度使用 `stats.nbar` |
| `stats.nIntvDelay` | Every 成交延迟；默认 1，10:00 信号对应截至 10:05 的 VWAP |
| `stats.isTradeT1` | 仅控制 Every 目标 alpha 换手汇总：true 平均、false 求和 |
| `stats.nbar` / `priceMode` | Interval 有效窗口长度、vwap/open/close 价格模式 |
| `stats.cleareod` | 仅在每日首次统计时清空 lastAlpha，不发平仓订单 |
| `stats.signal_times` | 可选 HHMM 列表；为空则所有采样点触发信号 |
| `stats.idelayEach` / `ileadEach` | IC 入口延迟、预测跨度；ileadEach 省略则使用 forecast_period |
| `execution.enforce_t1` | 独立的真实模拟交易 T+1 开关，默认 true |
| `backtest.forecast_period` | 因子 IC 的预测期，单位同上；与持有期独立 |
| `execution.fee_bps` | 每次成交、双边均收的费率；5 表示成交金额的 0.05% |
| `execution.sell_tax_bps` | 可选额外卖方税率；默认 0，未自动套用历史税率 |
| `execution.slippage_bps` | 可选按金额计的额外滑点成本；默认 0 |
| `execution.borrow_bps_annual` | 可选合成空头借券年费；默认 0，365 天口径 |
| `execution.participation` | 每个独立组合单 bar 的成交股数不超过源成交量的比例；默认 1% |
| `execution.limit_filter` | 默认 true，按日线真实涨跌停价检查成交方向 |
| `execution.suspension_filter` | 默认 true，缺失/停牌状态禁止成交，停牌股不参与新信号 |
| `execution.reject_bad_vwap` | 默认 true，源 VWAP 异常标记或不完整 bar 不成交、不形成 IC 标签 |
| `operations.exclude_st` | 默认 true，ST 股不参与新信号；已有仓位仍须合法卖出 |
| `operations.st_unknown` | `error` 报错，`exclude` 剔除未知，`allow` 明确保留未知并写入审计 |
| `operations.winsor_mad` | 横截面中位数 ± N × 1.4826 MAD 截尾；0 关闭 |
| `operations.standardize` | 横截面 z-score；零方差横截面不产生信号 |
| `operations.neutralize_styles` | 任选连续风格暴露列名 |
| `operations.neutralize_industries` | 任选分类行业列名，回归时自动生成哑变量 |
| `operations.exposures_path` | 历史时点暴露文件，含自定义回归变量 |
| `operations.min_cross_section` | 横截面有效样本数下限，默认 20 |
| `output.dump` | false 时不创建输出文件夹，Python API 仍返回结果对象 |
| `output.plots` / `positions` | 是否画图 / 保存每日完整持仓；最终持仓始终随 dump 保存 |
| `output.directory` / `name` | 输出根目录 / 本次名称；追加上海时间戳，避免覆盖 |

### 时间和统计模式

- `0800` 为日频 08:00 决策，当日 VWAP 成交，日终按收盘价估值。
- `0930-1450-5` 在所选窗口采样 46 个 bar，跳过午休。StatsEvery 默认建立 46 个独立固定时点策略，各自每天只调仓一次；StatsInterval 在一套账户中按有效区间依次处理。
- `nIntvDelay=1` 时，10:00 信号使用 `(10:00,10:05]` 的 VWAP，10:05 完成；2 对应 `(10:05,10:10]`。跨出当日窗口的延迟订单跳过并记入审计，不转到次日另一个时点。
- StatsEvery 统计使用昨日同一时点的 alpha 承担昨收至今日成交的收益，再由今日该时点 alpha 承担成交至今收的收益。新模式不使用原先的滚动持仓批次。
- `isTradeT1` 仅控制目标 alpha 的同点跨日换手平均/求和。实际 T+1 买卖限制始终由独立的 `execution.enforce_t1` 控制。
- StatsInterval 采用不重叠的连续有效窗口；VWAP 为窗口成交额之和 / 成交量之和，统计收益除以配置的 `nbar`。详细端点、尾部截断和 `sumnoend` 约定见 STATS.md。
- IC 预测期独立于实际交易路径；按所选采样序列计数，可跨午休和隔夜。标签不会输入因子，跨 IS/OS 边界或超出回测尾部的标签不计入评价。
- 原来的逐 bar 建批/到期退出算法保留为 `mode="Cohort"`；只有该模式使用可调的 `holding_period`。旧配置中的 `holding_period=5/46` 若配上新模式会明确报错，避免静默改变含义。

### Universe、因子和外部数据

内置 universe 为 `all_a`，继续使用历史生命周期池。另支持：

```toml
[universe]
kind = "symbols"
symbols = ["000001.XSHE", "600000.XSHG"]
```

或者 `kind="table"` 加 `path="/path/to/pool.parquet"`。外部池与全 A 生命周期取交集，未来若有中证 500/1000 的历史成分可按此接口接入。

**外部文件统一支持 CSV/Parquet，必须有 `order_book_id` 和 `available_at`**。日期时间统一上海时区；无时区字符串按上海时间处理。`available_at` 必须是信息实际可用时点，不是财报季度、报告日或回填日。只做截至决策时点的 as-of 更新，同一个时点/标的重复会报错，尚未公布的记录不可见。字段值一直延续到下次更新；需停用某个因子时提交 null，退出池时提交 false。

| 表 | 除统一键外的列 |
|---|---|
| 自定义因子 | 自定义数值列，例如 `my_alpha` |
| ST 表 `operations.st_path` | `is_st`：true / false / null |
| 标的池 | `in_universe`：true / false；未知默认不在池内 |
| 中性化暴露 | 任意数值风格列、任意分类行业列，例如 `size,beta,industry` |

```toml
[[factors]]
name = "quality"
kind = "table"
path = "/path/to/pit_factors.parquet"
column = "my_alpha"
weight = 0.6

[[factors]]
name = "reversal5"
kind = "reversal"
lookback = 5
weight = 0.4

[operations]
st_path = "/path/to/pit_status.parquet"
st_unknown = "error"
exposures_path = "/path/to/pit_exposures.parquet"
neutralize_styles = ["size", "beta", "my_custom_style"]
neutralize_industries = ["industry"]
standardize = true
```

内置 `momentum` 为过去 k 个采样 bar 的复权收盘收益，`reversal` 为其负值，`volatility` 为这 k 期收益标准差的负值（低波动方向）。全部因子在共同有效样本上先截尾，再进行带截距 OLS 中性化，再 z-score，最后按配置权重线性组合。回归变量缺失则该证券不参与，未指定变量时不回归。不会自动使用风险表中的某个模型或今天的行业分类。

股票价格/量额、风险暴露和财报的原始结构详见 [数据盘点](/home/qinml/document/nas_data_inventory.md)。源风险数据有不同 `model`，财报存在修订版本；应先按正确模型与实际公布时间整理成上述 PIT 表。第一版没有把未经时点校验的原始财报直接接入策略。

### Python / 张量接口

在项目目录中：

```python
from qsim import run

result = run({
    "backtest": {"start": "2023-01-03", "end": "2023-03-31"},
    "operations": {"st_unknown": "allow"},  # 仅用于数据链路演示
    "output": {"dump": False},
})
annual = result["summary"]
net = result["daily"]
ic = result["diagnostics"]
```

`MarketData.load_day(date)` 返回 `DayCube`，每个价格/量额字段的形状是 **[1, 标的数, 当日 bar 数]**，即 `[time/day, security, bar]`。固定标的轴跨日不变；每日分块流式读取，避免将 19 亿条分钟记录一次加载进内存。因子横截面、回归、分组与多组合持仓计算采用 NumPy 矩阵；交易时间和可卖库存按顺序处理。多年度 1 分钟回测仍会积累逐 bar 统计，第一版尚未实现结果流式落盘/断点续跑。

## 成交与会计口径

1. 订单股数按决策前可知的复权参考价和上一时点净资产形成；不使用当根成交价或收盘价反推信号仓位。随后按该 bar VWAP 检查实际可成交量、现金/敞口额度并缩减成交。
2. 买方向（包括空头回补）在 `VWAP_raw >= limit_up - 0.005` 时禁止；卖方向（包括合成卖空）在 `VWAP_raw <= limit_down + 0.005` 时禁止。阈值缺失或非正时该方向不成交。不假设所有板块统一 10% 涨跌停。
3. 使用 VWAP 和成交量只能判断 bar 级别约束，不能模拟排队次序或证明触板时每笔成交可实现。涨停但全天 VWAP 低于上限的 bar 仍可能允许买入。这是明确的模拟近似。
4. 每个独立组合的退出优先消耗该 bar 成交量额度；开仓使用剩余额度。分组比较组合不与主多空组合争抢同一份模拟流动性。各侧不使用额外融资；市场涨跌可能让持仓市值相对净值漂移。
5. 使用复权价格与浮点“复权单位”记账，原始成交量/涨跌停用原价和当日复权比例检查。此账本是总收益研究口径，不是逐项现金分红、送股、整手规则和真实券商股数账本。
6. 空头为合成研究腿，开仓收入隔离，不用于扩大多头预算；同样施加至少跨交易日的批次退出规则。未模拟历史融券标的、券源、召回或涨跌停盘口。
7. 每一步验证 `净资产变化 = hold_pnl + trade_pnl - tradecost`。费用包含设置的交易费、卖方附加费、滑点和借券费。借券费按日末空头市值计一日，周末/假期使用上个交易日日末市值计额外天数。
8. 缺失行情不能成交；持仓估值沿用最后有效价格并写审计计数，不填造交易收益。对退市且无后续可卖行情的仓位也不凭空清仓；需关注 `final_positions` 和陈旧估值记录。回测结束保留实际未平仓位，没有强制虚构平仓。

## IS / OS 隔离

2024-01-01 起全部为 OS。默认禁止，需明确 `allow_os=true` 才运行。改变日期、标签 horizon 或预热均不能绕过截止日；不会为补齐最后几条标签而读取请求终点后的行情。

未来标签跨越 IS/OS 边界时标记 `split_embargo`，样本尾部不足预测期时标记 `right_censored`，不计入 IC。预热在起点之前进行，因子只看截至当前的历史。跨边界连续运行时，持仓按原规则延续，同时分别输出 IS/OS 统计；不会为 OS 开始虚构一次清仓或重置资金。因子组合权重固定，第一版不包含参数寻优/模型拟合；外部因子的训练数据隔离仍由其生成流程负责。

## 输出及统计定义

`daily/summary/events` 是实际约束下的资金账本。StatsEvery 的主图/summary 是各独立时点账户的等资金比较组合，具体时点在 `slot_daily/slot_summary`。`stats_*` 是目标 alpha 的参考公式统计，不能与账户净值混用。

每次独立目录包含：

```text
config.json                  完整展开配置
audit.json                   配置/代码指纹、数据目录指纹、异常计数、模型假设
summary.csv                  每年、IS、OS、ALL；各组、long、short、LS
daily.csv / daily.parquet    日终净值、现金损益、敞口、费用和换手
events.parquet               逐 bar 及日终会计记录
diagnostics.csv / parquet    逐信号 IC、rank IC、自相关、TC 和标签状态
final_positions.parquet      未平仓仓位
positions.parquet            可选每日持仓
dashboard.png                净值/回撤/分组/IC，含图上指标
factor_analysis.png          分组总收益、累计 IC
slot_daily/slot_summary.*    各独立时点的净值、年度及全期统计
stats_slots/stats_tvr.*      Every 同点跨日参考 PnL、目标 alpha 换手汇总
stats_intervals.*           Interval 每个区间的参考分解及 /nbar 结果
stats_sum/stats_sumnoend.*   Interval 全区间/排除末窗口的日汇总
stats_ic_sum*.csv            Interval Spearman IC 日汇总
timing_comparison.png        Every 各独立时点曲线与收益对比
COMPLETE                     全部文件写入成功的标志
```

统一约定：收益率和换手用小数，金额用元，费率配置用 bps；图和 CSV 的策略数据完全一致。下表为本项目明确采用的定义，前司同名指标可能有不同分母，可据此调整。

| 指标 | 定义 |
|---|---|
| `from,to` | 统计区间首末交易日 |
| `long,short` | 日末多头/空头绝对市值占该组合净资产比例的时间均值 |
| `return,net,pnl` | 区间累计净收益率、相对区间初始净资产的净值、元损益 |
| `annual_return` | 按实际交易日数、252 日折算；短区间仅是数学年化 |
| `tvr` | 每日买卖绝对成交额之和 / 当日初始净资产，取日均；双边，不除以 2 |
| `net_tvr` | 每个 bar 对同一标的买卖净额取绝对值，再日合计 / 日初净资产，取日均 |
| `sharp` | 费后日收益均值 / 样本标准差 × sqrt(252)，无风险收益设为 0 |
| `dd,dd_start,dd_end` | 最大回撤正数及峰/谷日期，纳入起始净值 1；起始峰标为 initial |
| `avgdd` | 每日日终从历史峰值的回撤幅度均值 |
| `bp_mrgn` | 区间元净损益 / 总绝对成交额 × 10000 |
| `winrate` | 日净收益 > 0 的天数 / 总交易日数，含未交易日 |
| `nHold` | 日末非零持仓数均值；LS 是两侧持仓数之和，同一标的在两侧同时持有会计两次 |
| `holdret` | 原持仓按当期收盘相对上次估值的损益累计 / 区间初始资金 |
| `traderet` | 当期净成交单位 × (期末估值 - 成交价) 的累计损益 / 区间初始资金 |
| `tradecost` | 所有费用累计 / 区间初始资金；`tradecost_cash` 为元金额 |
| `IC,rank_IC` | 每个信号横截面分数与对应未来 VWAP 收益的 Pearson / Spearman 相关，然后时间平均 |
| `ICIR,rank_ICIR` | 对应 IC 的时间均值 / 样本标准差，不额外年化 |
| `auto_corr` | 相邻信号横截面分数的 Pearson 自相关，时间平均 |
| `Tstat` | IC 均值的 Newey–West HAC t 值，最大滞后实际 IC 跨度-1（ileadEach 优先），考虑重叠标签 |
| `AHD` | 已退出仓位按退出金额加权的实际持有交易日数（目标仓位模式按可卖库存的加权平均入场日核算），不把未退出仓位当已完成交易 |
| `TC` | 因子分数与实际 LS 两侧合计带符号市值的横截面 Pearson 相关，时间平均 |

分组行中的 IC/TC 等诊断来自同一组合因子/LS 实现，用于对照；不是重新为每个分组分别计算一个 IC。旧 Cohort 输出的 `Gxx_forecast_return` 是事后预测收益均值；新模式的分组表现使用分组账户净值。

## 验证与后续接口

测试覆盖 T+1 延迟退出、涨跌停方向、停牌、流动性容量和费用、复权事件、现金/持仓会计恒等式、时点数据可见性、中性化残差正交性、OS 访问开关、跨边界/尾部标签、外部因子/ST/Universe 以及关闭 dump。

示例因子只是用于验证程序，没有选择最优参数或通过表现筛选策略。源分钟缺失和异常 VWAP 保留原有质量标记。历史 ST/行业补齐、真实融券与公司行动、整手规则、按日历变化的费用表、分钟长区间结果流式落盘等可以继续在独立模块扩展。
