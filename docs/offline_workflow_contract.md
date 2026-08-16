# Offline 工作流契约

- **状态**：正式 owner
- **适用范围**：offline data、offline training 与 daily-alpha backtest 的配置选择、日程、
  执行编排、运行结果和实验命名。
- **CLI owner**：[`docs/engineering/cli_contract.md`](engineering/cli_contract.md)

## 共同边界

`src/workflows` 只提供三个 workflow composition root：

```python
run_offline_data(
    *, app_config: AppConfig, path_manager: PathManager,
    submission: DataSubmission,
) -> None

run_offline_training(
    *, model_config: ModelConfig, path_manager: PathManager,
    submission: TrainingSubmission, experiment_id: str,
) -> None

run_daily_alpha_backtest(
    *, backtest_config: BacktestConfig, path_manager: PathManager,
    submission: BacktestSubmission, experiment_id: str,
) -> None
```

`src/workflows` 只负责入口校验、实验身份、日程解析和依赖组装，并通过不可变 tuple 显式
声明具体 step 及其顺序。Data 只使用一个 `DataPipeline`，Training 与 Backtest 分别使用
`TrainingPipeline` 和 `BacktestPipeline`。`DataPipeline` 只拥有 workflow 传入的单一 `steps`
tuple 和 Instrumentation 作用域，不知道 data kind、日期 schedule、source、step 分类或具体
step 类型；它必须严格按传入顺序把同一个 Context 交给每个 step，一次 `run` 中每个 step
实例只调用一次。Training 与 Backtest Pipeline 继续拥有各自的 window/timing schedule。
所有 Pipeline 都不得创建、发现、排序或按类型重排 step。

公共编排抽象只允许结构化 `PipelineStep[ContextT]` 协议和 `run_steps(...)` 保序执行器。
`run_steps(...)` 自动以具体 step 类名调用 `Instrumentation.measure(...)`；每个 step 必须
返回同一领域的 Context，业务终止以异常表达。不得建立通用 Pipeline/Workflow 基类、
DAG、step registry、依赖声明、priority 或 before/after 规则。

`src/data_system/steps` 是 offline data 业务行为的唯一实现目录；日期循环、Meta reuse、
缺失与部分可用语义、payload 发布顺序和 lineage 提交都由具体 Step 直接拥有。Broker、
builder 与 normalize 分别保留在 `src/data_system/brokers`、`builders` 与 `normalize`，这些
目录本身就是 Step 调用的具体执行能力，不再外包一层通用 `engines` 目录。跨多个
normalize 模块复用的 Arrow 原语只允许作为 `normalize` 的 private module；不得建立公共
Arrow engine。无状态 normalize 计算直接使用领域具名函数，不建立只提供 `execute()` 或
`resolve()` 的 Engine/Resolver 包装类。不存在 Materializer、Service 或 Manager 转发层。

Normalize 的公共入口按 source family 固定为
`normalize.tushare.normalize_tushare` 与 `normalize.level2.normalize_level2`，共享返回契约
只由 `normalize.NormalizeOutput` 表达。Workflow 直接把这两个 callable 绑定到 broker；
不得建立 `profiles.py`、profile registry 或第二层 profile 名称。Level-2 batch parse、完整
日表 index 和 enrichment 属于同一个 `level2` 模块；只有独立变化的证券代码段规则与
生效日期/成交时段规则分别保留为 `level2_security` 和 `level2_phase`。

### 公共 Step 链执行边界

`run_steps(...)` 是无状态的单段 Step 链执行原语，统一拥有四个不变量：严格使用调用方
给定的顺序；把前一 Step 返回的 Context 交给后一 Step；默认且仅一次测量每次 Step 调用；
原样传播异常。Data 用它执行一次完整 Step 链；Training 分别对每个 window 和最终 Step 链
调用；Backtest 分别对每个 timing 和最终 Step 链调用。各领域 Pipeline 仍拥有 schedule、
Context 创建、跨迭代状态和整个 workflow 的 Instrumentation 作用域，`run_steps(...)` 不拥有
这些生命周期。

该公共规则必须表达为函数而不是公共 Pipeline 基类或执行器对象。单段 Step 链执行没有独立
身份、状态或资源生命周期；把 `Instrumentation` 保存到公共对象并增加 `run_step(...)`，只会
包装 `Instrumentation.measure(...)` 与 `step.run(...)`。让三个领域 Pipeline 继承公共实现，
属于仅为复用循环而继承，并使子类隐式依赖父类状态。让一个通用 Pipeline 同时理解单次执行、
window、timing、final phase 或是否管理 Instrumentation，则需要模式分支并混合不同领域
schedule。因此不得新增 `BasePipeline`、`StepExecutor`、`PipelineRunner`、`StepChain` 或具有
同等职责的包装对象。

若未来不再要求跨领域统一的 Context 串联和默认测量，`run_steps(...)` 应直接删除，并把循环
内联到各具体领域 Pipeline 的 `run()`；不得把执行移入 workflow、Step 或 Instrumentation，
也不得以三个同构 private helper 代替。当前契约仍要求上述公共不变量，因此保留
`run_steps(...)` 是满足语义的最小实现实体。

Data、Training 与 Backtest 各自使用最小领域 Context，只保存相邻 step 必须传递的值；
config、registry、Access、PathManager、Instrumentation 和 experiment identity 绑定到具体
step 或 Pipeline，不进入 Context。Data Context 保存请求的 `start`、`end`，并由 calendar
step 写入后续 step 所需的 `trade_dates`。Backtest Context 每个 timing 新建，Training
Context 的单 window 临时字段在每次迭代前清空。只有确需跨 timing 延续的 backtest 值进入
`BacktestState`。

每个 workflow 必须从收到的同一个 `PathManager` 创建唯一
`Access(pm=path_manager, processed_version="v1")`，组装对应 Pipeline 后调用一次 `run`；公共
入口不接受第二个 `Access`，从而不能把不同 storage root 组合到同一次执行。正式 processed
version 固定为 `v1`，配置和 submission 都不得选择它。

Training 与 backtest 的 experiment name 分别固定为：

```text
training_{start}_{end}_{experiment_id}
backtest_{start}_{end}_{experiment_id}
```

两者共用同一个命名和冲突检查边界。最终 experiment 目录已存在时，在日历读取和执行前
以 `FileExistsError` 失败；该检查不创建、预留、锁定、清理或恢复目录。

Instrumentation 衡量 workflow 显式组装的 step，返回 `step.run(context)` 的原始结果，以
`finally` 累计耗时，并原样传播异常。Step 不接收也不调用 Instrumentation；只有
`run_steps(...)` 负责默认测量。同名 step 累计总耗时、次数和平均值；进入 Instrumentation
作用域后，成功或异常都只输出一次 timeline。准备阶段或空 schedule/timing 在进入作用域前
失败，因此不输出 timeline。现有 step label 固定为：

```text
CalendarMaterializeStep, FactMaterializeStep, FeatureBuildStep, LabelBuildStep,
DatasetBuildStep, PreprocessStep, ModelTrainStep, ICEvaluateStep,
ArtifactPersistStep, SignalStep, SignalEvalStep, TradableAlphaEvalStep,
PortfolioStep, RiskEvalStep, ExecutionEvalStep, AccountingStep,
FullBacktestStep, MetricsPersistStep, ReportStep
```

Workflow 不统一包装异常，不把失败改成空制品或 success，也不重复记录 traceback。

## Data workflow

唯一入口 `run_offline_data` 直接消费已校验的 `DataSubmission(kind, start, end)`。
`kind=data-standard` 固定选择 Tushare Python active manifest；`kind=data-level2` 固定
选择配置中的 enabled Level-2 文件 source。错误 kind 必须在配置解析、日志、
Instrumentation 和 I/O 前失败。完整闭区间是一个 workflow 执行单位。

任何日期 I/O 前必须完成：

1. 从 `TushareBroker._TUSHARE_SOURCES` 解析固定的同名 source/raw/output，并取得唯一
   `trade_calendar`；
2. Standard 选择 manifest 中除 calendar 外的全部 fact source；Level-2 排除配置中 disabled
   文件 source，并选择剩余全部条目；
3. 确认当前 kind 至少有一个 fact source；
4. 绑定固定的 Tushare calendar broker/normalize，并解析所有 fact source 的 broker class
   和固定 broker normalize callable；
5. 选择当前 kind 的 feature 与 label operation；
6. 解析全部被选择的 `(feature_set, version)` 与 `(label_set, version)` builder，并计算每个
   label builder 全部 output column 的最大 lookahead。

Feature 与 label 的支持集只由各自不可变 builder mapping 表达；workflow 不维护第二份
identity allowlist。Standard 当前选择所有 enabled feature 与 label 配置；任一 identity 无法
解析时，必须在 I/O 和 timeline 前失败。Level-2 的 feature 与 label 实现尚未定义，因此
当前选择空 operation 集；对应两个 step 仍各执行一次，但不读写 derived 数据，也不解析
Standard 的 feature 或 label 配置。

Tushare manifest 是受代码审查的执行清单，不通过配置、Broker 反射或 capability provider
动态展开。Level-2 配置则只表达文件 identity、启停和输出映射。除 calendar 外的所选
source 在 workflow 准备阶段转换为完整 `SourceConfig` 后直接绑定到
`FactMaterializeStep`；固定的 Tushare calendar 由 `CalendarMaterializeStep` 按年度对象
直接承担。

Broker adapter 在首次 raw Meta miss 时才构造，并按 broker 在整个 workflow 内缓存一次。
全 Meta hit 不构造 adapter。一次 fetch 仍可拥有自己的网络 session。Normalize、feature
和 label operation 在准备阶段绑定 source/profile/builder；日期循环不得重复解析 capability。

两个 kind 的 workflow 都只显式组装一个线性 step tuple。`CalendarMaterializeStep` 先在自己
的一次 `run` 中按自然年升序复用或物化完整 `[start, end]` 所需的 `trade_calendar` 年度
对象，再通过同一个 Access 把正式交易日写入 Context。随后：

- `FactMaterializeStep` 在自己的一次 `run` 中对每个正式交易日执行所选 fact source 的
  ingest 与 normalize；
- 休市日不执行 fact；只包含休市日的范围成功。

单个日期 ingest 必须尝试全部 selected fact source。已有 raw Meta 与本次成功 payload 都
表示可用；全部无 payload 返回 `False`，全部可用返回 `True`，部分可用必须在尝试完全部
source 后抛 `RuntimeError`。两个 kind 的任一正式交易日全部 fact source 缺失都必须失败；
`FactMaterializeStep` 必须完成范围内所有正式交易日的尝试后，一次报告全部缺失日期。不得
返回 workflow 级 skipped 或跳过缺失日期。只包含休市日、因而没有正式交易日的范围成功。

Calendar 的年度 ingest、raw Meta、normalize 和 lineage 由
`CalendarMaterializeStep` 直接承担；fact 的对应责任由 `FactMaterializeStep` 直接承担。
两个 Step 共享 workflow 的 lazy broker adapter cache。仅当某日全部所选 fact source 可用
时才 normalize；不得拆分独立 ingest/normalize Pipeline Step，也不得引入 Materializer 或
其他转发对象。

所有 fact 日期完成后，一个 `FeatureBuildStep` 才在自己的一次 `run` 中按到达日升序生成
全部 selected feature operation；一个 `LabelBuildStep` 再按相同日期顺序生成全部 selected
label operation。通用 label 规则为：某 label builder 的最大 lookahead 是 `L` 时，Access
返回截至到达日的最近 `L + 1` 个正式交易日；完整 tuple 交给 builder，首日是 label
partition identity。空 operation 集自然不产生数据；Pipeline 不隐式扩大请求范围。

两个 kind 使用同一套 workflow 语义，显式 step 顺序都固定为 calendar materialize → fact
materialize → feature build → label build。差异只存在于 workflow 准备阶段选择的 source、
normalize 与 derived operation 实现。Pipeline 只按 workflow 传入的单一 tuple 执行，不知道
也不校验这些领域顺序。Normalize、feature 或 label 产生零行必须失败。成功对象必须先发布
payload 再提交 Meta；
Meta reuse、lineage、Level-2 symbol slices 与 staging/raw 选择继续由各 producer owner 负责。
Data workflow 保留 started 和 finished 业务日志；其他两个 workflow 不新增 start/done 日志。

## Training workflow

Training runtime 范围只来自 `TrainingSubmission`。当前 model group 固定为
`sgd_regression`。配置中的 feature/label version 继续选择研究制品；processed adjustment
refdata 固定读取 `v1`，其 version 不是配置项。既有 `params.json` schema 仍记录
`adjustment_refdata.version="v1"`。

Label builder 的 `target_lookahead(label_column)` 是 `eval_offset` 的唯一来源。Workflow
通过 Access 读取请求闭区间正式交易日，然后由纯 schedule resolver 产生：

```python
TrainingWindow(train_dates: tuple[str, ...], eval_date: str)
```

- `train_window_days=0` 表示 expanding；
- 正整数 N 表示包含当前 train end 的 N 个正式交易日滚动窗口；
- `eval_index = train_end_index + eval_offset`；
- 历史或未来不足的 window 被排除；
- 最终没有 window 时在 Instrumentation 前抛 `ValueError`。

Schedule 不保存可从 `train_dates` 派生的 start/end/asof，也不重复查询或重新验证 Access
提供的日期。Dataset loader 直接消费完整 `train_dates` 和 `eval_date`，读取 feature、label
及需要的 adjustment Meta/payload，并保留既有 price adjustment、列选择、无穷转缺失、
drop-na 和索引一致性语义。

Workflow 以 `per_window_steps` 显式声明 dataset load → train-only preprocess fit 与 eval
transform → fresh `SGDRegressor` train → Rank IC，并以 `final_steps` 声明 artifact persist
→ report。Preprocess 处理后训练集为空时在该 provider 失败；trainer 只保留一次有限值和
X/y 长度校验。Training step 将每个 IC 写入
`metrics[f"ic@{eval_date}"]`，最终持久化最后一个 window 的 model/preprocess、全部 IC、
既有 params schema，再由 persisted JSON 直接生成报告。公共返回值为 `None`。

## Backtest workflow

Runtime mode、model experiment、strategy、start 和 end 只来自 `BacktestSubmission`。
`BacktestConfig` 只拥有静态 `init_cash` 与 `min_listing_calendar_days`；不得再声明 name、
dates、mode、model 或 strategy。

Workflow 通过 Access 读取正式交易日，并把相邻日期映射为
`BacktestTiming(signal_date, forward_date)`；signal date 同时是 feature date。无 timing 时
在 component 构造和 Instrumentation 前抛 `ValueError`。

Component builder 直接接收 submission 的 mode、model experiment、strategy，以及静态
config 和 `PathManager`。五种 mode 都构造同一套其余 capability、执行完整八层图并持久化
全部 metrics。执行映射固定为：

```text
signal_eval, tradable_alpha_eval, risk_eval -> IdealExecution
execution_eval, full_backtest              -> ExecutionOrchestrator
```

Simulated execution 的 slippage 固定为 `5.0` bp；risk 固定为 `NoOpRiskManager`，均不是
配置项或 fallback。

`BacktestState` 只保存跨 timing 持续的 portfolio、ledger、equity、signal/target tape、五类
evaluation frame、bar/signal count、last mark prices 和 trade dates。它构造后立即可用，不含
path/config/experiment/timing/bar/price/score/target 等单 timing scratch 值。每层从最小
`BacktestContext` 读取前一层结果并写入自己的命名结果；具体 step 明确把需持久的
frame/event/accounting 结果追加到 state，不提供通用 `record_results`。

Workflow 以 `per_timing_steps` 显式声明 signal → signal evaluate → tradable-alpha evaluate
→ portfolio → risk evaluate → execution evaluate → accounting → full-backtest，并以
`final_steps` 声明 metrics persist → report。
初始 cash 只来自静态 config，universe 的 listing age 原样交给 Access。日线执行价格与
phase 语义保持不变。公共返回值为 `None`。

## 失败现场

Artifact persist 或 report 失败时原异常传播，已经创建的 experiment 目录和制品保留为失败
现场；它们不表示成功，也不被本次 workflow 自动清理。后续同名运行仍因目录存在而失败。
