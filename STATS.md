# StatsEvery / StatsInterval：QSim v0.2 口径

依据用户 2026-10-28 提供的文字定义实现业务逻辑。没有找到原 ksim/zsim 源码；以下标为“项目约定”的内容不是原系统已核实行为。截图中未定义的 `vwappnl`、`showSecond`、`price_style=online` 没有猜测实现。本系统是历史数据模拟，主入口均保持防前视和 IS/OS 边界。

## 1. 模式和结果层次

```toml
[backtest]
mode = "StatsEvery"           # 或 StatsInterval
holding_period = 1            # 新模式不使用旧的分批持有参数

[stats]
nIntvDelay = 1
isTradeT1 = true
idelayEach = 0
# ileadEach = 48              # 可显式指定；省略时用 backtest.forecast_period
signal_times = []             # 全部采样时点；也可指定 ["1000", "1400"]

[execution]
enforce_t1 = true             # 独立的交易约束，不由 isTradeT1 控制
```

两层结果并列输出：

1. **账户模拟**：`events/daily/summary/slot_daily/slot_summary`。按真实成交单位、VWAP、手续费、成交量容量、涨跌停、停牌和可卖库存记账，遵循现金会计恒等式。因信号仓位按事前可知参考价下单、成交限制及仓位漂移，结果不必等于简化 alpha 公式。
2. **alpha 公式统计**：`stats_*`。将多因子分数转为分组等权目标 alpha 后按下面公式归因，给出目标换手、费前/费后以及跨度归一化结果。它们不声称股票一定能成交；不存在通过统计公式绕过实际账户 T+1 的交易。

项目约定：每个分组目标权重之和为 1；long 为最高组的 +1 暴露、short 为最低组的 -1 暴露，LS 对两侧各赋 1/2，目标毛暴露为 1。各独立账户从配置资金起步；long/short 各分一半。首次回测日从空仓开始，预热不创建假想历史仓位。

## 2. StatsEvery

### 独立时点与 PnL

`0930-1450-5` 对应 46 个固定信号时点。默认创建 46 套互相独立的账户和统计状态，而不是一天调仓 46 次的一套账户。每套只在自己的时点调仓一次，次日同点再次更新；不共享现金、持仓或昨日日内 alpha。

令 A 为该固定时点的目标 alpha 向量，P 为对应延迟成交窗口的后复权 VWAP，C 为后复权日收盘价：

```text
hold_return[d,k]  = sum(A[d-1,k] * (P[d,k] / C[d-1] - 1))
trade_return[d,k] = sum(A[d,k]   * (C[d] / P[d,k] - 1))
raw_return       = hold_return + trade_return
```

这里的 hold/trade 是参考公式的两段，不是账户账本中同名的现金 PnL 分解。`stats_slots` 对每个日/时点/分组分别保存。缺失价格如果影响非零 alpha，参考收益保留 NaN；不会把未知收益填成 0。

### 延迟与采样端点——项目约定

- 缓存 bar 表示 `(bar_start, bar_end]`。信号在起点产生，只看已完成历史 bar。
- `nIntvDelay=1`：10:00 信号对应 `(10:00,10:05]` VWAP，成交结果在 10:05 确定。
- `nIntvDelay=2`：10:00 信号对应 `(10:05,10:10]` VWAP，在 10:10 确定。
- 延迟沿所选日内采样网格，跳过午休。超出当日所选窗口则不成交、不生成该时点参考统计，并记录 `skipped_delay_signals`，不移到第二天改变固定时点身份。
- `nIntvDelay` 至少为 1；零延迟瞬时 VWAP 没有定义，不以未知的当根完整价格假装即时成交。
- 日频 `0800` 支持同日全天 VWAP，固定 `nIntvDelay=1`。

### 换手和 T+1 是两个开关

目标换手函数在本项目定义为双边绝对权重变化，不除以 2：

```text
tvr[d,k] = sum(abs(A[d,k] - A[d-1,k]))

stats_tvr[d] = mean_k(tvr[d,k])     if isTradeT1=true
             = sum_k(tvr[d,k])      if isTradeT1=false
```

比较的是**今天与昨天同一个时点**，不是今日相邻时点。分母为本次配置下具有执行窗口的固定时点数，不硬编码 48 或 49。整个横截面无有效信号时维持之前该时点的目标，不制造零信号清仓。

`isTradeT1` **只改变 `stats_tvr` 汇总**：不改变参考 PnL、IC、实际成交、费用或账户净值。实际当日新增库存是否允许卖出由 `execution.enforce_t1` 决定，默认 true。这一拆分已做回归测试。

账户 `daily/summary.tvr` 仍是实际成交额换手，不伪装成目标 alpha 换手。StatsEvery 主 `daily/summary` 和 dashboard 为独立账户的等初始资金比较组合；其曲线不会因 `isTradeT1` 改变。逐时点结果见 `slot_*` 和 `timing_comparison.png`，选择交易时点应查看这些表和图。

## 3. StatsInterval

```toml
[backtest]
mode = "StatsInterval"
holding_period = 1

[stats]
nbar = 2
priceMode = "vwap"            # 也支持 open / close
cleareod = false
signal_times = []
```

### 有效信号、分段和 VWAP——项目约定

1. `signal_times=[]` 表示所选网格全部触发；指定 HHMM 列表则仅这些点触发，相当于显式的 isFireWork 掩码。必须是网格上的合法时点。
2. 每日把有效区间按时间排序，按 **不重叠** 的 nbar 个区间分段。每段只使用起点已经可知的 alpha；起点每次前进 nbar 个有效区间。未触发的原始槽不加入窗口 VWAP。
3. 例如 5 分钟、nbar=2，第一段包含 `(09:30,09:35]` 和 `(09:35,09:40]`，在起始边界 09:30 产生 alpha、09:40 结算。这是本项目的 i 到 i+nbar 的边界定义。
4. 午休不生成虚假行情；每日窗口不跨交易日。尾部不足 nbar 时截断到最后一个有效区间，并输出 `actual_bars`。**报告仍除以配置的 nbar**，不偷偷改变分母。
5. VWAP 使用缓存保留的原始金额和成交量：`sum(total_turnover) / sum(volume)`，再乘当日后复权比例。不能算小 bar VWAP 的算术平均。零总成交量、缺失金额/量或不完整 bar 不成交。
6. `open` 取段首 open，`close` 取段尾 close；实际成交和研究统计均显式使用所选价格模式。日频的 StatsInterval 当前仅支持 nbar=1；多日窗口暂不隐式聚合。

### 收益方向——明确的项目定义

令 Cprev 为前一个计算段末收盘价，Cend 为当前段末收盘价，P 为本段成交价格。首段的 Cprev 使用预热末日收盘；跨日不自动清空 lastAlpha。

```text
# VWAP / close
holding_return = sum(lastAlpha * (Cend / Cprev - 1))
trading_return = sum((alpha - lastAlpha) * (Cend / P - 1))
raw_return     = holding_return + trading_return

# open（项目约定：当段 alpha 承担开盘至段末的收益）
raw_return     = sum(alpha * (Cend / OpenStart - 1))

reported_return = (raw_return - cost_return) / nbar
```

`close` 模式 P=Cend，因此该参考公式的交易项为 0。买入价低于段末价时，新增多头交易贡献为正；测试使用人工数值验证了方向。

`cost_return` 是根据目标权重变化估算的交易费和滑点，卖方附加费按负方向权重变化计；其列保留归一化前数值。借券费仅计入实际账户，不在这个参考公式中重复估算。此费用约定是本项目选择，不是对截图未写明部分的推断。

`/nbar` 只发生在参考统计的 `reported_return`，不会把真实资金账本上的利润或费用除掉，不是复利等效单期收益，也不作用于 VWAP。

### cleareod 和输出

`cleareod=true` 在每日**第一次有效计算**前把统计状态 lastAlpha 清零。它不向账户发送任何平仓订单，也不重置现金；测试确认开关切换前后账户净值一致。参考换手/开仓费用按照被重置的 lastAlpha 计算，所以参考统计可能不同。

- `stats_intervals`：逐段原持仓/变动持仓贡献、原始收益、费用、归一化收益、目标换手、实际段长、是否末段。
- `stats_sum`：各日所有有效段汇总；reported_return 是各段归一化收益之和。
- `stats_sumnoend`：排除**触及所选采样窗口最后一根 bar 的整个计算段**。若选择到 14:50，则末 bar 是 14:50，不会错误声称已经读取 15:00 竞价。这是明确的项目约定。
- `stats_ic_sum` / `stats_ic_sumnoend`：对相应有效起点的 Spearman IC 取日均，保留可用条数以及 undefined-to-zero 数量。参考换手按段求和，不借用 Every 的 mean 开关。

## 4. IC 时点与边界

IC 使用信号起点分数与 `VWAP_adj[entry+h] / VWAP_adj[entry] - 1`：

- `entry = 信号采样索引 + idelayEach`；默认 0，即信号所对应的当根 VWAP。
- `h = ileadEach`，省略则使用 `backtest.forecast_period`。单位是所选采样网格的 bar，不硬编码一天 48/49 个 bar；Interval 的 nbar 独立控制 PnL 分段。
- 标签可跨日，但不会为了补齐尾部而读取回测终点之后的数据。IS→OS 的标签标为 split_embargo，样本尾部标为 right_censored，均保持 NaN。
- Interval 对**已经到达结束时点但相关系数未定义**的 Spearman IC 置 0，同时标记 `ic_undefined=true` 和实际 `label_count`；每个相关系数至少需要 3 个有效证券。这一兼容规则不应用于尚未到期/跨隔离边界的标签。
- Every 未定义 IC 保留 NaN。各固定时点的 IC、自相关和 TC 独立计算，其他时点不会改写其历史状态。

## 5. 验证与迁移

旧 `statsevery` 小写配置会解析为新 `StatsEvery`，**不会继续使用旧的滚动批次算法**。该旧算法保留为明确命名的 `Cohort`，便于复核此前结果。旧 holding_period 若不为 1 且模式不是 Cohort，会报错并说明替代参数。

新增测试验证：文档 Every 公式、同点跨日状态、删除其他时点不改变目标时点净值、isTradeT1 只改变目标换手汇总、nIntvDelay 索引、有效时点和尾部截断、金额/量加权、Interval 收益方向和 /nbar、cleareod 不平账户、实际 T+1、资金损益一致性以及导出。原先的复权、中性化、费用、OS 访问、PIT 数据和尾部标签测试继续保留。
