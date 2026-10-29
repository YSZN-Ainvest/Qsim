# QSim 开发交接与迭代记录

本文供后续 AI 开发者接手。它记录当前实现、业务约定、验证证据和待办，不替代源码。每次迭代更新当前状态并追加开发记录，不能只追加历史而保留过时的现状。

## 1. 当前交接点

- 最后核对日期：2026-10-29，上海时区。
- 项目：`/home/qinml/qsim`；Python 包版本 `0.3.0`。
- 仓库：<https://github.com/YSZN-Ainvest/Qsim>；origin 为 `https://github.com/YSZN-Ainvest/Qsim.git`。
- 本文建立前的代码基线：`eb183c1b8c43136af874696c174fed5d8622dcd1`，提交说明 `docs: simplify setup and backtest documentation`。这不是要求未来回退到该提交。
- `v0.2.0` 标签指向初始提交 `f6c5158a00460b6ee69735b57ed5df2605deae10`；后续有文档提交，标签和 main 不必相同。
- 已有可运行的日频、1/5/30 分钟框架；StatsEvery、StatsInterval、Cohort 三种模式。
- 历史验证：30 项构造数据测试通过；日频 Every、5 分钟 Every、5 分钟 Interval 完成真实全 A 数据验证。详情见第 10 节；不是所有模式、频率、全时间范围均完成真实数据验证。
- 最新功能迭代：v0.3 已实现默认流式落盘、共享输入、命名因子／表达式、因子线程池、多配置进程池、完整日志、独立 PNG、JSON 汇总及因子值 dump；已移除强制 OS 门禁和研究分界。71 项回归测试通过，绘图刻度修复后专项复测通过。使用方法见 [STREAMING.md](STREAMING.md)。
- 使用入口已重写为 README 全流程教程：默认现有 NAS 行情，完整配置为 configs/tutorial.toml；新增 USAGE、CONFIGURATION、DATA、OUTPUTS 四份分层说明及真实输出图片。数据与 QD 协调计划列在 README，不把历史停牌接口或推算复权误写为完全不存在。
- README 的 QSim 总流程保留用户指定的竖向灰色卡片；Operations按追加要求改为两行蛇形（向右、向下、向左），引用document/assets的SVG并保留HTML源文件。
- 终端汇总已修复固定10列导致的展示遗漏：按收益、回撤／持仓、交易／成本、因子评价输出完整LS年度与ALL记录，dd_start、dd_end、winrate等沿用原统计值，新增字段自动显示在Additional metrics。
- 最新独立验证：四组实际账户收益、费用、回撤、持仓及 IC 复算一致；89 天输入（含预热）与原始日线、停牌表及已生成复权因子一致。全套 39 项测试通过。参考多空收益仍分别有 30／36／33／32 天缺失，不能当成完整收益曲线。证据见第 10.2 节。
- 五年日频与30分钟示例均已完成、通过落盘统计独立复算，回测及监控进程已退出；README现展示2021–2025年全A池10分组，详情见FIVE_YEAR_EXAMPLES.md。终端指标补全、float64核验与相关测试见最新记录。
- 上一阶段分钟 PIT 复核：5 项边界测试和六组真实小样本 f 检查通过；v0.3 在此基础上完成内存／日志／绘图及并行改造，见第 12 节最新记录。
- 当前维护分支为 `main`，已快进合入 `feature/streaming-pools` 的 aa2da7a，包含此前全部功能和 CI 修复。后续默认在 main 维护和交付，临时分支验证完成后及时合回；旧功能分支保留历史引用。版本验收报告和示例结果均在 NAS，不提交 Git。
- CI 修复：e6b6276 的工作流因 job.env 不支持 runner.temp 在测试启动前被拒绝；bcdf0a4 将缓存变量移至测试 step.env，远端 run 40837614274 全部 71 项通过（34.945 秒，无跳过）。本机测试通过不等于工作流有效。
- Git 留存：用户已授权更新提交并同步 GitHub；最新版统一交付到 main，本轮同步记录见第 12 节末尾。

### 接手顺序

1. 阅读工作区与项目的 `AGENTS.md`、本文，再读取 `README.md`、`STATS.md` 和当前任务相关代码。
2. 检查 `git status --short`、分支和最近提交；保留用户未提交改动，不用 reset/checkout 覆盖。
3. 核实数据目录和 NAS 可读写。只需接手时，不重跑全量扫描、迁移或全历史回测。
4. 将用户本次目标与“现状／缺口／建议”区分开，先完成已授权工作。
5. 修改后执行相关验证，更新本文及受影响的业务文档，检查 diff，再按用户授权提交和推送。

证据优先级：用户最新明确约定 → 当前代码及可复现验证 → 本文当前状态 → 历史记录。发现不一致时先查明并修正文档；不能仅以实现为由悄悄改变用户定义。

## 2. 用户目标与长期约束

用户要一个可持续迭代的多因子研究回测系统，最初参考其前司系统（称 ksim），但明确不要求原样复刻，只需实现业务逻辑。

目标包括：日频和分钟频率、VWAP 成交、分组和多空、可配置资金与费用、标的池、起止时间、持有期和预测期、矩阵计算、可选 dump、年度和全期统计、净值／分组／IC 图。用户最新要求：研究 IS/OS 划分不属于 QSim，v0.3 已移除固定边界、访问门禁及跨界标签屏蔽。最初要求全 A、中证 500、中证 1000；由于缺历史指数成分，用户明确同意先只做全 A。

### 已确认范围及当前落实情况（v0.3）

- 交易近似和现有持有期解释已接受。主线为 StatsEvery／StatsInterval；Cohort 暂留兼容，不作为主要入口继续扩展。本轮不改变 StatsEvery 的时点比较均值口径。
- 内存：事件、持仓、因子值按日／批写 Parquet 后释放；内存主要保留滚动窗口、当前账户和待到期 IC 标签。可在线累计的指标在线算，绘图／复杂汇总读取小型已保存汇总表。外部因子按批、按列读取，同一文件不按因子重复整表加载。
- 性能验收：固定股票数、窗口和账户数量，扩大回测天数，检查内存峰值仍受控；同时记录耗时、吞吐和磁盘读写量，不能仅凭内存降低宣称高效。
- 日志：每次运行独立 ID，保存最终配置、代码／数据版本、生命周期与进度、有效股票／因子覆盖、缺失和过期计数、未成交原因、资源与落盘进度、异常堆栈及时间／标的／阶段。普通日志按日或时间间隔汇总，异常计数加有限样例；文本和 JSONL 及时落盘。
- 绘图：不再默认多面板 dashboard；每次回测独立目录，每张图单独 PNG。优先多空组合净值、分组净值，再输出分组收益柱状图、分组超额收益柱状图、IC／Rank IC、tvr。金额 PnL 与净值单位分开，账户结果与目标 alpha 参考结果明确区分。
- 超额收益：已增加同一有效研究股票池的等权基准账户，采用相同费用、执行窗口和交易约束；相对净值为组别净值／基准净值。汇总指标已增加 JSON。任意外部指数基准尚未接入，不用分组收益均值冒充基准。
- 因子：已增加原值／处理后值／组合值 dump 和受限表达式；默认各因子分别截尾和当期截面标准化后组合，中性化可选，允许原值组合。支持命名 alpha 和依赖 DAG，算子范围见 STREAMING.md。
- 范围边界：财务、分析师原始数据仓库及研究侧训练／拟合预处理不属于 QSim；保留既有当期截面处理。其他缺失数据由用户与 QD 协调后再接入。分钟级 PIT 已复核，后续并行继续通过相同检查。
- 尚待明确：外部因子缺更时的有效期／过期策略未在本轮确认，不擅自改变现有前值沿用行为。

### 目录规则

- 新建代码、脚本、Notebook、配置和代码文档放在 `/home/qinml`，按项目组织。
- 新生成的缓存、结果、dump、日志、模型检查点、临时计算文件、下载缓存全部放在 `/mnt/nas/qinml_cache`。
- 建议后续新任务使用 `/mnt/nas/qinml_cache/qsim/{cache,result,dump,tmp,logs}/`，结果按日期或 run ID 分目录。
- v0.3 默认输出 `/mnt/nas/qinml_cache/qsim/result`；旧 `/mnt/nas/qinml_cache/qsim_runs` 保留可读，没有移动或删除历史结果。
- 根目录必须是 `/mnt/nas/qinml_cache`，不是 `/nas/qinml_cache`。
- 执行前设置 TMPDIR、XDG_CACHE_HOME、PIP_CACHE_DIR、PYTHONPYCACHEPREFIX，以及需要的绘图库／框架缓存目录。测试中的 TemporaryDirectory 也须落在 NAS。
- NAS 不可访问或不可写时报告，不能静默改写本机磁盘。
- 不因新规则批量移动或删除历史文件；需要用户另行指定迁移任务。

### 大型任务资源规则

- 开跑前检查 MemAvailable、CPU 空闲与负载、已有任务、当前 cgroup／调度器配额；GPU 任务还需显存。采用实际可用资源和剩余配额中更严格的一项。
- 本任务和全部子进程合计，目标最多使用启动时空闲内存与 CPU 能力的 50%，60% 为上限；用户给出更低限额时从低。
- 估算输入、复制、临时矩阵、worker 和库线程的峰值；不确定则先小样本试跑，记录峰值后确定批量和并发。
- 显式限制 worker、OpenMP、MKL、OpenBLAS 等线程，禁止未评估的 `n_jobs=-1` 或无界并发。
- 环境支持时设置 cgroup／调度器硬限制并留余量；nice、线程数和估算不是内存硬限制。无法设硬限制则更保守、主动监控，不承诺绝不会 OOM。
- 长任务监控整组进程、系统余量和交换压力；其他任务负载增加时收缩或停止本任务，只终止自己启动的进程。
- 结束或失败后检查子进程退出和资源释放，简要报告实际观测。监控日志和检查点也在 NAS。

### 沟通和版本管理

- 用户偏好直接执行已授权工作，不反复询问确认。出现真实缺失信息或权限障碍时明确说明。
- 普通 README、提交说明和用户文档简洁具体；本文面向 AI，可以详细，但避免套话和重复日志。
- GitHub 是后续迭代记录位置。仓库放代码、配置、文档和测试，不上传行情、原始结果、缓存、个人凭据。2026-10-29 用户明确允许少量文档截图／图表入库，原始运行结果仍留 NAS；此例外仅限已核验的文档素材。
- GitHub CLI 已安装在 `/home/qinml/.local/bin/gh`，曾完成授权及 HTTPS Git 配置。先检查实际访问状态，不默认要求重新安装插件或重新登录；不读取／打印凭据文件。
- 当前仓库可见性为 public（历史核对），不要自行改变可见性。当前本地提交身份为 QSim Automation，不用用户邮件擅自更改作者信息。

## 3. 文件和数据导航

| 路径 | 内容与用途 |
|---|---|
| `/home/qinml/qsim` | 回测程序及 Git 工作树 |
| `/home/qinml/qsim/document/QSIM_DEVELOPMENT.md` | 本文唯一维护版本，随仓库提交 |
| `/home/qinml/document/QSIM_DEVELOPMENT.md` | 指向本文的本机入口软链接，便于用户在新对话提供 |
| `/home/qinml/document/nas_data_inventory.md` | 2026-10-27 原始 NAS 数据盘点，含数据源、字段、缺口和当时状态 |
| `/home/qinml/stock_data` | 数据构建、校验和迁移 Python 脚本，尚未纳入 QSim 仓库 |
| `/mnt/nas/data/whole_history` | 原始历史行情、证券元数据、停牌、财务和风险数据；不要覆盖 |
| `/mnt/nas/qinml_cache/adjustment_factors/backward/20260930` | 由昨收推算的后复权因子 |
| `/mnt/nas/qinml_cache/backtesting/20260930` | 日历、复权价、VWAP、收益、标签、全 A 池和质量报告 |
| `/mnt/nas/qinml_cache/qsim_runs` | 已有真实数据回测结果 |
| `/mnt/nas/qinml_cache/.migration/state.json` | 已完成迁移的历史核验依据 |

原始盘点文档中的“尚未生成复权因子”等句子描述的是 10 月 27 日状态，不能当成当前未完成事项。本文记录其后已完成的构建与迁移。源数据的其他历史缺口也不能因本项目验证通过而推定已修复。

## 4. 数据前置工作：已完成范围

### 4.1 复权因子

用户要求根据日线 `prev_close` 推算并标明前／后复权。现有产物是**后复权**，样本首个有效时点归一为 1：

```text
m[t] = 上一有效原始收盘价 / 当日 prev_close
A[t] = A[t-1] * m[t]
P_adj[t] = P_raw[t] * A[t]
```

历史缺口、停牌和无效记录必须结合构建脚本的处理与质量标志理解，不能将任意断档两侧价格直接累乘。该因子是由参考昨收推算，不是已全量对齐供应商公司行动的官方因子；报价舍入和异常参考价会产生差异。不根据此价格因子直接调整成交量、成交额。

前复权可表示为 `P_raw[t] * A[t] / A[T]`，但本项目没有另行生成前复权数据集。不要把 backward 叫作前复权。

历史生成范围到 **2026-09-30**，用户明确后续再更新。复权历史记录为 1,636 个交易日、7,600 个证券（5,802 股票及 1,798 ETF）、9,454,782 行；110 个极端因子变化被标记。QSim 全 A 证券轴与此统计不是同一口径，不能要求证券数量一致。

### 4.2 回测缓存接口

覆盖 2020-01-02 至 2026-09-30。`catalog.json` 是字段、分区、频率和口径的主要机器可读入口。

```text
calendar/{1d,30m,5m,1m}.parquet
prices/{frequency}/{year}/{yyyymmdd}.parquet
vwap/{frequency}/{year}/{yyyymmdd}.parquet
returns/{frequency}/{year}/{yyyymmdd}.parquet
labels/{frequency}/{year}/{yyyymmdd}.parquet
universe/all_a/{year}/{yyyymmdd}.parquet
securities.parquet
```

- 行情／收益主键为 `order_book_id, bar_id`；标的池为 `order_book_id, date`。证券标识必须保留交易所后缀。
- prices：`open_adj, close_adj, bar_complete, source_rows`。
- vwap 独立存放：`vwap_raw, vwap_adj, volume, total_turnover, vwap_outside_ohlc`。VWAP 为总成交额／总成交量；聚合时对额和量分别求和后相除。
- returns：`ret_oo, ret_cc, ret_vv`，简单收益 `P[t]/P[t-1]-1`。
- labels：`fwd_ret_oo, fwd_ret_cc, fwd_ret_vv`，未来简单收益 `P[t+1]/P[t]-1`。**labels 含未来信息，不能送入当期因子计算。**
- 全日频率网格为日频 1、30 分钟 8、5 分钟 48、1 分钟 240 根；分钟结束时间为 09:31–11:30、13:01–15:00。聚合不跨午休。
- 全 A 池按上市／退市生命周期构造，保留 ST 和停牌股票并单独存状态；历史 ST 未知值不能默认为非 ST。
- 中证 500／1000 已按用户决定跳过，不使用当前成分倒推历史。

历史验证记录：27,812 个分区哈希和行数检查通过，86 个样本日期数值检查并做原始数据抽样对照；不是全量每个数值的独立认证。每类价格／收益表的历史行数分别为日频 8,066,215、30 分钟 64,501,840、5 分钟 387,011,040、1 分钟 1,935,055,200。

已知源数据质量标记包括 3,485 个证券日的分钟缺口，以及 6,835,658 个 1 分钟 VWAP 超出 OHLC 范围。见 `coverage_gaps.csv`、`source_quality.json`、`verification.json`、`source_verification.json`。有标记不等于已经修复，也不应静默填为可成交数据。

### 4.3 NAS 迁移状态

迁移已完成：31,129 文件、141,090,278,993 字节，源与目标 SHA256 回读一致后移除本机数据。Python 脚本仍在 `/home/qinml/stock_data`；该目录中的 README、adjustment_factors、backtesting 已是 NAS 兼容软链接。

不要再次复制这约 131.4 GiB 数据，不删除这些软链接，不将其放进 Git。迁移状态以 `.migration/state.json` 为依据；今天核对状态不等于今天重新执行全量哈希。

## 5. 代码结构与处理顺序

| 文件 | 职责 |
|---|---|
| `qsim/__init__.py`、`cli.py`、`__main__.py` | Python `run(config)` 和 CLI 入口 |
| `config.py` | 默认值、TOML／命名 alpha、类型验证、频率与表达式依赖检查；旧 OS 项弃用并忽略 |
| `data.py` | MarketData、DayCube，按日读取缓存与原始日线 |
| `pit.py`、`pit_store.py`、`source_cache.py` | 按 available_at 查询的分块共享 PIT 索引，版本化 Arrow 行情缓存 |
| `factors.py` | 因子历史窗口、外部因子、加权组合 |
| `operations.py` | MAD 截尾、OLS 中性化、标准化、分组 |
| `runner.py` | 主入口及旧 Cohort 调度；新模式转交 stats_runner |
| `stats_runner.py` | StatsEvery／StatsInterval 时点和区间调度、标签与输出组装 |
| `stats.py` | 目标 alpha 参考公式、VWAP 聚合、时点／区间统计 |
| `engine.py` | 基础账户和旧滚动批次 Cohort |
| `target_engine.py` | 新模式的 TargetLedger，按目标持仓执行约束 |
| `metrics.py` | 日净值、年度／全期统计、相关性、HAC t 值 |
| `run_store.py`、`stream_runner.py`、`online_metrics.py` | 逐日／批次 Parquet、在线统计、日志、资源监控、大结果按需读取 |
| `expressions.py`、`batch.py`、`resources.py` | 受限表达式、受资源预算约束的多配置进程池；因子线程池在 factors.py |
| `report.py` | 独立 PNG、严格 JSON、有限点绘图及旧内存路径导出 |
| `experiments.py`、`tests/test_experiments.py` | 2–5 因子分别及组合串行运行；信号、成交、IC、账户对账检查与因子公式测试 |
| `alpha_events.py` | 普通回测与 livetest 共用的决策时点、信号计划、标的／ST 过滤 |
| `input_policy.py` | 输入文件指纹与按可用时间截断／扰动，先于行情 pivot 和因子处理 |
| `alpha_replay.py` | 生产因子重放、真实 checkpoint、独立进程恢复入口；不重算历史前缀 |
| `alpha_compare.py`、`livetest.py`、`livetest_config.py` | 严格键对齐、三类检查编排、配置、报告与退出码 |
| `scripts/verify_simple_factors.py`、`tests/test_verification.py` | 不导入 QSim 的日频独立输入核对、信号／IC／账户重放及手算测试 |
| `scripts/verify_minute_pit.py`、`tests/test_minute_pit.py` | 三种分钟频率与两种主模式的未来数据复核；午休／隔夜／发布时间／执行延迟边界测试 |
| `tests/test_core.py`、`test_integration.py`、`test_stats.py` | 构造数据与端到端语义测试 |

行情数组按 `[day, security, bar]` 组织，每次 DayCube 的 day 维长度为 1。固定证券轴通过证券表构造，各天有效池可变化；真实验证的全 A 轴含 5,939 标识，不意味着每一天都持有这些股票。

默认流式路径逐日清理事件／参考统计、按批导出因子值，外部 PIT 分块建索引并仅保留当前截面。输出采用 DiskTable，不隐式把全部历史装回内存；显式 to_pandas/materialize 和旧 stream=false/dump=false 路径仍可能占大量内存。固定窗口、股票数、账户数下已做天数扩展验收，不宣称任意规模分钟任务均安全。

因子计算只使用已结束 bar 和 `available_at <= decision_time` 的表数据。先算信号、执行相应窗口，再将完成价格加入因子历史；日频 08:00 不允许使用当日全天收盘来构造信号。

分钟复核确认：5 分钟 09:35 的信号可用截至 09:35 完成的上一根 bar，不能使用本根 09:35–09:40 的收盘；13:00 的最近已完成 bar 来自 11:30。周一 09:30 使用前一交易日的已采样历史。若采样窗口只到 14:50，则滚动历史也只到前一交易日 14:50，不自动补入至 15:00 的数据。当前内置分钟因子无需额外整体 shift；外部因子按真实 available_at 对齐，晚于决策时刻的记录不能提前使用。bar_end 即可用是模型假设，真实发布／计算延迟仍需在输入时间戳或执行安排中体现。

内置因子：momentum、reversal、volatility（负波动方向）。lookback 单位是观察到的采样 bar；warmup_days 单位是交易日。组合先取全部非零权重因子共同有效横截面，再分别做 MAD 截尾、可选风格／行业 OLS 残差化、标准化，最后按配置权重加总。不因某个因子缺失临时改变组合权重。

`FactorModel` 和 `PITTable` 现有 `state_dict/load_state_dict`。alpha checkpoint 保存滚动历史、PIT 状态／游标、Python／NumPy 全局随机状态和事件游标；代码／配置／已读数据变动会拒绝恢复。保存边界为当前 bar 的 compute+observe 之后，恢复执行下一事件。仅用于 alpha 状态，不是完整资金账户 checkpoint；可信本机 pickle 不用于加载第三方不可信文件。

外部因子、ST、标的池和暴露使用 CSV／Parquet，必须带 `order_book_id, available_at`；无时区时间按上海时间解释。相同证券、相同知识时点重复记录报错，最新已知值向后延续。外部池须与全 A 生命周期取交集。财务报告期不等于可用时间，不能直接把季度数据回填到季度起点。

## 6. 交易与统计口径：不可混淆的约定

详细规范见仓库 `STATS.md`。以下保留跨对话最容易误解的部分。用户曾给出 StatsEvery／StatsInterval 的文字定义，原附件位于 `/home/qinml/.codex/attachments/a7a12603-9302-481c-8e01-664b42e7b0c8/已粘贴的文本.txt`。附件路径不是运行依赖，当前落实口径已写入 STATS。

### 6.1 账户结果与参考公式是两层

`daily/summary/events` 是实际约束账户结果；`stats_*` 是目标 alpha 的参考公式。目标权重未必能全部成交，两者允许不同，不能混合解释。

每个分组目标权重之和为 1；最高组做多、最低组做空，LS 两侧各占 1/2。独立组和独立时点配置资金分别用于策略比较，不能把所有组别收益直接相加作为同一个真实账户。

### 6.2 StatsEvery

每天固定在同一个日内时点调仓，每个时点维护独立 TargetLedger 和昨日同一时点 alpha。`0930-1450-5` 是 46 套每日固定时点策略，**不是同一个账户每天连续调仓 46 次**。

```text
hold_return[d,k]  = Σ A[d-1,k] * (P[d,k] / C[d-1] - 1)
trade_return[d,k] = Σ A[d,k]   * (C[d] / P[d,k] - 1)
raw_return = hold_return + trade_return
tvr[d,k] = Σ |A[d,k] - A[d-1,k]|
```

P 为执行窗口后复权 VWAP，C 为后复权日收盘。换手双边计算，不除以 2。

- `nIntvDelay=1`：10:00 信号使用 `(10:00,10:05]` VWAP；2 则用 `(10:05,10:10]`。
- 延迟以所选网格计数，跳过午休；超出当日所选窗口的订单跳过并审计，不挪到次日。日频仅支持 delay=1。
- `isTradeT1=true` 将每日各固定时点目标换手取均值，false 求和；**只影响 stats_tvr，不控制股票 T+1、不影响 PnL／IC／实际交易**。
- `slot_daily/slot_summary` 是单时点账户；主 daily/summary 是等初始资金的时点比较组合，账户换手从实际成交金额计算。
- Every 的 auto_corr 比较相邻交易日相同时点。

### 6.3 StatsInterval

一个 TargetLedger 按有效区间连续调仓。`signal_times=[]` 表示所有采样点，否则只取指定 HHMM。每天将连续有效区间按 nbar 不重叠分段，信号来自段起点；未触发的区间不加入成交价聚合。

- vwap 成交价为分段内 `sum(total_turnover)/sum(volume)` 再乘当天复权比例，不能平均各 bar 的 VWAP。
- priceMode 另支持 open（段首 open）和 close（段末 close）。
- 窗口不跨交易日；尾段不足 nbar 允许截短，但参考报告仍除以配置 nbar，记录 actual_bars。

```text
# vwap / close
holding_return = Σ lastAlpha * (Cend / Cprev - 1)
trading_return = Σ (alpha - lastAlpha) * (Cend / fill - 1)
raw_return = holding_return + trading_return
# open
raw_return = Σ alpha * (Cend / OpenStart - 1)
reported_return = (raw_return - cost_return) / nbar
```

- `/nbar` 只影响参考统计，不缩放实际账户资金损益。
- `cleareod=true` 在每日首次有效计算前清零参考 lastAlpha，**不发平仓订单、不重置账户现金**。
- `stats_sum` 汇总所有段；`stats_sumnoend` 去掉触及所选窗口最后 bar 的整个段。窗口截止 14:50，则末 bar 就是 14:50，不是固定 15:00。
- 参考费用按目标权重变化计算手续费、滑点和卖方附加费；cost_return 保留除以 nbar 前值。借券费只计入实际账户。
- 日频 Interval 目前只支持 nbar=1。

### 6.4 Cohort 与持有期

最初把 StatsEvery 误解为逐 bar 开新批次，后来已按用户定义纠正。旧批次引擎保留为 `mode="Cohort"`；**不能再把 Cohort 当作 StatsEvery**。

只有 Cohort 使用任意 holding_period；Every／Interval 目前要求 holding_period=1。Interval 跨度通过 nbar 指定。用户原始“可设持有期”需求在新模式中尚不是任意多日持有接口，后续如扩展需明确队列、换手和 T+1 语义。

### 6.5 实际账户约束

- `execution.enforce_t1=true` 默认开启；当天新增库存不能当天减少，独立于 isTradeT1。
- 先减仓再加仓，受现金、仓位和成交量上限约束；默认 participation=0.01，限制按独立账本执行，不是所有比较策略合并后的市场容量限制。
- 涨跌停方向约束使用原始价格和原始 limit_up/down；不能把复权价直接与原始限价比较。
- 停牌、未知状态、无效价格、不完整 bar 等按执行逻辑拒单；超出 OHLC 的 VWAP 默认拒绝。
- 未成交目标不等于实际持仓。持仓缺估值使用最后有效价格并记录 stale mark，不把缺失价填 0。
- 空头为合成研究仓位；未建真实券源、召回或融券可得性模型。
- 账户使用可分割的后复权单位，没有整手限制和逐笔公司行动股数账本；不是券商实际股数模拟。
- 不在回测末尾强制平仓。每步核对 `净资产变化 = hold_pnl + trade_pnl - tradecost`。

## 7. 预测、IC 与研究区间

- v0.3 按用户要求移除固定 2024-01-01 边界、allow_os 门禁、IS/OS 统计行及 split_embargo。旧 allow_os/os_start 仅兼容读取并警告忽略，不能重新作为执行限制。
- 研究员在系统外管理训练／验证划分。跨年标签可以正常到期计算；超过请求区间尾部仍为 right_censored、保留 NaN，不额外读取区间外行情。

```text
entry = 信号采样索引 + stats.idelayEach
h = stats.ileadEach（若省略，用 backtest.forecast_period）
label = VWAP_adj[entry+h] / VWAP_adj[entry] - 1
```

idelayEach 默认为 0；预测期按所选采样网格 bar 计数，可跨午休和隔夜。日频 h=5 是 5 个后续交易日采样点；分钟 h=12 是 12 个所选网格 bar，不必等于连续墙钟 60 分钟。它与成交延迟 nIntvDelay、Interval 的 nbar、Cohort 持有期是不同参数。

相关至少需要 3 个有效证券。Interval 对“已到期但 Spearman 未定义”的值记 0 并加 ic_undefined；Every 保留 NaN。尾部截尾仍为 NaN，不得一律填零。

## 8. 配置和输出接口

默认值与严格验证见 `qsim/config.py`，不要复制一份配置实现。关键参数：

| 参数 | 当前约定 |
|---|---|
| data.root / raw_root | 构建缓存根目录／原始行情根目录；两者都依赖 |
| data.warmup_days | 起点前预热交易日，只更新历史不交易 |
| backtest.start / end | 日期两端包含，必须在缓存覆盖内 |
| backtest.frequency | `0800` 日频；`HHMM-HHMM-{1,5,30}` 分钟网格 |
| backtest.mode | StatsEvery 默认，另有 StatsInterval、Cohort |
| backtest.groups / capital | 分组数／独立组合初始资金，默认 10／1000 万 |
| universe.kind | all_a、symbols 或 table |
| execution.fee_bps | 默认 5；5 bps = 0.05%，不能当作 5% |
| sell_tax_bps / slippage_bps / borrow_bps_annual | 可配置，默认均 0；不是历史税费逐日模型 |
| operations.st_unknown | 默认 error；还可 allow／exclude |
| neutralize_styles / neutralize_industries | 用户指定数值风格列／行业分类列，需 exposures_path |
| output.dump / plots / positions | 是否导出、绘图、保存每日持仓 |

`0930-1450-5` 最后一根为 `(14:45,14:50]`，午休跳过，46 根；全日 5 分钟缓存仍为 48 根，不矛盾。

示例：daily_demo 为 2023-01-03 至 03-31 日频 Every；minute_demo 为 2023-12-04 至 08 的 5 分钟 Every；interval_demo 为同一周 Interval nbar=2。示例明确允许未知 ST，只能用于流程和逻辑演示，不代表完整过滤 ST。strict_template 默认缺失 ST 报错。

Python 接口 `from qsim import run`，接受配置字典或 TOML 路径并返回 DataFrame 等结果。CLI `--validate` 只检查配置，`--no-dump` 不保存本次结果；工具自身缓存目录仍需按规则设置。

v0.3 默认 dump+stream 时，summary 保留为小型 DataFrame，大明细返回 DiskTable。优先使用 iter_batches；显式 to_pandas、兼容列／索引访问或 output.materialize=true 可能加载整表。Parquet 结果以同名数据集目录保存，仍可由 pandas.read_parquet 读取。旧 dump=false/stream=false 路径仅作小样本内存参考。

`--livetest f/p/c`、`--livetest f,p` 或 `--livetest all` 显式开启 alpha 检查并替代本次账户运行；不占用短参数。配置 `[livetest]` 本身不启用检查。不带参数的配置结构不新增 livetest 节；未启用时数据干预接口为空，原账户流程保持兼容。CLI 可配置 times、warmup-days、atol、rtol、report；其他选项及覆盖边界见 [LIVETEST.md](LIVETEST.md)。

`./run.sh configs/simple_factors.toml --compare-factors` 运行 20 日动量、5 日反转、20 日低波动三个单因子及等权组合。日频 Every、30 日预热、5 分组、5 日 IC、2023 年一季度，允许 ST 未知值。对比结果在 `/mnt/nas/qinml_cache/qsim/result/`，根目录 `comparison.csv` 汇总各组结果，全部检查通过才写 COMPLETE。通用对比入口接受 2–5 个非零权重因子，组合沿用配置权重。

主要导出：daily、summary、events、diagnostics、final_positions、可选 positions，Every 的 slot_daily／slot_summary，以及 stats_slots、stats_intervals、stats_sum 等模式相关表。配置 config.json、审计 audit.json 记录代码／配置／缓存目录 catalog 的哈希和异常计数。导出成功有 COMPLETE，失败可能有 INCOMPLETE；发现目录不能直接判定成功。

默认分别输出 long_short_nav.png、long_short_pnl.png、group_nav.png、group_returns.png、group_excess_returns.png、ic.png、turnover.png，不再默认 dashboard。指标按年度、ALL 及组别输出，包含：

```text
from to long short return tvr net_tvr sharp dd dd_start dd_end
bp_mrgn winrate nHold traderet holdret tradecost avgdd
net pnl annual_return IC ICIR rank_IC rank_ICIR auto_corr Tstat AHD TC
```

收益／换手为小数、金额为元，sharp 拼写沿用接口。Sharpe 使用日收益及 sqrt(252)；ICIR 不年化。Tstat 为 IC 的 Newey–West t 值；AHD 为退出金额加权持有交易日数；TC 为因子分数与实际 LS 带符号市值的横截面相关。精确定义以 STATS 为准，新增字段不得暗改旧字段单位。

## 9. 运行环境与安全启动方式

已有解释器 `/home/qinml/miniconda3/envs/alpha/bin/python`；依赖 numpy、pandas、pyarrow、matplotlib，Python 3.11+。`run.sh` 优先 QSIM_PYTHON，其次该本机解释器，最后 python3。无需为阅读或改文档重装环境。

以下是本机轻量检查示例；只限制库线程，不构成内存或 CPU 硬配额。**大型真实数据运行仍必须先执行第 2 节资源检查、预算和监控。**

```bash
set -euo pipefail
test -d /mnt/nas/qinml_cache
test -w /mnt/nas/qinml_cache
qsim_runtime=/mnt/nas/qinml_cache/qsim
mkdir -p "$qsim_runtime"/{cache,tmp,logs}
export TMPDIR="$qsim_runtime/tmp"
export XDG_CACHE_HOME="$qsim_runtime/cache"
export PIP_CACHE_DIR="$qsim_runtime/cache/pip"
export PYTHONPYCACHEPREFIX="$qsim_runtime/cache/pycache"
export MPLCONFIGDIR="$qsim_runtime/cache/matplotlib"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export ARROW_NUM_THREADS=1
cd /home/qinml/qsim
/home/qinml/miniconda3/envs/alpha/bin/python -m qsim --config configs/daily_demo.toml --validate
/home/qinml/miniconda3/envs/alpha/bin/python -m unittest discover -s tests -v
```

Arrow 等库是否接受环境变量以及实际线程数要核实，大型任务不能只凭变量宣称全部线程已限制。若需将命令输出保存为日志，写入 NAS 的带时间／run ID 子目录。不要在代码目录重定向生成 `*.log`。

已有 GitHub Actions 使用构造数据跑 unittest，不需要真实 NAS 数据；CI 通过 QSIM_CACHE_ROOT 把输入缓存定向到 runner 临时目录，本机仍使用 NAS。远端 CI 环境与这台服务器分开；不得用远端测试通过替代本机数据接入验证。

## 10. 验证证据与已知限制

### 10.1 历史通过记录（2026-10-28）

本机 `/home/qinml/qsim/verification_stats.json` 记录版本 0.2.0、30 项测试通过与三次真实数据结果；该 JSON 属历史产物，已被 Git 忽略，本次不迁移它。

| 模式 | 范围／规模 | NAS 目录（位于 qsim_runs 下） |
|---|---|---|
| 日频 Every | 59 天、1 个固定时点 | daily_demo_20261028_144829_941831 |
| 5 分钟 Every | 5 天、46 个独立时点 | minute_demo_20261028_144829_818476 |
| 5 分钟 Interval | 5 天、1 个组合时序 | stats_interval_demo_20261028_144822_344513 |

历史检查覆盖：时点独立性、同一时点跨日换手、mean/sum 只影响参考换手、量额加权 VWAP、成交延迟、尾段归一化、cleareod 不平仓、真实 T+1、OS embargo、尾部 censor、现金损益恒等式、净值汇总一致性、输出完整性和代码指纹。

基线 `eb183c1` 的 CI 成功记录：<https://github.com/YSZN-Ainvest/Qsim/actions/runs/40648458893>。后续代码变化必须跑相关新验证，不可将这一历史结果写成新提交的通过结果。

### 10.2 三因子结果独立验证（2026-10-29）

脚本 `scripts/verify_simple_factors.py` 不导入 QSim 因子、行情、交易或统计实现，按保存配置直接读取原始日线、停牌状态、复权因子和缓存；重建过去窗口信号、MAD／标准化／分组、5 日 IC、参考统计及独立现金账户。范围严格限定本次日频 Every、单时点、全 A、三类内置因子、无中性化和附加税／滑点／借券费的配置。

- 30 天预热 + 59 天回测、5,939 证券轴，四组、每组 7 个账户加 LS 汇总。代码／配置／catalog 指纹、CSV／Parquet 数值一致性、日收益、费用、换手、回撤、期末持仓、54 天 IC 和末尾 5 天截尾均通过；41,611 次数值／一致性检查、86 类检查无失败。
- 净值最大复算差异 1.68e-8 元，IC／Rank IC 最大差异 1.39e-16；四组汇总收益差异不超过 1.67e-15。原报告净收益和 IC 无需更正。
- 原始日线和已生成后复权因子与缓存一致，不等于供应商原始数据和复权链已经独立认证。历史 ST、证券生命周期完整性、缺价估值、合成空头和 VWAP 执行近似仍有限制。
- `stats_slots` 的 LS `reported_return` 缺失天数：动量 30、反转 36、低波动 33、组合 32（各 59 天）。这是目标仓位遇到缺价时按既定口径传播 NaN；实际账户依照不能成交／旧价估值规则仍有完整净值。禁止对参考收益简单填 0 或跳过缺失后宣称完整回测。
- 报告：`/mnt/nas/qinml_cache/qsim/result/verification_20261029_independent_01/verification.json`；输入结果目录为 `simple_factors_comparison_20261029_095953_227402`，原结果未改写。复查脚本接收 `--comparison`、`--output`，输出须为 NAS 下的新目录；执行前仍须资源预检、缓存环境、硬限制和监控。

### 10.3 未完成或未保证的内容

1. 历史 ST 未补齐，示例放行未知值；不能宣称已经彻底剔除 ST。
2. 中证 500／1000 历史成分未接入，用户明确暂缓。自定义池接口可用不等于这些池已有数据。
3. 全 A 来自证券快照的上市／退市生命周期；历史证券清单完整性及幸存者偏差未独立认证。
4. 中性化接口已有，但默认没有一套经过 PIT 验证的行业和风格输入。NAS 的风险暴露表是潜在来源，必须检查模型维度、可用时点和覆盖后接入。
5. 昨收推算因子未与供应商官方因子全量对齐；异常跳变未自动解释为公司行动。
6. 数据只生成到 2026-09-30，没有自动日更或增量重建流水线。
7. 空头、成交容量、涨跌停以研究简化模型处理；没有真实融券、盘口排队、整手股数及公司行动账本。
8. 新统计模式没有任意 holding_period；不要为绕过配置错误默默改模式。
9. 默认流式路径已改为有界事件／因子缓冲，外部 PIT 状态按源共享；显式物化和旧内存模式仍受内存限制。未做全历史全 A 1 分钟／任意数量因子的容量认证。
10. 未做因子收益优化或研究参数调优；示例收益不能当成策略有效性结论，研究划分不属于基础设施。
11. `run.sh` 已验证 NAS 检测、缓存／临时目录重定向及数值库和 Arrow 单线程设置；尚不自动做资源预检、硬限制或压力监控。本轮真实回测单独使用 systemd 用户服务硬限制和外部监控，后续执行者仍须落实这些保护。
12. Livetest 当前覆盖分组前组合分数，不覆盖账户状态／目标权重恢复。日历和证券轴固定；缺发布时间的缓存池／停牌／ST 暂按 08:00 可用，不能认证其真实历史可用时间。上游缓存／复权生成、表因子训练、绕过加载器的外部 I/O、自定义 RNG 对象需另行适配；PASS 不代表盈利能力。

可考虑的后续方向（建议，不是当前待执行承诺）：数据 PIT 和 ST 完整性、固定小样本回归基准、资源预检和监控、增量数据更新、长区间分批导出、真实交易约束增强。具体顺序由用户本次目标确定。

## 11. 维护方法与交付要求

每次迭代都完成以下维护：

1. 更新第 1 节核对日期、实现状态和当前阻塞项；有任务中断时写清恢复位置、进程和产物。
2. 修改受影响的架构、参数、数据路径、统计定义与限制。解决的缺口从当前清单移除，在历史记录中保留解决过程。
3. 在第 12 节末尾追加一条记录。未跑测试写“未执行”并说明原因；历史通过、新跑通过、仅检查文件存在三者分开。
4. 修改业务口径同时改 STATS 和相关语义测试；版本变化同步 pyproject.toml、qsim/__init__.py、CHANGELOG。
5. Git 记录写真实提交／分支；不要在提交前猜测本次提交哈希，也不要为把自身哈希写入同一文件反复 amend。可用提交主题和基线定位，下一轮补记已知 hash。
6. 写清 commit、push、CI 分别是什么状态。提交成功不等于推送成功，推送成功不等于 CI 成功。
7. 文档不保存原始结果大表、密钥、访问令牌、个人登录信息；保留可复查路径、摘要和必要公式。

记录模板：

```markdown
### YYYY-MM-DD — 本次目标

- 需求与范围：
- 基线／分支／关联提交：
- 实际改动（文件或模块）：
- 口径决策、兼容性与数据影响：
- 验证（命令、范围、结果；未执行项）：
- 资源与产物（大型运行的预算、峰值、异常、退出状态；NAS 路径）：
- 剩余问题／恢复步骤：
- 提交、推送和 CI 状态（分别记录）：
```

## 12. 开发记录

### 2026-10-27 — 数据盘点

完成 NAS 文件、字段、时间范围和部分缺口盘点；记录日线 prev_close 推算复权的公式与样本。证据为 `/home/qinml/document/nas_data_inventory.md`。该文档保留当时尚未生成数据的历史状态。

### 2026-10-28 — 数据生成与 NAS 迁移

生成截至 2026-09-30 的后复权因子、各频率日历／价格／独立 VWAP／收益／标签、全 A 池和质量报告。中证 500／1000 按用户要求暂缓。验证后将非 Python 数据迁移到 NAS，保留构建脚本和兼容软链接；规模和校验证据见第 4 节。此处由已有记录补录，不代表本次重新执行。

### 2026-10-28 — QSim 初版与统计模式纠正

建立多因子、交易约束、PIT 接口、IS/OS、报告与测试。根据用户进一步提供的定义，将 StatsEvery 改为每日固定时点独立账户，加入 StatsInterval；旧逐 bar 滚动批次改名 Cohort。分开参考 alpha 统计和实际账户，区分 isTradeT1、enforce_t1 与 cleareod。版本 0.2.0；30 项测试和三次真实数据验证通过，见第 10 节。历史运行没有留在本文中的资源峰值，不补造数字。

### 2026-10-28 至 2026-10-29 — GitHub 留存与文档整理

仓库建立并推送 main、v0.2.0 标签；初始提交 f6c5158，文档简化 eb183c1。基线的本地与远端曾核对一致且 CI 通过。只上传代码、配置、文档和测试；数据与运行产物不上传。后续接手仍须读取实时 Git 状态，不能把历史一致性当作永久状态。

### 2026-10-29 — 建立 AI 开发交接文档

- 需求：新对话可读取一份开发记录继续迭代，每次更新维护，入口放在 document。
- 基线：eb183c1；本次提交主题为 `docs: add QSim AI development handoff`，具体提交号通过 Git 历史定位。
- 改动：新增本文、项目 AGENTS 维护要求；README 与 CONTRIBUTING 加入口；在 `/home/qinml/document` 建同名软链接指向仓库版本。
- 核对依据：当前配置、调度／因子／数据入口、STATS、历史验证 JSON、缓存 catalog 与验证记录、迁移状态。未重新执行全量行情检查和回测。
- 验证：文档相对链接、代码块配对、本机入口指向同一文件、三次历史结果的 COMPLETE 标记检查通过；Git diff 空白检查通过。不改变运行逻辑，未重跑单元测试或大型回测。
- 资源：只做小量文件读取与文档编辑；没有启动大型计算和 worker。
- 提交／推送／CI：本节随本次文档提交保存，最终状态以 Git 远端和该提交 CI 为准；不要将基线 CI 成功混写为本次 CI 成功。
- 未完成项：第 10 节所列功能和数据缺口保持原状态，等待用户指定下一迭代目标。
- 并行工作区变化：发现另一轮“三因子对比”实现及其 README／CHANGELOG 改动，见第 1 节；其文档声称 36 项测试及四次真实运行通过，本次没有独立复验，不将其混入本次验证结论。

### 2026-10-29 — 三个简单因子及组合跑通

- 目标：用 2–5 个简单因子验证真实数据流程。复用现有 momentum/reversal/volatility，不改变因子、成交或统计口径。
- 基线／分支：eb183c1 / `feature/simple-factor-smoke`。保留并行文档任务改动，按新增 AGENTS 要求同步本记录。
- 改动：新增 `configs/simple_factors.toml`、`qsim/experiments.py`、6 项测试；CLI 增加 `--compare-factors`；`run.sh` 统一 NAS 缓存路径和线程设置；更新 README、CHANGELOG。
- 兼容性：原 `run(config)` 不变；对比入口要求 2–5 个非零因子。缺信号、无成交、无有效 IC、无效净资产或损益对账失败会报错并保留结果。启动脚本在 NAS 不可用时失败，不回退本机磁盘。
- 验证：原 30 项先通过，新增 6 项通过，最终 `unittest discover -s tests -q` 共 36 项通过；日志 `/mnt/nas/qinml_cache/qsim/logs/tests_20261029.log`。配置校验、编辑器诊断、Shell 语法及 Git 空白检查通过。未重跑其他频率、全历史或真实 OS 数据。
- 真实试跑：先验证 2023-01-03 至 01-13 的 9 天，再跑至 03-31 的 59 天。证券轴 5,939，每天至少 5,006 个有效信号；四组均 59 天成交、54 天有效 IC，末尾 5 天 right_censored，无无信号 bar。最大对账误差约 6.4e-9 元。
- 季度 LS 净收益／Rank IC：动量 −4.3802%／−0.04479；反转 +0.1792%／+0.01120；低波动 +1.7851%／+0.09708；组合 −0.1700%／+0.05105。仅为小样本流程验证。
- 结果：`/mnt/nas/qinml_cache/qsim/result/simple_factors_comparison_20261029_095953_227402/`，含 comparison.csv、四组报告／图表和完成标记。组合仍有 stale_mark_cells=97、limit_blocked_cells=499、unfilled_exit_cells=2864；沿用原估值／成交约束，未修复源数据缺口。
- 资源：预检可用内存约 39 GiB，CPU 有空闲，父 cgroup 无额外限额；串行运行。systemd 硬限制 1 核、2 GiB，MemoryHigh=1.5 GiB、禁止 swap。每 5 秒监控，系统可用内存低于 8 GiB 或任务超过 1.5 GiB 时停止自己的服务。季度运行约 71 秒，cgroup 峰值 341.2 MiB；退出码 0，最终 MainPID=0、服务 inactive，未触发保护。日志 `/mnt/nas/qinml_cache/qsim/logs/20261029_simple_factors/`。
- 异常：首次小样本命令因未安装 `/usr/bin/time` 未启动回测；改用 Python resource 与 systemd 统计后成功，无遗留 worker。
- 剩余项：ST、证券清单和合成空头等限制仍在；未做收益优化，下一步由用户指定。
- 提交／推送／CI：本功能迭代尚未提交或推送，未运行远端 CI；以上均为本机验证。

### 2026-10-29 — 独立核验三因子回测结果

- 需求：接续开发交接文档，验证上一轮结果是否正确。先读项目 AGENTS、当前交接文档、用户提供的只读快照和 STATS；只维护仓库文档及其既有软链接，未编辑附件。
- 基线／分支：745d4f8 / `feature/simple-factor-smoke`，保留上一轮全部未提交改动。
- 改动：新增独立核验脚本及 3 项测试，补充本文、README、CHANGELOG。无生产引擎、因子、统计公式或原结果改动。
- 方法与结果：见第 10.2 节。验证器的多／空首日现金和费用、次日退出与容量限制均有手算用例；另验证数值篡改和 NaN 不一致会被捕获。此 3 项测试已实际通过。
- 最终回归：`unittest discover -s tests -q` 共 39 项通过（16.3 秒），日志 `logs/20261029_verification/tests.log` 位于 NAS 项目目录；新增代码无编辑器诊断，Git 空白检查通过。
- 资源：启动前可用内存约 39 GiB、CPU 空闲约 55%、所查父 cgroup 无额外限制；按 89 天约 50 MiB 数值矩阵加分块解析开销估算低于 700 MiB。使用 1 核、2 GiB 硬限制及 1.5 GiB MemoryHigh，单线程、每 5 秒监控及余量阈值停止保护。运行约 33.5 秒，cgroup 内存峰值 336.2 MiB、无 swap；退出码 0，最终 MainPID=0、inactive，无遗留 worker。
- 证据：报告路径见第 10.2 节；运行日志和资源日志在 `/mnt/nas/qinml_cache/qsim/logs/20261029_verification/`。
- 结论与未完成项：当前模型口径下实际账户收益及 IC 复算正确；目标参考收益有大量按规则保留的缺失。未认证真实可交易性、供应商数据、完整 PIT 状态或其他模式／频率，不据此做收益优化。
- 提交／推送／CI：本次尚未提交、未推送、未触发远端 CI。

### 2026-10-29 — 可选 alpha livetest 与 checkpoint

- 需求：增加 f 未来数据、p 历史起点、c checkpoint 恢复检查；严格对齐同一时点／标的／alpha，不能将无有效比较点判为通过。
- 基线／分支：745d4f8 / `feature/simple-factor-smoke`，保留前两轮未提交修改。读当前项目交接及用户提供的只读快照，未编辑附件。
- 实现：共享决策网格和过滤函数；MarketData／PITTable 新增可选输入干预，在本地特征处理之前截断或扰动可用时间晚于目标的输入。生产因子仍调用原 compute/observe。新增 checkpoint 显式保存状态、输入版本及数据游标；c 默认新进程恢复，逐时点比较后缀，并检查事件不重放、不遗漏。
- 参数与报告：`--livetest` 支持 f/p/c/all 和逗号组合，未启用则不运行检查。默认 3 个确定性时点，p 默认 3 个不同交易日起点；完整预热不足为 INSUFFICIENT_DATA。float64 默认 atol=1e-10、rtol=1e-8；键并集对齐，缺失／重复／无穷值失败，双 NaN 不算有效证据。所有显式检查全部 PASS 才返回 0，其他状态返回 1 并保存 JSON；参数错误为 2。
- 测试：首轮专项 15 项通过；补充分钟输入边界、确定性扰动和缺失输出后，完整 unittest 共 56 项通过（115.9 秒）。覆盖故意读未来、启动日期依赖、漏存历史、随机状态恢复、PIT 状态、跨日／跨周与分钟三模式、真实恢复子进程、版本变化拒绝、全 NaN／缺失／重复／数据不足、CLI 退出码和默认兼容性。日志 `/mnt/nas/qinml_cache/qsim/logs/20261029_livetest/all_01.log`。
- 实际 CLI：`configs/livetest_demo.toml`（6 标的、2023-01-03 至 01-20、14 天、10 天预热）执行 all。选择 01-03、01-11、01-19 的 08:00；p 起点为 01-03、01-06、01-12。f 6 次比较／252 有效点、p 2 次／84 点、c 3 次／126 点，全 PASS、最大绝对误差 0、无缺失键。c 实际保存时间均为当日 15:00，3 个独立 worker，从下一事件继续。
- 报告样例：`/mnt/nas/qinml_cache/qsim/result/livetest_acceptance_20261029/report.json`；同目录 report_state 保存可信 checkpoint、恢复输出和 worker 日志。报告含 99 个输入文件指纹；livetest 自身耗时 32.4 秒。
- 默认兼容性：不带 livetest 重跑此前组合的 59 天季度回测，daily、events、diagnostics、final_positions、slot_daily、stats_slots、stats_tvr、summary 均与改动前保存结果一致（rtol=1e-12、atol=1e-8）。代码指纹因增加功能而变化，未伪造旧指纹。记录在同日志目录的 compatibility.json。
- 资源：预检可用内存约 35.9 GiB，16 核机器采样空闲 48%，所查父 cgroup 无额外限额；已有其他负载，采用 1 核／2 GiB 硬限制、MemoryHigh=1.5 GiB、禁用 swap，每 5 秒监控并在资源阈值触发时停止自己的服务。CLI 验收加兼容性重跑约 59.8 秒，cgroup 峰值 407.6 MiB；正常退出、最终 MainPID=0/inactive，未触发资源保护。
- 兼容性与未覆盖：未更改收益／交易公式，无需改 STATS。当前只验证 combined_score，账户依赖 alpha 返回 UNSUPPORTED；上游数据构建、训练和未知发布时间等限制见第 10.3 节。默认最多 100 万条 alpha 记录，超限返回 UNSUPPORTED；这是规模保护，不替代资源预检。
- 文档：新增 LIVETEST 使用说明与小规模配置，更新 README／CHANGELOG／本文。编辑器诊断和 Git 空白检查通过。
- 提交／推送／CI：本次未提交、未推送，未触发远端 CI。

### 2026-10-29 — 提交并同步 GitHub

- 需求：用户明确要求 Git 提交并同步 GitHub。
- 范围：此前三因子对比、独立验证、livetest／checkpoint 及对应配置、测试和文档；数据、结果、日志及 checkpoint 保留 NAS，不纳入 Git。
- 提交主题：`feat: add factor experiments and alpha livetest checks`；分支 `feature/simple-factor-smoke`，远端 `YSZN-Ainvest/Qsim`。
- 验证：沿用本轮已执行的 56 项通过记录，以及实际 livetest 与季度回测兼容性验证；提交前检查 diff 和文件清单，无生产代码再改动，不重复大型运行。
- 提交／推送：本条作为功能提交的交付记录，完成状态以该提交和远端分支为准，不预填自身提交哈希。
- CI：当前 workflow 只在 main push、PR 或手动 dispatch 时运行，功能分支 push 本身不触发；不得将本地通过记录写成远端 CI 通过。

### 2026-10-29 — 确认后续范围并复核分钟 PIT

- 用户确认：接受交易近似、持有期说明和 TOML 操作；明确内存受控、日志、独立 PNG、JSON 汇总及因子处理方向，Cohort 仅作兼容。训练／拟合预处理不在 QSim 范围。本轮先复核分钟 PIT 并记录下一阶段要求，不提前实施整套改造。
- 基线／分支：96ecf04 / `feature/simple-factor-smoke`。只读检查附带的 livetest_demo 快照；新增测试／复核脚本，生产计算逻辑未改。
- 构造数据验证：`test_minute_pit.py` 的 5 项测试通过（4.04 秒）。逐次检查 1／5／30 分钟信号实际读取的历史窗口与独立时间线完全一致；覆盖午休、跨周末、Every 延迟跳午休及尾部不顺延、Interval 信号先于整个执行窗口、外部 PIT 精确等于时点及晚一微秒／午后发布、14:50 截止窗口的隔夜历史。
- 真实数据复核：6 个既有示例标的，2023-01-06（周五）、01-09（周一），预热 1 个交易日；1／5／30 分钟各运行 Every 和 Interval(nbar=2)。在周五 13:00、末端有效决策和周一 09:30，分别截断／扰动未来输入，共 36 次比较、4,944 个有效配对点，全 PASS，无缺失记录或不一致值，最大绝对误差 0。
- 结论：当前内置分钟价量因子未发现提前读取当前未完成 bar，无需统一再 shift 一格；外部因子应以真实 available_at 为准。不能据此认证上游输入时间戳真实性或真实行情／计算延迟，日级池／停牌状态发布时间限制继续保留。没有开展研究侧训练预处理检查。
- 证据：`/mnt/nas/qinml_cache/qsim/result/minute_pit_20261029_01/summary.json` 与六个子目录报告；日志 `/mnt/nas/qinml_cache/qsim/logs/minute_pit_20261029/`。此前全套 56 项通过属于历史记录，本轮只执行新增 5 项及上述真实数据复核，不冒称重跑全部 61 项。
- 资源：启动前 MemAvailable 约 35.6 GiB、CPU 空闲采样 91%、所查父 cgroup 无额外限制；估算小股票池、完整分钟日历及 Parquet 解析峰值低于 800 MiB。串行、库单线程、1 核／2 GiB 硬限制、MemoryHigh=1.5 GiB、无 swap，每 5 秒监控；实跑约 19.3 秒，cgroup 峰值 326.5 MiB，正常退出、MainPID=0/inactive，无遗留 worker。
- 文件归属：修改前核实交接文档和工作目录属 qinml；结果只写 NAS 新目录，未覆盖历史报告；测试生成的少量新输入留在 NAS 临时目录，未清理服务器其他内容。
- 后续：第 2 节已确认范围仍待实施；外部因子过期策略仍待确认。用户已要求本轮完成后 Git 提交并推送；提交主题 `test: verify minute PIT boundaries and record agreed roadmap`，实际同步以该提交与远端分支为准。未触发远端 CI。

### 2026-10-29 — v0.3 流式、共享输入与并行执行

- 需求：用户明确要求实施此前六项待办，增加命名 alpha 的 pools 和多配置并行，解决同源重复读／复制，保持串并行及 PIT/checkpoint 一致，并删除基础设施内的强制 OS 划分。
- 基线／分支：24eb75e / `feature/streaming-pools`。版本由 0.2.0 升至 0.3.0。生产交易算法只增加诊断计数；新流式调度复用既有因子、账户与参考统计实现。
- 输入与计算：CSV／Parquet 文件或分区目录按列、分块建共享 PIT SQLite 索引；一个物理源一个当期状态。批次预先合并列需求，进程只查所需列。行情通过版本化 Arrow mmap 缓存复用。因子线程池按依赖层共享只读快照并复用相同基础计算；多配置为 spawn 进程池、有界队列、限制嵌套并发、动态准入。时点账户及账户时间轴保持顺序。
- 表达式：支持 `[alphas.NAME]`、`<NAME>` 引用和安全 AST 点运算／有限截面函数；禁止任意 Python、属性访问、下标与循环依赖。合成表达式引用处理前或处理后分量可配置；表达式内写权重时要求各 factor.weight=1。巨大常量幂转换为受控 NumPy 浮点运算，非有限输出为 NaN。
- 存储与日志：默认逐日写事件／持仓／诊断／参考统计并清缓冲，因子三阶段值按 batch 写；在线维护年度／ALL 指标及 HAC 所需滞后。大结果返回 DiskTable。run.log／run.jsonl 记录阶段、异常、覆盖、拒单原因样例、资源和写入计数；失败保留已完成分区和 INCOMPLETE。标准 JSON 汇总将非有限值写 null。
- 图表与基准：单独输出多空净值／金额 PnL、分组净值／收益／超额、IC 和换手图。增加同有效研究股票池、同执行／费用约束的独立等权基准账户。长曲线有限点绘制，指标仍使用全量。目视发现并修复分类轴重复刻度，图表专项测试及真实示例重绘通过。
- 研究边界：删除固定 2024-01-01 gate、split_embargo 与固定 IS/OS 统计行；兼容读取旧 allow_os/os_start 并忽略。保留真实数据覆盖检查与 right_censored。研究划分由研究员管理，未加入训练／拟合逻辑。
- 测试：最终完整 unittest 71 项通过（191.6 秒，all_03.log）；随后只修改绘图刻度格式，相关专项再通过（plots_01.log）。覆盖流式／内存三模式数值对照、100 列一次读取及有界 PIT 状态、表达式安全／DAG／串并行一致、真实双进程共享投影、分区 PIT 的 f/c、故障日志／残留分区、缓冲随天数稳定、跨年无门禁和独立 PNG。生产源文件无编辑器诊断、Git 空白检查通过。
- 内存／I/O 验收：独立进程、512 股票、16 因子，32 天峰值 RSS 209.37 MiB、耗时 7.79 秒、Parquet 7.89 MiB；256 天 209.36 MiB、59.29 秒、62.94 MiB。两者事件缓冲峰值 26、待到期标签 5、因子缓冲 16,896 行相同。报告还包含进程逻辑读写计数；NFS 的物理 I/O 计数可能为 0，不代表没有读取。
- 并行验收：4,096 股票、100 因子、20 个计算时点，1 线程 2.402 秒、2 线程 1.865 秒，约 1.29 倍；完整输出 SHA256 一致。两个真实数据配置暖缓存下，串行 7.110 秒、双进程 3.962 秒，约 1.79 倍；summary 逐项一致。均为指定样本观测，不承诺任意任务同等加速。
- 真实数据：新 streaming_demo 使用 2024-01-02 至 01-19、6 个标的，无 OS opt-in；表达式／并行 f/p/c 全 PASS，c 创建独立恢复进程。流式结果与串行内存参考逐表一致；真实 `--config A B --pools 2` CLI 运行成功、effective pools=2。
- 证据：性能总报告 `/mnt/nas/qinml_cache/qsim/result/streaming_acceptance_20261029_01/benchmark_summary.json`；最终 PIT／数值复验 `/mnt/nas/qinml_cache/qsim/result/streaming_acceptance_20261029_02/`；最终图表示例 `/mnt/nas/qinml_cache/qsim/result/streaming_demo_20261029_152242_386477_6aa0a73e/`。日志 `/mnt/nas/qinml_cache/qsim/logs/streaming_upgrade_20261029/`。
- 资源：预检可用内存约 33–41 GiB、CPU 空闲约 95%，已查父 cgroup 无更低限额。整组硬上限最多 6 核／4 GiB、MemoryHigh=3 GiB、禁用 swap，内部按空闲资源和配额的 50% 收缩并发，每 5 秒外部监控。首轮全回归＋性能约 308.6 秒、cgroup 峰值 519.1 MiB；最终复验约 232.3 秒、峰值 391.3 MiB。均正常退出，MainPID=0/inactive；后续小型绘图复查亦已结束。
- 兼容与限制：Parquet 文件变为可直接读取的分区目录，大结果默认返回方式改变；提供显式物化／旧内存路径。旧 checkpoint 因代码／结构变化需重建。默认等权基准已实现，任意外部指数基准、完整账户恢复、外部因子过期策略、GPU／分布式和全历史全 A 分钟容量认证尚未覆盖。原有 ST、券源与数据质量限制继续保留。
- 提交／推送／CI：按已授权的 Git 留存流程，提交主题 `feat: add streaming backtests and controlled factor pools`，推送 feature/streaming-pools；不把本地验证写成远端 CI，通过状态以 Git 记录为准。

### 2026-10-29 — 修复 GitHub Actions 上下文错误

- 依据：用户截图及远端 run 40836549161。e6b6276 在工作流解析阶段失败，未启动测试；不是 71 项本地测试的执行失败。
- 原因与改动：GitHub 的 jobs.<job_id>.env 不支持 runner 上下文；将 QSIM_CACHE_ROOT 的 runner.temp 表达式移至测试步骤 env。依据官方 contexts 的 Context availability 表；保留 NAS 无关的 CI 临时缓存和原触发条件，不改回测算法。
- 验证：检查差异与官方上下文范围，Git 空白检查通过。推送后使用 workflow_dispatch 主动验证当前分支；该远端结果另行追加，不预填成功。此前“功能分支 push 不触发”的说明不适用于无效工作流解析错误。
- 范围：只修改 CI 与交接文档；只读附件未修改，未启动本机大型计算。提交主题 `fix: scope runner cache path to test step`。
- 远端复验：修复提交 bcdf0a450b867c7f3ad1736047b007b840a5512f 已推送；[workflow_dispatch run 40837614274](https://github.com/YSZN-Ainvest/Qsim/actions/runs/40837614274) 成功，安装与测试步骤均成功，71 项测试用时 34.945 秒、无跳过。后续仅更新本条验证记录，不修改已验证的工作流或生产代码。

### 2026-10-29 — 最新版统一到 main

- 用户要求减少分支、在 main 维护最新版。核实工作区干净、远端 main 为 eb183c1 且为功能分支祖先后，将本机 main 快进到 aa2da7a，无冲突、无历史改写。
- 更新 AGENTS 与当前交接点，明确后续默认 main；保留旧分支，不删除历史引用。仅新增维护约定，无生产代码或测试变动。
- 验证沿用已通过的远端 71 项测试；本次 main 推送将自动触发 CI，实际状态以对应提交的 GitHub Actions 记录为准，不预填成功。无需再次启动本机回测或读取 NAS 业务数据。

### 2026-10-29 — README 全流程与参数文档

- 需求：保持干练文风与超链接，重写项目定位、工作流、安装／运行场景、完整配置、因子与 operations、Every／Interval、输入形状、并行／内存、dump 和真实输出图片；移除“三因子试跑”及个人验收叙事。用户随后确认默认共用 NAS 数据、不展开数据来源，并允许文档图片入库；追加与 QD 协调的数据计划。
- 改动：README 增加两幅 Mermaid 流程图、完整 tutorial.toml、真实终端输出截图及图表。新增 document/USAGE.md、CONFIGURATION.md、DATA.md、OUTPUTS.md 和图片来源说明；只为 document/assets/tutorial_*.png 放开忽略规则。保持生产计算代码与统计规则不变。
- 准确性：逐项核对配置默认值／约束、PIT 更新、处理顺序、账户均值、标签、实际输出与批次目录规则。计划明确历史 ST／状态时点、行业风格、指数成分、官方复权交叉验证和自动更新；不把已有停牌控制和推算复权写成未实现。
- 示例：configs/tutorial.toml 的真实日频 Every，2024-01-02 至 01-19，6 标的、30 个交易日预热，14 天均有成交、9 天有效 IC，no_signal_bars=0，损益对账最大误差约 2.25e-10。运行正常完成，结果 `/mnt/nas/qinml_cache/qsim/result/tutorial_20261029_163103_153463_fc34a258/`；stdout 在 `/mnt/nas/qinml_cache/qsim/logs/readme_20261029/tutorial_01.log`。
- 文档验证：6 份用户说明的 76 个本地链接／锚点、27 个代码块语法通过；README TOML 与模板逐字一致、与实际 config.json 相同；JSON 摘录匹配实际 summary，7 张 PNG 与源文件逐字节一致。终端图来自真实 stdout 的浏览器排版截图，明确注明形式。验证报告 `/mnt/nas/qinml_cache/qsim/tmp/readme_20261029/doc_validation.json`。
- 命令复验：同一 tutorial.toml 的 --validate 成功，--livetest all 的 f/p/c 分别6／2／3次比较全部PASS，报告 `/mnt/nas/qinml_cache/qsim/result/livetest/livetest_20261029_163714_105088/report.json`。配置校验与重放整组用时63.589秒、CPU17.277秒、systemd峰值217.0 MiB、swap为0；正常退出。此次未修改生产逻辑，不重复本机71项全套测试；main推送自动CI另行核实。
- 资源：启动前可用内存约 39–40 GiB、CPU 采样空闲约89%，已查用户及父 cgroup 无更低限额。小样本估算小于512 MiB，整组设1核／1 GiB硬限制、MemoryHigh=768 MiB、禁swap。示例耗时26.035秒、CPU4.450秒；systemd报告峰值117.5 MiB，进程ru_maxrss约264.2 MiB（统计口径不同，不混用）。结束MainPID=0、inactive，无遗留worker。截图服务启动失败（端口占用）未留下本次服务，改用浏览器内存页面捕获，不动他人服务。
- 范围与留存：附件只读未修改，原始数据／结果未迁移或删除；初拟的合成数据教程已按用户“默认NAS”要求撤回，仅删除本轮新建且未提交的教程生成器。用户未要求重写的旧业务文档保持原结构。本轮按已有授权提交至 main，不预填远端 CI 成功。

### 2026-10-29 — 替换 README 两张流程图

- 按用户提供的 QSim Workflow、Operations Execution Order HTML 文案和样式，替换 README 两处 Mermaid。HTML 保存至 document/assets/qsim_workflow.html、operations_order.html；按浏览器实际布局导出同名 SVG，README 直接引用，避免依赖内嵌 HTML/CSS 渲染。
- 保留竖向灰底圆角卡片、箭头、PIT 侧向输入和账户／评价双分支。SVG 仅包含矩形与文字，无脚本、外部资源或 foreignObject；HTML 的每段可见文字与 SVG 去空白比对一致，浏览器预览与 XML 解析检查通过。
- 本次仅改文档和图形素材，未修改回测逻辑，不启动本机测试或回测；没有本轮计算 worker。原始结果未改动，按既有授权同步 main，远端 CI 状态以对应提交为准。

### 2026-10-29 — 终端指标补全与五年示例

- 用户要求：README 详细说明按bar网格决定的Every账户与Interval连续账户；展示至少4–5年、日频及分钟、5／10组的分层结果；livetest独立并列解释f/p/c。随后要求先核对终端遗漏的dd_start、dd_end、winrate。
- 已确认：summary.json／在线统计已有这些字段，CLI固定选择10列导致隐藏。新增qsim/terminal.py分块显示原始LS完整汇总，CLI调用它，不重算或改变指标；未知新增列也不丢弃。新增2项终端测试和既有CLI／livetest兼容测试实际通过。
- 五年配置：2021-01-01至2025-12-31，历史全A池、10组；日频与30分钟均Every，分钟全日8bar对应8套时点策略。扫描日历与分区确认1212个交易日、30日预热，无所需分区缺失（不等同于逐值完整性）。关闭长期因子和每日持仓dump，保留账户／参考统计／诊断和图表。短段10日试跑正常，进程峰值约299／361MiB。
- 已完成任务：qsim-readme-daily-5y.service、qsim-readme-minute-5y.service。运行目录分别为 `/mnt/nas/qinml_cache/qsim/result/readme_daily_5y_20261029_171342_503890_f9ae4c10/`、`readme_30m_5y_20261029_171352_496764_b43399ef/`（同结果根目录）；两者均有COMPLETE。
- 日志与监控：`/mnt/nas/qinml_cache/qsim/logs/readme_5y_20261029/`，daily.log、minute.log、resources_relaxed.log。先按1核／2GiB试跑；用户随后明确允许合计500%CPU和启动前空闲内存60%。重新核查约36GiB可用后，两个unit各250%CPU、MemoryHigh=4GiB、MemoryMax=10GiB、禁swap，合计20GiB低于60%上限；每5秒监控，系统余量不足8GiB或整组超过当前空闲60%时只停本轮任务。旧监控仅终止自身PID833436并替换，新监控继续运行，回测没有重启。
- 终端修复提交23f091a已推送main、远端CI通过；后续用户确认总CPU可至500%、内存可至空闲60%，并要求核实32／64位精度及Operations图转弯布局，均纳入本轮。
- 完整结果：日频／分钟各1212个交易日、固定证券轴5939、最少有效分数4073、10分组；分钟8个时点账户。LS全期净收益分别−36.0998%／−56.9380%，如实保留亏损，不改符号或截取年份。日频1207条有效标签、5条右删失；分钟9656条有效标签、40条右删失。
- 独立复算：各78条组合／年度／ALL的收益、PnL、回撤及峰谷日期、胜率、换手、费用与日表一致；从slot_daily独立重建主账户均值。逐事件最大对账误差分别约5.42e-9／6.12e-9元。报告在日志目录verified_daily.json、verified_30m.json。未将账户对账当作源数据或逐笔可交易性认证，也未在五年全市场重放超预算livetest。
- 资源结果：整组实际耗时13分48秒／24分13秒，进程峰值RSS约406.8／450.3MiB；cgroup峰值约2.1／4.0GiB含页缓存，和RSS口径不同。Parquet写出约83.5／204.3MiB；没有swap，未触发资源保护。两个unit均MainPID=0、inactive，监控正常退出。
- 精度核验：实际行情、因子原值／处理后／组合、现金／持仓／净资产均float64；两份实际落盘数值为Parquet double，Python浮点有效尾数53位。新增test_precision用float32会折叠的1e-9信号差异和1亿元中的分币差异，验证加载、计算和导出保持双精度；本机专项通过。precision_audit.json保存运行证据。没有为了验收而改动核心计算dtype。
- 文档和素材：新增两份五年完整配置与FIVE_YEAR_EXAMPLES，快速tutorial改为全A／10组；README直接详解Every／Interval、独立f/p/c、完整终端与float64。18张长样本素材为14张原始PNG及4张真实stdout分块截图，源结果留NAS。Operations保留八步文案并加编号，按两行右→下→左导出1008×511 SVG，文本和方向检查通过；总流程图不改。
- 最终本机核对：新版快速tutorial亦实跑成功（23.083秒），结果为 `/mnt/nas/qinml_cache/qsim/result/tutorial_20261029_174133_215780_86261b3e/`；完成后无本轮遗留worker。7份说明的108个本地链接／锚点、30个代码块检查通过；README全文配置与实际快速配置一致，两份长配置与保存快照一致，JSON摘录及全部原始PNG逐值／逐字节匹配。报告为日志目录documentation_validation.json。此次增加1项精度回归，最终远端全套结果以本轮提交CI为准。
