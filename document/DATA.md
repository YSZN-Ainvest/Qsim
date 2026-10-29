# 输入数据规范

[返回 README](../README.md) · [配置字典](CONFIGURATION.md) · [输出数据](OUTPUTS.md)

本文默认使用已准备好的共享 NAS 数据。QSim 输入分两层：**行情数据集**提供价格、日历、证券轴和基础交易状态；**外部 PIT 表**提供因子、历史标的池、ST 或中性化暴露。这里说明加载契约，不展开数据下载和来源建设。

## 行情目录与字段

`data.root` 必须遵守以下目录契约。日频只需要 1d；分钟回测同时需要 1d 和所选分钟频率：

```text
<data.root>/
├── securities.parquet
├── calendar/
│   ├── 1d.parquet
│   └── 5m.parquet                 # 按需：1m、5m、30m
├── prices/<frequency>/<YYYY>/<YYYYMMDD>.parquet
├── vwap/<frequency>/<YYYY>/<YYYYMMDD>.parquet
└── universe/all_a/<YYYY>/<YYYYMMDD>.parquet

<data.raw_root>/
└── daily_bar/<YYYY>/<YYYYMMDD>.csv
```

`catalog.json` 可记录上游数据版本和口径；当前行情加载器不依赖它完成普通回测。已有缓存可以包含 returns、labels 等表，但当前加载器不将它们送入因子计算。**含未来收益的 labels 不能混入当期特征。**

| 文件 | 必需列 | 类型与语义 |
|---|---|---|
| securities.parquet | order_book_id, type | 字符串；仅 type=`CS` 进入股票轴，标识唯一，按完整代码排序 |
| calendar/频率.parquet | date, bar_id, bar_start, bar_end | date 为 `YYYY-MM-DD` 字符串；bar_id 为该频率内唯一键；start/end 为带 Asia/Shanghai 时区的时间戳，按交易顺序排列 |
| prices 分区 | order_book_id, bar_id, open_adj, close_adj, bar_complete | 标识与日历键；正的复权价；bar_complete=1 才视为完整 |
| vwap 分区 | order_book_id, bar_id, vwap_raw, vwap_adj, volume, total_turnover, vwap_outside_ohlc | 原始／复权 VWAP、成交量、成交额、质量标记（0 正常）；数值列 |
| universe/all_a 分区 | order_book_id, in_all_a, suspended, st_status | 逐日生命周期资格（布尔）、停牌和 ST 状态（0/1/缺失）。每个证券每天至多一行 |
| raw daily_bar CSV | order_book_id, close, limit_up, limit_down | 原始收盘、涨停价、跌停价；每个证券每天至多一行 |

prices/vwap 的主键为 `(order_book_id, bar_id)`，重复键报错。缺失证券行情重排为 NaN，不用零填价格。日历、池和证券表也应保证无重复和稳定类型；不能依靠部分加载器检查代替输入校验。

分钟日历使用结束时间约定，例如第一根 5 分钟 bar 为 `(09:30,09:35]`，午休不生成交易 bar。选定窗口可以是全日子集；预热和正式运行使用同一窗口。

### 价格、成交量和复权

- 原始价与复权价必须使用同一原始数据和一致复权比例。加载器用“复权日收盘 / 原始日收盘”推导当日比例；即使回测分钟数据也需要当日日频价格。
- VWAP 是 `total_turnover / volume`，量额聚合后相除，不能取各 bar VWAP 的简单平均。volume 与 total_turnover 必须使用相容单位，除法结果应是每股价格。
- `open_adj/close_adj/vwap_adj` 使用相同价格基准；不要把价格复权比例直接乘到成交额或随意改写成交量。QSim 使用复权分数单位模拟，不等同于券商原始股数账本。
- 原始日线 close 用于日终估值／复权比例，不能当作当天 08:00 可用的因子输入。涨跌停和基础状态是当前日级约束，若源数据没有发布时间，QSim 不能证明其历史可用时刻。

### 内存中的形状

每次加载一天后，价格字段的 `DayCube` 形状为 `[1, N, B]`：一天、固定证券轴 N、当日选中的 bar 数 B。`day.bar(j)` 得到 `[N]` 的截面数组；因子历史是 `[K+1,N]`，单个因子输出也是 `[N]`。

这不是把全部回测年份堆成一个大张量。固定证券轴使不同表、不同因子和 checkpoint 可以稳定对齐；每天的有效池仍然可以变化。

## 外部 PIT 表

CSV、Parquet 文件和 Parquet 分区目录均支持。同一源文件必须有稳定 schema；日期列应能解析，推荐 ISO 8601 且显式 `+08:00`。

| 公共列 | 要求 |
|---|---|
| `order_book_id` | 非空字符串，必须与行情证券标识完全一致，保留交易所后缀 |
| `available_at` | 信息真正可用于决策的时刻；非空；无时区时解释为 Asia/Shanghai |
| 业务数值／状态列 | 因用途不同而异，详见下表；不要求外部表事先变成二维数组 |

`(available_at, order_book_id)` 是知识时点键，参与读取范围内重复会报错。各行不必先排序，索引会按时间检索。不要用重复键表示“同一时刻两个版本”，修订必须有新的可用时间。

### 因子表

```csv
order_book_id,available_at,quality,value
000001.XSHE,2024-01-16T07:30:00+08:00,0.32,-0.15
000002.XSHE,2024-01-16T07:30:00+08:00,-0.08,0.41
000001.XSHE,2024-01-16T10:02:00+08:00,0.38,-0.12
```

示意数据，不是行情或收益样本。10:00 仍使用第一条记录；10:05 可以使用 10:02 的更新。判断始终是 `available_at <= decision_time`，不是用日期去匹配“当天因子”。

每个 table alpha 指定 `path` 和 `column`。可以让所有 alpha 指向同一张多列宽表；按实际所需列建立一个共享索引。不要为每个因子复制一份相同数据。

**更新以整行所选列为单位。** 新记录中的 NaN 会替换该列旧值；它不是“本次不更新此列”的标记。各列发布时间不一致时，分别建表，或在每个知识时点构造完整的当时快照，不能填入尚未发布的新值。

最新记录会一直沿用，直到下一次更新。没有自动到期机制；首条记录发布前是缺失，想让因子失效可以写新的 NaN 更新。对于只在盘后才算出的因子，应标记实际盘后时间，而不是简单回填当天开盘。

### 标的池、ST 与中性化暴露

| 用途 | 业务列 | 连接配置 | 缺失／变更行为 |
|---|---|---|---|
| 历史标的池 | `in_universe` | universe.kind=table，universe.path | true 纳入、false 退出；首条记录前不纳入。仍与 all_a 生命周期相交 |
| 历史 ST | `is_st` | operations.st_path | true 表示 ST；未知按 st_unknown=error/exclude/allow 处理 |
| 连续风格 | 例如 `size, beta` | exposures_path、neutralize_styles | 必须数值有限；缺失行不参与该次回归 |
| 分类行业 | 例如 `industry` | exposures_path、neutralize_industries | 字符串或分类值；缺失不能参加回归；不填当前行业倒推历史 |

布尔列只接受 true/false、True/False、0/1 及相应字符串；null 表示未知，不能填字符串 `"null"`。行情内 st_status/suspended 使用数值 0/1/缺失。

一个共享表可以同时包含因子、池、ST 和暴露。实际业务发布时间不同则应拆开，避免误把整张表视为同一时刻发布。

配置历史池时，不要用每次下载“只保留当前成分”的文件覆盖全部历史。对于退出成分，补一条对应 available_at 的 `in_universe=false` 记录。

## 时间、缓存与可复现性

- QSim 在决策前推进 PIT 游标，数值线程只读该时刻的截面；行情因子只观察已经完成的 bar。是否需要 shift，取决于真实可用时间，不能机械地对所有因子都移一格。
- 行情缓存和 PIT 索引按源路径、大小、修改时间或分区清单以及列投影区分版本。源变更产生新缓存，不自动清理旧缓存；完整数据版本管理仍由调用方负责。
- 首次读 CSV 可能扫描整个源文件建立索引，但分块处理，不把全文件长期留在内存；后续查询只推进所需时点和证券。
- livetest 在加载层截断／扰动，发生在特征计算前；它不能回溯重建上游已加工行情，不能证明供应商时间戳和训练产物没有泄漏。
- 运行期间应冻结输入版本。不要边跑边覆盖同一个数据集；保存配置、版本和日志后再更新数据。
