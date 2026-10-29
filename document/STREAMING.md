# 流式、多因子和并行运行（v0.3）

使用 [streaming_demo.toml](../configs/streaming_demo.toml) 可跑通完整输出。示例已使用 2024 年日期，不需要 OS 开关。

## 配置与表达式

TOML 用 `[alphas.alpha1]` 表示独立因子配置；与旧 `[[factors]]` 二选一。

```toml
[compute]
pools = 2                 # 因子共享内存线程池的请求上限
batch_pools = 2           # 多配置回测进程池的请求上限
memory_per_job_mb = 768   # 每个任务的内存预算

[alphas.alpha1]
kind = "momentum"
lookback = 5

[alphas.alpha2]
kind = "reversal"
lookback = 3

[combine]
expression = "0.5*(<alpha1> + <alpha2>)"
stage = "processed"
```

`<alpha1>` 和 `alpha1` 都可作为表达式引用。支持数值、括号、`+ - * / **`，以及 `abs/sqrt/log/exp/minimum/maximum/clip/rank/zscore` 当期截面函数；不执行任意 Python、属性访问、下标或 I/O。不支持表达式内的未来引用或任意时序算子。滚动窗口使用现有 momentum/reversal/volatility 配置。

- 默认先按共同有效股票截面处理每个因子，再合成；中性化仍为可选配置。
- `combine.stage="raw"` 显式使用原始分量。未配置表达式时沿用各因子的 `weight` 加权。
- 配置合成表达式时，因子 `weight` 必须保持 1，把权重写在表达式中，避免重复加权。
- 可定义 `kind="expression"` 的派生因子：其表达式读取依赖因子的**原始值**，按依赖层计算，再像普通因子一样接受处理。未知引用、循环依赖会报错。截面函数只使用当时 eligible 的标的。

## 两种并行

```bash
./run.sh configs/streaming_demo.toml --factor-pools 2
./run.sh configs/streaming_demo.toml configs/simple_factors.toml --pools 2
```

单配置因子池为持久线程池：主线程推进 PIT，工作线程共享只读行情／当期快照，独立因子并行，同层相同基础计算复用。组合和账户更新仍有确定顺序。它不为每根 bar 重建进程，也不并行 StatsEvery 时点账户。

多配置使用 spawn 进程池和有界待执行队列，结果按输入配置顺序汇总。启动前汇总各配置对同一 PIT 文件的列需求，分块建一次共享 SQLite 索引；工作进程只查询自己需要的列和已到时点。行情以版本化 Arrow IPC 缓存共享，进程 mmap 读取，避免反复解析原始 CSV／Parquet。每个任务仍需自己的当前数组和账户状态，不宣称所有内存零复制。

并发按 CPU 空闲、亲和性、可见 cgroup 配额及可用内存的 50% 保守预算收缩，配置值是上限。嵌套的任务池 × 因子池也受预算约束。配额不可见时保守退到 1；资源减少时暂停追加任务，每个运行定期检查 RSS／剩余内存。实际数量和依据写入日志／批次清单。

这些是准入与监控，**不是内存硬限制**。大型任务仍需按工作区规则使用 systemd/cgroup 或调度器限制整组资源。不要通过设置大量 pools 或放开底层 BLAS 线程绕过预算。启动脚本及回测主入口限制底层计算库线程，防止嵌套放大。

## 输入共享和 PIT

CSV、单个 Parquet 文件、Parquet 分区目录均可作为外部表来源，必须包含 `order_book_id, available_at` 和数值列。按所需列分块建立索引，支持源记录无序；每个物理文件在一个模型内只有一个 PIT 状态。不会为 100 个因子复制 100 份宽表。

索引和行情缓存位于 `data.cache_directory`，本机默认 NAS 的 qsim/cache。缓存键包含源路径、大小／时间戳或分区版本以及列投影；输入版本变化使用新缓存，不自动删除历史缓存。首次构建有 I/O 成本，应区分冷／热缓存性能。CSV 首次建索引仍需扫描文件，但不会把整个文件留在内存。

PIT 查询只推进至 `available_at <= decision_time`。checkpoint 保存独立源的当期状态／游标和滚动历史，不复制整张源表；恢复重新打开只读索引。因子线程池不进入 checkpoint，恢复时重新创建。`PITTable.frame` 仅为诊断兼容入口，显式访问会全量物化，生产计算不使用它。

## 输出、日志和内存

```toml
[output]
directory = "/mnt/nas/qinml_cache/qsim/result"
name = "my_run"
stream = true
materialize = false
positions = true
factors = true
factor_batch_rows = 65536
csv = true
plots = true
plot_points = 2000
```

默认流式运行：日内事件和参考统计每天写入并清空；因子值填满批次或结束当天即写入；仅保留滚动状态、当前账户、前一时点分数及尚未到期的标签。年度／全期统计在线累计，IC 的 HAC 统计只留所需滞后缓存。

每次生成独立运行 ID 文件夹。`events.parquet`、`daily.parquet`、`diagnostics.parquet`、`positions.parquet`、`factors.parquet` 等是**Parquet 数据集目录**，内部按批次排序命名。已经完成的分区在失败后仍可读取，不依赖一个长文件最后写 footer。

因子明细键为 `(timestamp, instrument, alpha_name, stage)`，包含 `raw`、`processed`、`combined`；数值列为 `value`。原值保留未过滤信息以便诊断，处理后值及组合值反映实际有效截面。输出中间分量包含零权重依赖，可能很大，默认关闭。

这是长表导出格式。重新作为外部 PIT 因子输入前，需要选定 alpha/stage 并转换为 `order_book_id, available_at, <数值列>`；不要把包含多个阶段和因子的长表直接当作单因子宽表输入。

`summary.json` 使用标准 JSON，NaN/Infinity 转 null；表明细仍保留缺失。`config.json`、`audit.json` 和 `manifest.json` 保存配置、代码／数据版本、执行限制计数、资源峰值、各阶段耗时、分区行数与字节数。

`run.log` 和 `run.jsonl` 在运行中写入：开始／结束／失败、逐日覆盖与进度、阶段耗时、资源及逻辑 I/O 计数、落盘字节数、拒单原因及有限样例。拒单原因可能重叠，不能简单相加作为独立订单数。未引入过期策略，日志明确 `expired_count=null`，不伪报为 0。异常保存堆栈和阶段，写 INCOMPLETE；所有导出完成后才写 COMPLETE。

## Python 返回值

```python
from qsim import run
result = run("/home/qinml/qsim/configs/streaming_demo.toml")
summary = result["summary"]             # 小型 DataFrame
for batch in result["events"].iter_batches():
    process_batch(batch)                 # 由调用方定义
daily = result["daily"].to_pandas()       # 明确请求加载完整表
```

大结果返回 `DiskTable`。兼容的列属性、索引等 pandas 风格访问会按需读取，可能物化整表；大任务优先 `iter_batches()`。`output.materialize=true`、`stream=false`、`dump=false` 保留小样本对照／内存返回方式，不满足长任务的流式内存保证。多配置并行要求 dump 和 stream 均开启。

## 图表与基准

每张图单独 PNG：`long_short_nav`、`long_short_pnl`、`group_nav`、`group_returns`、`group_excess_returns`、`ic`、`turnover`。不再默认输出 dashboard。长时间序列按 plot_points 采样绘图，始末点保留；汇总指标仍使用全部数据，IC 缺失不填零。

流式模式增加独立的等权基准账户：同一时点具备有效因子分数的 eligible 股票、相同资金／费用／执行窗口与交易约束；Every 时点仍按等初始资金均值汇总。分组累计超额为 `组别期末净值 / 基准期末净值 - 1`。暂未提供任意外部指数基准接口；旧内存参考路径不构造该基准，超额图会明确标记不可用。

## 日期和兼容性

不再有强制 2024-01-01 OS 边界、allow_os 门禁、跨研究边界标签屏蔽或固定 IS/OS 统计行。旧 allow_os/os_start 读取时发出弃用提示并忽略；年度和 ALL 统计继续提供。超出回测终点的标签仍为 right_censored，数据实际覆盖范围检查仍有效。

本次更新代码指纹和 checkpoint 结构，旧版本 checkpoint 不能直接恢复。原回测结果目录保持不变。串并行和流式／内存数值对照、PIT/checkpoint 及资源验收记录见 [QSIM_DEVELOPMENT.md](QSIM_DEVELOPMENT.md)。

本机代表性验收：固定 512 股票、16 因子，32／256 天峰值 RSS 均约 209 MiB；100 因子 × 4,096 股票 × 20 时点，双线程约 1.29 倍加速；两个小配置双进程约 1.79 倍。结果一致，不能外推为所有工作负载的加速比。报告同时记录耗时、Parquet 字节量和逻辑 I/O；NFS 物理 I/O 计数可能不反映真实网络读写。
