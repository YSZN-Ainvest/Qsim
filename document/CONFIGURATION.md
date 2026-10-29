# 配置字典

[返回 README](../README.md) · [完整模板](../configs/tutorial.toml) · [运行指南](USAGE.md)

配置可来自 TOML 文件或 Python 字典。TOML 未写的键由默认值补齐；未知配置段、未知参数、错误类型或不支持的组合会报错。名称区分大小写，布尔值写 `true/false`，不加引号。日期建议写带引号的 `YYYY-MM-DD`。

路径按进程当前工作目录解析，不是相对 TOML 所在目录；QSim 不展开 `$VAR` 或 `~`。跨机器部署请显式设置全部路径。本文把实现中带环境约定的路径默认值记为“部署默认”，不将它们当作可移植输入；`--validate` 可打印本机实际合并值。

## data

| 键 | 类型／默认 | 作用及约束 |
|---|---|---|
| `root` | 字符串／部署默认 | 预处理行情根目录，含 calendar、securities、prices、vwap、universe；不是缓存输出目录。见[数据目录](DATA.md#行情目录与字段) |
| `raw_root` | 字符串／部署默认 | 原始日线根目录；加载 `daily_bar/YYYY/YYYYMMDD.csv` 的收盘与涨跌停价格。分钟回测同样需要 |
| `warmup_days` | 整数／40 | 回测起点前的交易日数，≥0；只加载历史，不交易。普通回测最多取现有历史，完整性需自行确认；livetest 的起点检查要求足量预热 |
| `cache_directory` | 字符串／`QSIM_CACHE_ROOT`，否则部署默认 | 共享 Arrow 输入缓存与 PIT SQLite 索引目录。环境变量只决定此默认值，不会替换 TOML 内容 |
| `batch_rows` | 正整数／4096 | 输入批读取行数；不是回测天数或因子数量 |
| `shared_cache` | 布尔／true | 行情是否通过版本化 Arrow 缓存读取。设 false 直接读行情源；外部 PIT 仍使用磁盘索引 |
| `pit_columns` | 字典／`{}` | 内部共享投影：绝对源路径 → 所需列名数组。批量运行自动生成，普通用户保持空字典；手动填写必须覆盖实际所需列 |

内置有限窗口因子至少需要 `lookback+1` 根已完成 bar。`warmup_days` 按交易日计，`lookback` 按采样 bar 计，不能直接当成同一单位。预热也遵守配置中的日内采样窗口。

## backtest

| 键 | 类型／默认 | 作用及约束 |
|---|---|---|
| `start` | 日期字符串／`2023-01-03` | 起始日期，包含当天；实际运行取交易日历中的日期 |
| `end` | 日期字符串／`2023-03-31` | 结束日期，包含当天；必须不早于 start 且在输入日历覆盖内 |
| `frequency` | 字符串／`0800` | `0800`、`daily`、`1d` 为日频；分钟为 `HHMM-HHMM-1/5/30`，例如 `0930-1450-5`。窗口位于 09:30–15:00 且对齐 bar，跳过午休 |
| `mode` | 字符串／`StatsEvery` | `StatsEvery`、`StatsInterval`、`Cohort`；也接受全小写别名。见[模式时间线](USAGE.md#选择采样与账户模式) |
| `holding_period` | 正整数／1 | Cohort 的批次持有 bar 数；Every、Interval 必须为 1，Interval 区间由 nbar 控制 |
| `forecast_period` | 正整数／5 | 默认 IC 标签跨度，单位为所选采样 bar；Every/Interval 的 ileadEach 非空时优先使用它 |
| `groups` | 整数／10 | 分组数，≥2；有效分数不足组数或近乎常数时不能分组 |
| `capital` | 正有限数／10000000 | 每个独立组合的初始资金；Every 各时点分别配置。不表示所有分组共享这一笔资金 |

日频按 08:00 决策、当天全天 VWAP 成交，不能在信号中使用当天最终收盘。`0930-1450-5` 的最后一根是 `(14:45,14:50]`，共 46 根，不补上 14:50 后的历史。

没有固定 IS/OS 边界。旧 `allow_os`、`os_start` 仅为兼容读取并忽略，新配置不要写。

## stats

| 键 | 类型／默认 | 作用及适用模式 |
|---|---|---|
| `nIntvDelay` | 整数／1 | Every 信号到成交窗口的位移，≥1：1 用紧随信号的 bar，2 用下一根。按选定网格跳午休，超出当天窗口则跳过。日频和 Interval 必须为 1 |
| `idelayEach` | 整数／0 | Every/Interval 的 IC 标签起点相对信号网格的偏移，≥0；不改变实际下单窗口 |
| `ileadEach` | 正整数／省略 | Every/Interval 的 IC 标签跨度，覆盖 forecast_period；默认 None。TOML 没有 null，使用默认时删去此键 |
| `isTradeT1` | 布尔／true | Every 参考 `stats_tvr` 按时点取均值；false 取和。不改变实际账户 T+1、PnL 或 IC |
| `nbar` | 正整数／1 | Interval 每段最多含几个被选中的 bar，区间不重叠、不跨交易日；日频必须为 1。参考收益除以配置 nbar，实际账户 PnL 不除 |
| `priceMode` | 字符串／`vwap` | Interval 支持 `vwap/open/close`；Every 只支持 vwap。VWAP 为总额／总量，不是各 bar VWAP 算术均值 |
| `cleareod` | 布尔／false | Interval 每天首次统计前清空参考 lastAlpha；不下平仓单、不重置账户现金 |
| `signal_times` | 字符串数组／`[]` | Every/Interval 的信号选择，`["0930","1000"]` 等。空数组使用全部采样起点；必须唯一且存在于配置网格。Cohort 不使用该筛选 |

稀疏 signal_times 下，Interval 的 nbar 对“选中的 bar 列表”分段，中间未选择的 bar 不计入成交窗口。要连续十分钟窗口，请使用完整 5 分钟网格、nbar=2，而不是把两个远隔时点当作连续窗口。

## universe

| 键 | 类型／默认 | 作用及约束 |
|---|---|---|
| `kind` | 字符串／`all_a` | `all_a` 使用输入的全 A 生命周期池；`symbols` 限定静态证券轴；`table` 再叠加历史 PIT 成分表 |
| `symbols` | 字符串数组／`[]` | kind=symbols 时必填；每个标识必须存在于证券表且 type=CS。会去重并按标识排序；并不绕过上市／退市过滤 |
| `path` | 字符串／空 | kind=table 时必填；CSV、Parquet 文件或分区目录，包含 `order_book_id, available_at, in_universe` |

table 池是 **全 A 生命周期 ∩ 截至决策时刻的 in_universe**。未发布状态的股票不在池内；退出时必须写 `false` 更新，不能只从下一份文件中省略该股票。详见[历史池](DATA.md#标的池st-与中性化暴露)。

## alphas.NAME 与 combine

`NAME` 是因子名称，用于表达式、日志和导出。建议使用字母、数字、下划线，且以字母开头。`[alphas.NAME]` 与旧 `[[factors]]` 二选一，不能混用；旧写法额外需要 `name`。

| 因子键 | 默认／要求 | 含义 |
|---|---|---|
| `kind` | 必填 | `table`、`momentum`、`reversal`、`volatility`、`expression` |
| `weight` | 1.0 | 无全局合成表达式时的有限权重，可正可负，可为零，至少一个非零；不自动归一化。有 combine.expression 时所有 weight 必须为 1，权重写在公式中 |
| `path` | table 必填 | 带 PIT 键的外部表路径；可让多个因子引用同一张表 |
| `column` | table 默认为 NAME | 读取的数值列；缺列会报错，不自动猜测 |
| `lookback` | 内置因子必填，正整数 | 使用最近 lookback+1 根已完成收盘价，形成 lookback 个收益间隔 |
| `expression` | expression 必填 | 引用其他已定义因子的原始值，按依赖层计算；循环依赖、未知引用会报错 |

设 `C[-1]` 为最近已完成的复权收盘，k=lookback：

| 内置类型 | 原始值 |
|---|---|
| momentum | `C[-1] / C[-k-1] - 1` |
| reversal | `-(C[-1] / C[-k-1] - 1)` |
| volatility | 最近 k 个简单收益的标准差取负，ddof=0；数值越大表示波动越低 |

窗口内价格非有限或非正时，该证券原值为 NaN。内置因子不是自动注册任意 Python 插件；扩展计算逻辑需要修改实现并验证 PIT/state 接口。

以下是替换或新增 alpha 配置的完整片段，不需要另一个回测账户：

```toml
[alphas.momentum20]
kind = "momentum"
lookback = 20
weight = 1.0

[alphas.reversed_quality]
kind = "expression"
expression = "-<quality>"
weight = 1.0
```

| 合成键 | 默认 | 含义 |
|---|---|---|
| `combine.expression` | 空字符串 | 空时按 weight 求和；非空时按公式计算，不隐式加入未引用因子 |
| `combine.stage` | `processed` | `processed` 使用每个分量处理后的值；`raw` 使用原值，组合分数仍受 eligible、共同有限值和 min_cross_section 限制 |

公式支持 `+ - * / **`、一元正负、括号、有限数值常量与 `abs/sqrt/log/exp/minimum/maximum/clip/rank/zscore`。例如 `clip(<quality>, -3, 3)`、`0.5*(quality+value)`。不支持属性、下标、任意函数调用或任意时序操作。非有限计算结果置 NaN。

区别：**派生因子 expression 读取依赖的原始值**，再作为普通因子接受 operations；**combine.expression 默认读取处理后值**。加工所有分量之后合成，不再自动对最终分数做第二遍 operations。

## operations

| 键 | 类型／默认 | 作用及约束 |
|---|---|---|
| `exclude_st` | 布尔／true | 过滤 ST 股票，作用于可参与计算的标的池 |
| `st_unknown` | 字符串／`error` | ST 状态未知时：error 中止；exclude 排除；allow 允许并计数。不是把未知判作非 ST |
| `st_path` | 字符串／空 | 可选 PIT ST 表，字段 is_st。非空时使用此表；空时使用行情池的 st_status |
| `winsor_mad` | 非负有限数／5.0 | 在中位数 ± 参数 × 1.4826 × MAD 范围截尾；0 关闭，MAD 近零时不截尾 |
| `standardize` | 布尔／true | 当期有效截面 z-score，标准差 ddof=0；常数截面或有效值不足返回 NaN |
| `exposures_path` | 字符串／空 | 历史 PIT 暴露表；配置任一中性化列时必需 |
| `neutralize_styles` | 字符串数组／`[]` | 连续数值风格列，例如 size、beta；OLS 包含截距 |
| `neutralize_industries` | 字符串数组／`[]` | 分类行业列，构造哑变量并删除参照类别 |
| `min_cross_section` | 整数／20 | 每个因子处理所需最少有效证券数，≥2。中性化还要求有效样本数多于设计矩阵列数+1 |

顺序：合成所需因子取共同有效截面 → MAD 截尾 → OLS 残差化 → 标准化。暴露缺失的行不能参加 OLS。最少样本数与 groups 是两道约束；设定更多分组不会创造更多有效股票。

## execution

| 键 | 类型／默认 | 作用及约束 |
|---|---|---|
| `fee_bps` | 数值／5.0 | 每次成交费用，按成交金额计；1 bps=0.01%，5 bps=0.05% |
| `sell_tax_bps` | 数值／0.0 | 卖出成交的附加费率 |
| `slippage_bps` | 数值／0.0 | 按成交金额计的额外成本，不是逐笔冲击模型 |
| `borrow_bps_annual` | 数值／0.0 | 合成空头年化借券成本，作用于实际账户；不表示存在真实券源 |
| `participation` | 数值／0.01 | 允许成交的窗口成交量占比上限，0<值≤1；不是目标资金权重 |
| `limit_filter` | 布尔／true | 按原始涨跌停价格检查成交方向约束 |
| `suspension_filter` | 布尔／true | 过滤停牌和未知停牌状态，限制计算与成交 |
| `reject_bad_vwap` | 布尔／true | 拒绝被 vwap_outside_ohlc 标记的成交价格；不等同于认证全部行情质量 |
| `short_mode` | 字符串／`synthetic` | 当前只支持 synthetic；不模拟券源、召回或真实融券流程 |
| `enforce_t1` | 布尔／true | 实际股票多头当日买入不可当日卖出；与 stats.isTradeT1 的参考换手开关分开 |

费用参数范围为 `[0,10000)` bps。缺价格、量额或完整 bar 时不补造成交；退出池不保证一定成交。具体资金、T+1、容量与计费规则见[统计口径](../STATS.md)。

## compute

| 键 | 类型／默认 | 作用及约束 |
|---|---|---|
| `pools` | 正整数／1 | 单配置内的持久因子线程池上限；CLI --factor-pools 覆盖 |
| `batch_pools` | 正整数／1 | 多配置独立回测的进程池上限；CLI --pools 覆盖。单配置不使用该池 |
| `memory_per_job_mb` | 正整数／512 | 每个任务的内存预算（MiB），用于批次准入及运行中 RSS 检查；不是操作系统硬上限 |

批次未指定 --pools 时，使用各配置 batch_pools 的最大请求值，再按任务数和资源收缩；单任务预算按各配置的最大值做保守准入。批次清单记录请求／实际并发。配额不可见时降为串行；不要仅凭配置宣称已并行。

## output

| 键 | 类型／默认 | 作用及约束 |
|---|---|---|
| `dump` | 布尔／true | 保存回测输出；false 走内存路径，CLI --no-dump 覆盖。输入缓存不受该开关取消 |
| `directory` | 字符串／部署默认 | 输出根目录，每次在其下创建唯一运行目录 |
| `name` | 字符串／`run` | 运行名前缀，仅字母、数字、下划线、连字符 |
| `plots` | 布尔／true | 完成汇总后生成独立 PNG，不弹窗 |
| `positions` | 布尔／false | 保存每日持仓明细；期末持仓另有 final_positions |
| `factors` | 布尔／false | 流式模式保存 raw、processed、combined 长表；旧内存路径不提供该导出 |
| `stream` | 布尔／true | dump=true 时按日／批保存并清理缓冲；false 使用旧内存参考路径 |
| `materialize` | 布尔／false | 流式计算结束后显式加载大表为 DataFrame；会增加最终内存峰值 |
| `csv` | 布尔／true | 流式模式额外写 CSV 小表；事件、持仓、因子等大明细保留 Parquet。旧内存导出的 CSV 行为不同 |
| `plot_points` | 正整数／2000 | 长曲线采样目标，非严格点数上限；保留末点，指标仍使用全部数据 |
| `factor_batch_rows` | 正整数／65536 | 因子长表写出批大小；达到行数或一天结束即写出释放 |

默认建议 `dump=true, stream=true, materialize=false`。多配置进程入口要求 dump 和 stream 都开启。批次总目录位于第一个配置的 output.directory，子任务实际目录会改为该批次目录；其他配置的 directory 不作为各自独立输出根。详见[输出说明](OUTPUTS.md)。

## livetest

只有传 `--livetest` 才运行检查，配置段本身不会开启。检查模式不同时执行账户回测。

| 键 | 类型／默认 | 作用 |
|---|---|---|
| `times` | 时间字符串数组／`[]` | f/c 检查决策时点；空时确定性选择，记录到报告 |
| `starts` | 日期数组／`[]` | p 的运行起点；默认自动选 3 个交易日；显式列表须至少 3 个不同交易日 |
| `warmup_days` | 非负整数／省略 | 默认沿用 data.warmup_days；TOML 不写 null |
| `samples` | 整数／3，至少2 | 自动检查时点数 |
| `seed` | 整数／0 | 可复现扰动种子，范围 `[0, 2**32)` |
| `atol` | 非负有限数／1e-10 | 绝对容差 |
| `rtol` | 非负有限数／1e-8 | 相对容差；判断为 abs(test-reference)≤atol+rtol×abs(reference) |
| `max_samples` | 正整数／5 | 报告保存的差异样例数；不是检查时点数 |
| `max_records` | 正整数／1000000 | 每次重放的输出规模限制 |
| `worker_timeout` | 正整数／300 | checkpoint 恢复子进程超时秒数 |
| `report_directory` | 字符串／部署默认 | 检查报告目录；CLI --livetest-report 可指定一个新 JSON 文件 |

CLI 的 times、warmup-days、atol、rtol 覆盖配置。状态、键对齐、NaN 和恢复边界详见 [livetest 说明](LIVETEST.md)。
