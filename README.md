# QSim

QSim 是面向横截面 alpha 的回测基础设施。你提供行情、因子和运行配置，QSim 按信息可用时间计算信号，完成因子处理、分组、交易模拟和统计，将过程与结果保存为可追溯的文件。

- **输入**：NAS 上准备好的行情数据集，以及带可用时间的因子、标的池、ST 和中性化暴露表。
- **计算**：外部因子、内置价量因子或表达式；截面截尾、中性化、标准化与组合。
- **回测**：日频、1/5/30 分钟；固定时点独立账户或区间连续调仓。
- **验证**：IC、分组与多空表现，以及可选的未来数据、历史起点、checkpoint 一致性检查。
- **输出**：终端进度、日志、Parquet 明细、JSON 汇总和独立 PNG。

QSim 执行研究者给定的因子逻辑，不负责自动寻找因子或划分训练样本。回测与一致性检查通过，不代表因子有预测能力或策略可以盈利。

![QSim — Factor Research & Backtesting Workflow](document/assets/qsim_workflow.svg)

**阅读路线**：[安装与首次运行](#安装与首次运行) → [完整配置](#完整配置示例) → [因子与处理流程](#因子如何进入回测) → [选择回测模式](#选择回测模式) → [查看结果](#查看结果)。

详细说明：[运行指南](document/USAGE.md) · [配置字典](document/CONFIGURATION.md) · [输入数据规范](document/DATA.md) · [输出与排错](document/OUTPUTS.md) · [统计公式](STATS.md)。

## 安装与首次运行

### 1. 安装依赖

使用 **Linux、Python 3.11+**。共享缓存使用文件锁，资源监控读取 Linux 信息；其他操作系统尚未验证。依赖为 NumPy、pandas、PyArrow、Matplotlib、threadpoolctl，安装时自动解析，版本下限见 [pyproject.toml](pyproject.toml)。当前 CI 使用 Python 3.11。

本文默认已挂载共享 NAS，行情已准备好，不涉及数据下载。在你的项目目录克隆仓库并进入 QSim。缓存与结果使用独立存储目录，不混入源码：

```bash
git clone https://github.com/YSZN-Ainvest/Qsim.git
cd Qsim

# 与配置中的缓存／输出根目录保持一致；可以改为自己的 NAS 工作目录。
export QSIM_STORAGE_ROOT=/mnt/nas/qinml_cache/qsim
export TMPDIR="$QSIM_STORAGE_ROOT/tmp"
export XDG_CACHE_HOME="$QSIM_STORAGE_ROOT/cache"
export PIP_CACHE_DIR="$XDG_CACHE_HOME/pip"
export MPLCONFIGDIR="$XDG_CACHE_HOME/matplotlib"
export PYTHONPYCACHEPREFIX="$XDG_CACHE_HOME/pycache"
export QSIM_CACHE_ROOT="$XDG_CACHE_HOME/qsim"
export SQLITE_TMPDIR="$TMPDIR"
mkdir -p "$TMPDIR" "$PIP_CACHE_DIR" "$MPLCONFIGDIR" "$PYTHONPYCACHEPREFIX" "$QSIM_CACHE_ROOT"

export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 ARROW_NUM_THREADS=1 ARROW_IO_THREADS=1

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

已有 Python 环境时，激活它后执行最后一行即可。`python -m qsim` 与安装后的 `qsim` 命令等价。也可使用 [run.sh](run.sh) 自动设置 NAS 缓存与线程变量；通过 `QSIM_PYTHON` 指定解释器，避免选到其他环境：

```bash
QSIM_PYTHON="$PWD/.venv/bin/python" bash run.sh configs/tutorial.toml
```

### 2. 使用现有 NAS 数据运行

先跑 [tutorial.toml](configs/tutorial.toml)：短日期范围、小标的池，开启日志、因子值、持仓和图表输出。它是使用流程示例，不是因子推荐或收益评估结论。

```bash
# 检查配置，并打印合并默认值后的最终配置；此步不读取行情。
python -m qsim --config configs/tutorial.toml --validate

# 使用真正的数据加载器和回测入口运行，并保存结果。
python -m qsim --config configs/tutorial.toml
```

示例显式设为 `st_unknown="allow"`：ST 状态未知时允许通过，并写入审计，不代表已经完成历史 ST 过滤。正式任务应根据自己的输入覆盖选择 `error` 或 `exclude`。

### 3. 改成自己的回测任务

复制 [完整配置模板](configs/tutorial.toml)，按以下顺序修改：

1. 保留或修改共享 NAS 的 `data.root`、`data.raw_root`。前者是 QSim 行情数据集，后者是原始日线；两者格式不同，见[目录与字段](document/DATA.md#行情目录与字段)。
2. 配置 `data.cache_directory`、`output.directory` 和可选的 `livetest.report_directory`。路径不会展开 `$变量` 或 `~`；建议写绝对路径。
3. 指定起止日期、采样频率、回测模式和标的池。预热数据必须位于起点之前。
4. 为每个 alpha 配置来源、数值列或计算逻辑，选择 operations 和合成方式。
5. 先 `--validate`，再用小标的池、短区间运行，检查信号覆盖、日志、输出和可选 livetest，最后扩大规模。

已有 NAS 行情可以直接复用，新增外部因子只需按 [PIT 表规范](document/DATA.md#外部-pit-表)准备列和可用时间。研究任务不要修改共享行情源文件。

## 完整配置示例

TOML 是可读的配置文本：`[section]` 表示配置段，`key = value` 表示参数。下面就是可直接运行的 **tutorial.toml 全文**，没有省略配置块。日期、证券、资金和处理参数是示例选择，可以按任务修改。

| 配置段 | 先决定什么 | 详细说明 |
|---|---|---|
| `data` | 行情、日历、预热和共享缓存 | [数据参数](document/CONFIGURATION.md#data) |
| `backtest`、`stats` | 时间、采样、账户模式、执行窗口和标签期限 | [回测参数](document/CONFIGURATION.md#backtest) |
| `universe` | 静态证券列表或历史时点标的池 | [标的池](document/CONFIGURATION.md#universe) |
| `alphas`、`combine` | 每个因子的定义与组合公式 | [因子参数](document/CONFIGURATION.md#alphasname-与-combine) |
| `operations` | ST、截尾、中性化和标准化 | [处理参数](document/CONFIGURATION.md#operations) |
| `execution` | 成交限制与费用 | [执行参数](document/CONFIGURATION.md#execution) |
| `compute` | 因子线程池、回测进程池和内存预算 | [计算参数](document/CONFIGURATION.md#compute) |
| `output`、`livetest` | 保存内容与可选一致性检查 | [输出参数](document/CONFIGURATION.md#output) |

```toml
# Complete walkthrough using the shared NAS market data.
# Paths are literal: QSim does not expand shell variables inside TOML.
[data]
root = "/mnt/nas/qinml_cache/backtesting/20260930"
raw_root = "/mnt/nas/data/whole_history"
warmup_days = 30
cache_directory = "/mnt/nas/qinml_cache/qsim/cache"
batch_rows = 4096
shared_cache = true
pit_columns = {}

[backtest]
start = "2024-01-02"
end = "2024-01-19"
frequency = "0800"
mode = "StatsEvery"
holding_period = 1
forecast_period = 5
groups = 3
capital = 1000000.0

[stats]
nIntvDelay = 1
idelayEach = 0
ileadEach = 5
isTradeT1 = true
nbar = 1
priceMode = "vwap"
cleareod = false
signal_times = []

[universe]
kind = "symbols"
symbols = ["000001.XSHE", "000002.XSHE", "000004.XSHE", "000005.XSHE", "000006.XSHE", "000007.XSHE"]
path = ""

[alphas.momentum]
kind = "momentum"
lookback = 20
weight = 1.0

[alphas.reversal]
kind = "reversal"
lookback = 5
weight = 1.0

[combine]
expression = "0.5*(<momentum> + <reversal>)"
stage = "processed"

[operations]
exclude_st = true
st_unknown = "allow"
st_path = ""
winsor_mad = 5.0
standardize = true
exposures_path = ""
neutralize_styles = []
neutralize_industries = []
min_cross_section = 3

[execution]
fee_bps = 5.0
sell_tax_bps = 0.0
slippage_bps = 0.0
borrow_bps_annual = 0.0
participation = 0.01
limit_filter = true
suspension_filter = true
reject_bad_vwap = true
short_mode = "synthetic"
enforce_t1 = true

[compute]
pools = 1
batch_pools = 1
memory_per_job_mb = 512

[output]
dump = true
directory = "/mnt/nas/qinml_cache/qsim/result"
name = "tutorial"
plots = true
positions = true
factors = true
stream = true
materialize = false
csv = true
plot_points = 2000
factor_batch_rows = 65536

[livetest]
times = []
starts = []
warmup_days = 30
samples = 3
seed = 17
atol = 1e-10
rtol = 1e-8
max_samples = 5
max_records = 1000000
worker_timeout = 300
report_directory = "/mnt/nas/qinml_cache/qsim/result/livetest"
```

所有公共参数的类型、默认值、作用和模式限制见[完整配置字典](document/CONFIGURATION.md)。上面的数值是教程选择，不等同于所有默认值。`[livetest]` 只配置检查行为；没有 `--livetest` 就不会执行检查。

## 因子如何进入回测

### 外部因子、内置因子与表达式

外部表使用 CSV 或 Parquet；同一张宽表可以装很多因子列。每行代表**某个标的在某个信息可用时点的一次更新**：

| order_book_id | available_at | quality | value |
|---|---|---:|---:|
| 000001.XSHE | 2024-01-16 07:30:00+08:00 | 0.32 | -0.15 |
| 000002.XSHE | 2024-01-16 07:30:00+08:00 | -0.08 | 0.41 |

上表仅说明字段。`order_book_id` 必须与行情证券轴一致；真实数据保留交易所后缀。`available_at` 是信息能被策略知道的时间，不是报告期或收益归属日期。07:30 发布的数据可用于 08:00 信号，08:01 才发布的数据不能用于 08:00。

每个 `[alphas.NAME]` 是独立配置：`kind="table"` 指明表因子，`path` 是来源，`column` 是数值列。相同路径的多个因子共用读取状态，不为每列复制整张表。

也可以使用内置 `momentum`、`reversal`、`volatility`，设置 `lookback`；单位是已完成的采样 bar。派生因子使用 `kind="expression"`。各类型的完整配置和计算定义见[因子参数](document/CONFIGURATION.md#alphasname-与-combine)。

组合支持这样的公式：

```toml
[combine]
expression = "0.25*(<alpha1> + <alpha2> + <alpha3> + <alpha4>)"
stage = "processed"
```

四个名字需分别有 alpha 配置。公式支持有限算术与截面函数，不执行任意 Python；滚动指标通过因子配置表达。未写公式时，按各因子的 `weight` 加总，权重不会自动归一化。

### Operations 的执行顺序

![Operations — Execution Order](document/assets/operations_order.svg)

默认先各自处理，再组合。截尾限制极端值；中性化去除配置的风格和行业线性暴露；标准化让不同尺度的因子可以组合。中性化需要对应历史时点的暴露表，不能用今天的行业回填历史。

参与合成的任何分量缺失，该股票就不进入本次共同有效截面；不会临时对剩余因子重新分配权重。`min_cross_section` 控制最少有效证券数。全截面无可分组信号时如何处理既有账户，见[逐时点流程](document/USAGE.md#一个决策时点发生什么)。

因子、标的池、ST 和暴露的字段、缺失值与更新规则分别见[外部表接口](document/DATA.md#外部-pit-表)。

## 选择回测模式

| 需求 | 模式 | 账户怎样运行 |
|---|---|---|
| 比较“每天 09:30 调仓”和“每天 10:00 调仓” | `StatsEvery` | 两套独立时点账户，每套每天在自己的时点调仓 |
| 同一策略在日内连续更新仓位 | `StatsInterval` | 每个组合沿时间维护一套账户，按区间连续调整 |
| 兼容按批次持有若干 bar 的旧研究方式 | `Cohort` | 各批次到期退出，当前作为兼容模式 |

**StatsEvery 的主结果是等初始资金的时点账户比较组合。** 例如两套账户各有 100 万，分别赚 1 万、亏 0.4 万，主结果对应平均 PnL 0.3 万、收益 0.3%；不是把 PnL 相加后仍除以 100 万。单独时点结果保留在 `slot_daily` 和 `slot_summary`。

日频 `0800` 只有一个时点：08:00 计算信号，当日全天 VWAP 作为成交窗口。分钟 `0930-1450-5` 在选定时段按 5 分钟采样，跳过午休；默认 Every 会形成多个固定时点账户。

Interval 的 `nbar=2` 表示每段最多使用两根采样 bar，并以段起点信号调整目标。两种模式的完整配置、时间线、延迟和 T+1 的区别见[运行指南](document/USAGE.md#选择采样与账户模式)，公式见[统计口径](STATS.md)。

## 按需要运行

在已激活环境、设置好存储与线程变量的项目目录执行：

| 情况 | 命令 |
|---|---|
| 检查配置，不读行情 | `python -m qsim --config configs/tutorial.toml --validate` |
| 普通回测 | `python -m qsim --config configs/tutorial.toml` |
| 同一配置内并行计算因子 | `python -m qsim --config configs/tutorial.toml --factor-pools 2` |
| 多个配置独立回测 | `python -m qsim --config configs/local_a.toml configs/local_b.toml --pools 2` |
| 只做 alpha 一致性检查 | `python -m qsim --config configs/tutorial.toml --livetest all` |
| 小规模调试，不保存回测输出 | `python -m qsim --config configs/tutorial.toml --no-dump` |

多配置前先准备各自的 TOML；`--pools` 是配置任务进程数，`--factor-pools` 是每个任务的因子线程数。实际数量会按资源预算降低，不是设置多少就一定启动多少。账户时间轴和 StatsEvery 时点账户不并行调度。

同一时点的股票用 NumPy 数组向量化计算；因子按依赖层计算并复用相同基础结果；进程通过版本化缓存共享源数据。事件、持仓和因子值按日或批写出，只保留当前状态、滚动窗口和待到期标签。详见[计算与内存](document/USAGE.md#计算与内存)。

`--no-dump` 走内存路径，不适合长任务，且不保存运行日志；输入缓存仍可能创建。生产运行保留 `dump=true, stream=true, materialize=false`。大任务先测小样本，检查可用内存、CPU 与 cgroup 配额，显式限制并发，并用调度器或 cgroup 设置硬限制。

## 查看结果

运行中终端逐日打印进度，结束后显示多空组合的年度与全期统计，以及实际输出路径。详细过程及时写入文本和 JSONL 日志；图表在汇总完成后生成，不会弹出窗口，也没有 dashboard。

![实际运行的终端输出](document/assets/tutorial_terminal.png)

上图是运行本页完整配置后保存的真实 stdout，经浏览器排版截图；没有改写数值。它包含逐日进度、最终摘要和输出路径。为便于复制与查询，字段解释和读取代码见[输出说明](document/OUTPUTS.md)。

每次回测创建一个独立运行目录。下面展示结构；`part-*.parquet` 是数据文件，带 `.parquet` 后缀的上级目录是整个数据集：

```text
<output.directory>/<name>_<timestamp>_<run_id>/
├── config.json / audit.json / manifest.json
├── summary.json / summary.csv / summary.parquet/
├── daily.parquet/           # 账户日净值、PnL、换手
│   ├── part-00000000.parquet
│   └── part-00000001.parquet
├── events.parquet/          # 账户事件与损益归因
├── diagnostics.parquet/     # IC、覆盖、标签状态
├── factors.parquet/         # 可选：原值、处理后值、组合值
├── positions.parquet/       # 可选：每日持仓
├── final_positions.parquet/ # 期末持仓
├── slot_daily.parquet/ / slot_summary.parquet/
├── stats_*.parquet/         # 按模式输出的目标 alpha 参考统计
├── benchmark_daily.parquet/
├── run.log / run.jsonl
├── long_short_nav.png / long_short_pnl.png
├── group_nav.png / group_returns.png / group_excess_returns.png
├── ic.png / turnover.png
└── COMPLETE                # 失败时为 INCOMPLETE，并附 failure.json
```

`daily/summary/events` 是受交易约束、扣除配置费用后的账户结果；`stats_*` 是目标 alpha 的参考统计。两者不能混为同一条净值。输出名称、字段、数值单位、读取方式、失败排查见[输出说明](document/OUTPUTS.md)。

以下图片由同一次 NAS 数据回测直接生成，分别保存在运行目录中；这里只用来展示输出形式，不评价因子效果。

![多空组合净值](document/assets/tutorial_long_short_nav.png)

![分组净值](document/assets/tutorial_group_nav.png)

![分组收益柱状图](document/assets/tutorial_group_returns.png)

完整图集：[金额 PnL、分组超额、IC 与换手](document/OUTPUTS.md#图表怎么看)。

多空图观察净值和回撤；分组图观察不同分数组的表现；分组柱状图检查收益排序是否稳定，而不是要求每次都单调。超额图使用相同有效股票池、费用和执行约束下的等权基准，定义为“组净值 / 基准净值 − 1”。IC 和换手需要结合覆盖率、交易成本一起看。

因子导出为长表，键是 `(timestamp, instrument, alpha_name, stage)`，数值列是 `value`；`stage` 分别为 `raw`、`processed`、`combined`。`output.factors=true` 才保存。普通回测的完整因子导出与 livetest 的组合分数比较是不同用途。

## 验证与边界

`--livetest f` 检查未来输入，`p` 检查预热后改变起点的稳定性，`c` 检查新进程 checkpoint 恢复；`all` 全部执行。只比较同一时间、同一股票的 alpha，全部通过才返回 0。详见 [livetest](document/LIVETEST.md)。它验证 alpha 状态，不是账户断点续跑接口。

当前成交模型是 bar 级模拟：支持停牌、涨跌停、T+1、费用和成交量约束；空头为合成研究仓位，未模拟券源、整手和排队。使用复权分数单位，结束时不强制平仓。外部因子缺更会沿用最新值，尚无自动过期策略。

QSim 不强制划分 IS/OS。数据质量、真实发布时间、历史池和行业暴露由输入方保证；行情预处理和模型训练的未来泄漏不能仅靠回测内检查排除。

## 后续计划

下一阶段与 QD 协调以下数据工作。这里区分“接口已有”与“数据已具备可用于研究的覆盖”，不把接口存在当作数据验证完成。

| 计划 | 当前能力与缺口 | 下一步 |
|---|---|---|
| 历史 ST、停牌状态 | 停牌过滤与 ST 策略已有；历史 ST 尚未完整接入，状态覆盖和真实可用时间仍需核验 | 补齐历史状态与变更时间，验证缺失、停复牌边界及 PIT |
| 中性化风格暴露 | 行业／连续风格中性化接口已有，尚未配置经过完整时点验证的行业、市值等输入 | 与 QD 确定分类与风格定义，核对 NAS 现有数据，接入历史暴露 |
| 中证 500／1000 标的池 | 历史 table 池接口已有，指数历史成分尚未接入 | 接入历史成分变更与生效时间，不用当前成分倒推历史 |
| 官方复权／公司行动 | 已有基于历史昨收推算的复权数据，尚缺官方数据交叉核验 | 对接供应商复权／公司行动，验证分红、拆并股等边界 |
| 数据自动更新 | 当前预处理行情缓存截至 **2026-09-30**，没有完整自动日更流水线 | 建立增量更新、完整性检查、版本记录和失败告警 |

这些是数据完善计划，不表示已有回测和过滤功能不可用；扩展正式研究范围前，应先确认所需输入的历史覆盖。

---

[开发说明](CONTRIBUTING.md) · [版本记录](CHANGELOG.md) · [开发交接](document/QSIM_DEVELOPMENT.md)
