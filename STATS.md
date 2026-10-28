# 统计和交易口径

按用户提供的业务定义实现。原系统源码未提供，区间端点、缺失值和费用等细节采用以下约定。

## 结果分两类

`daily/summary/events` 是账户结果，包含成交限制、费用和实际持仓变化。

`stats_*` 是目标 alpha 的参考公式统计。目标仓位不一定全部成交，因此参考统计与账户净值可能不同。

每个分组目标权重之和为 1；最高组做多、最低组做空，LS 两侧各占 1/2。各组及各固定时点独立分配配置资金。预热只计算因子，回测从空仓开始。

## StatsEvery

每天固定在同一个日内时点调仓，各时点独立维护账户和历史 alpha。`0930-1450-5` 默认生成 46 套策略，不在同一账户内连续调仓 46 次。

令 A 为目标 alpha，P 为延迟成交窗口的后复权 VWAP，C 为后复权日收盘价：

```text
hold_return[d,k]  = sum(A[d-1,k] * (P[d,k] / C[d-1] - 1))
trade_return[d,k] = sum(A[d,k]   * (C[d] / P[d,k] - 1))
raw_return       = hold_return + trade_return
```

结果逐日、逐时点写入 `stats_slots`。非零 alpha 对应价格缺失时，参考收益保留 NaN。整个横截面无有效信号时沿用旧目标；退出池或被过滤的证券目标置零，实际卖出仍受交易限制。

### 延迟

- `nIntvDelay=1`：10:00 信号使用 `(10:00,10:05]` VWAP，10:05 完成。
- `nIntvDelay=2`：使用 `(10:05,10:10]` VWAP。
- 按所选网格延迟，跳过午休；越过当日窗口的订单跳过并记录，不顺延到次日。
- `nIntvDelay` 至少为 1；日频 `0800` 固定为 1，使用当日全天 VWAP。

### 换手

```text
tvr[d,k] = sum(abs(A[d,k] - A[d-1,k]))

stats_tvr[d] = mean_k(tvr[d,k])   # isTradeT1=true
             = sum_k(tvr[d,k])    # isTradeT1=false
```

换手比较今日与昨日同一时点，双边计量、不除以 2。均值分母是配置中有执行窗口的时点数，不固定为 48 或 49。

`isTradeT1` 只影响 `stats_tvr`，不改变 PnL、IC 或成交。实际 T+1 使用独立开关 `execution.enforce_t1`。

逐时点账户结果在 `slot_daily/slot_summary`。主 `daily/summary` 是各时点账户的等初始资金比较组合，实际换手按成交金额计算。图表 `timing_comparison.png` 用于比较各时点表现。

## StatsInterval

`signal_times=[]` 表示全部采样点触发，也可指定 HHMM 列表。每日按连续有效区间、不重叠地分段，每段最多 `nbar` 根；使用段起点的 alpha。

例如 5 分钟、`nbar=2`，首段为 `(09:30,09:40]`。尾部不足时截断，记录 `actual_bars`，报告仍除以配置的 nbar。窗口不跨交易日；未触发区间不加入窗口成交价。

| priceMode | 成交价 |
|---|---|
| vwap | `sum(total_turnover) / sum(volume)`，再乘当日复权比例 |
| open | 段首 open |
| close | 段尾 close |

VWAP 不做算术平均。零总量、缺失量额或不完整 bar 不成交。日频 StatsInterval 当前仅支持 `nbar=1`。

Cprev 为前段末收盘价，Cend 为本段末收盘价，P 为成交价：

```text
# vwap / close
holding_return = sum(lastAlpha * (Cend / Cprev - 1))
trading_return = sum((alpha - lastAlpha) * (Cend / P - 1))
raw_return     = holding_return + trading_return

# open
raw_return = sum(alpha * (Cend / OpenStart - 1))

reported_return = (raw_return - cost_return) / nbar
```

首段 Cprev 使用预热末日收盘。close 模式下交易项为零。`/nbar` 仅用于参考统计，不缩放账户资金损益。

`cleareod=true` 在每日首次有效计算前清空统计状态 lastAlpha，不发平仓订单、不重置现金。参考换手和费用按重置后的状态计算。

### 输出

- `stats_intervals`：逐段归因、原始收益、费用、归一化收益及实际段长。
- `stats_sum`：所有有效段的日汇总。
- `stats_sumnoend`：排除触及所选窗口最后一根 bar 的整个段。窗口到 14:50 时，末 bar 就是 14:50。
- `stats_ic_sum` / `stats_ic_sumnoend`：相应起点的 Spearman IC 日均，保留条数和未定义值计数。

参考费用按目标权重变化计算，包含交易费、滑点及卖方附加费；`cost_return` 保留除以 nbar 前的值。借券费仅计入实际账户。Interval 的目标换手按段求和。

## IC 与样本边界

```text
entry  = 信号采样索引 + idelayEach
h      = ileadEach（省略则使用 backtest.forecast_period）
return = VWAP_adj[entry+h] / VWAP_adj[entry] - 1
```

`idelayEach` 默认 0。IC 预测期与成交延迟、Interval 分段长度分别设置，按所选网格计数，可跨午休和隔夜。

跨 IS/OS 边界标记 `split_embargo`，超过回测尾部标记 `right_censored`，均保留 NaN，不额外读取区间外行情。

相关系数至少需要 3 个有效证券。Interval 仅对已经到期但无法计算的 Spearman IC 置零，并标记 `ic_undefined`；Every 保留 NaN。

## 账户统计指标

收益和换手用小数，金额用元。年度、IS、OS、ALL 分别统计；以下基于账户结果。

| 指标 | 定义 |
|---|---|
| long / short | 日末多/空绝对市值占净资产比例的均值 |
| return / net / pnl | 累计净收益率 / 区间净值 / 元损益 |
| tvr / net_tvr | 双边成交额 / 逐 bar 同标的净成交额绝对值，按日初净资产归一后取日均 |
| sharp | 日收益均值 / 样本标准差 × sqrt(252)，无风险收益为 0 |
| dd / dd_start / dd_end | 最大回撤正值及峰谷日期；起始峰标为 initial |
| avgdd | 日末回撤幅度均值 |
| bp_mrgn | 元净损益 / 总双边成交额 × 10000 |
| winrate | 日净收益大于 0 的天数占比，含未交易日 |
| nHold | 日末持仓数均值；LS 两侧分别计数 |
| holdret | 原持仓按期末价相对上次估值的损益 / 区间初始资金 |
| traderet | 净成交单位 × (期末价 - 成交价) 的损益 / 区间初始资金 |
| tradecost | 累计费用 / 区间初始资金；tradecost_cash 为元金额 |
| IC / rank_IC | 分数与未来收益的 Pearson / Spearman 横截面相关，取时间均值 |
| ICIR | IC 均值 / 样本标准差，不年化 |
| auto_corr | 相邻信号分数的横截面相关；Every 比较同一时点的相邻交易日 |
| Tstat | IC 均值的 Newey–West t 值，滞后阶数为实际预测期减 1 |
| AHD | 已退出仓位按退出金额加权的持有交易日数；目标模式按可卖库存的平均入场日计算 |
| TC | 因子分数与实际 LS 带符号市值的横截面相关 |

组别行里的 IC/TC 是组合因子及 LS 诊断，不是组内重算。账户逐步核对 `净资产变化 = hold_pnl + trade_pnl - tradecost`。

## 旧配置

旧滚动批次算法保留为 `mode="Cohort"`，只有它使用 `holding_period`。新模式将该参数设为 1；Interval 跨度用 `stats.nbar`。小写 `statsevery` 会解析为新的 StatsEvery，不会调用旧算法。
