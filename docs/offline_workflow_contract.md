# Offline 工作流契约

- **状态**：正式 owner
- **适用范围**：offline data、offline training 与 daily-alpha backtest 的配置选择、日程、
  calendar bootstrap、执行编排、运行结果和实验命名。
- **CLI owner**：[`docs/engineering/cli_contract.md`](engineering/cli_contract.md)

## 共同边界

`src/workflows` 只提供六个 workflow composition root：

```python
run_trade_calendar_bootstrap(
    *, app_config: AppConfig, path_manager: PathManager,
    as_of_date: str,
) -> None

run_offline_data(
    *, app_config: AppConfig, path_manager: PathManager,
    submission: DataSubmission,
) -> None

run_standard_fact_bootstrap(
    *, app_config: AppConfig, path_manager: PathManager,
    submission: StandardFactBootstrapSubmission,
) -> None

run_feature_backfill(
    *, path_manager: PathManager,
    submission: FeatureBackfillSubmission,
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
`run_steps(...)` 自动以具体 step 类名调用 `Instrumentation.measure(...)`；该公共调用形式
由 [`docs/engineering/technology_stack_decisions.md`](engineering/technology_stack_decisions.md#instrumentation-公共调用形式)
拥有。每个 step 必须返回同一领域的 Context，业务终止以异常表达。不得建立通用
Pipeline/Workflow 基类、DAG、step registry、依赖声明、priority 或 before/after 规则。

`src/data_system/steps` 是 offline data 业务行为的唯一实现目录；日期循环和 operation
调度由具体 Step 拥有。Feature 与 label Step 共用一个 private derived-partition 发布函数，
该函数唯一拥有 Meta reuse、非空检查、payload 先于 Meta 的发布顺序；它不拥有日期、
dataset identity、日志或计算。Broker、builder 与 normalize 分别保留在
`src/data_system/brokers`、`builders` 与 `normalize`，这些
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
`finally` 累计耗时和次数，并原样传播异常。Step 不接收也不调用 Instrumentation；只有
`run_steps(...)` 负责默认测量。同名 step 累计总耗时、次数和平均值；离开 Instrumentation
作用域时只输出一个多行、等宽对齐的 Pipeline timeline 日志事件，其中依次列出每个 step
的总耗时，再列出 Total；同名 step 在同一作用域内实际执行多次时，该行才额外列出平均
耗时和 runs。正常退出以 `✅` 和 `INFO` 开头，异常退出以 `❌` 和 `ERROR` 开头；续行不重复
时间、level、源码位置或状态符号。准备阶段或空 schedule/timing 在进入作用域前失败，
因此不输出 timeline。现有 step label 固定为：

```text
CalendarMaterializeStep, FactMaterializeStep, FeatureBuildStep, LabelBuildStep,
DatasetBuildStep, PreprocessStep, ModelTrainStep, ICEvaluateStep,
ArtifactPersistStep, SignalStep, SignalEvalStep, TradableAlphaEvalStep,
PortfolioStep, RiskEvalStep, ExecutionEvalStep, AccountingStep,
FullBacktestStep, MetricsPersistStep, ReportStep
```

Workflow 不统一包装异常，不把失败改成空制品或 success，也不重复记录 traceback。

## Calendar bootstrap workflow

`run_trade_calendar_bootstrap` 是 CLI-only `data-calendar` 的唯一 workflow。CLI 在 composition
root 读取一次 Asia/Shanghai 当前日期，并以显式 `as_of_date` 传入；workflow 不读取当前
时间。`as_of_date` 必须是规范系统日期。Bootstrap 范围固定为：

```text
start = 2016-01-01
end = <as_of_date 所在年份>-12-31
```

Workflow 从收到的 `PathManager` 创建唯一 `Access`，只组装一个
`CalendarMaterializeStep` 的不可变 step tuple，并调用一次 `DataPipeline.run()`。该 Step
按自然年升序复用或物化范围内每个完整年度的 raw 与 processed `trade_calendar`，并通过
同一个 Access 终验完整范围。Bootstrap 不选择或执行 fact source、feature、label、Level-2
或其他 derived operation，不创建 `DataSubmission`、Job、实验身份或运行制品。Workflow
进入执行时记录 `▶️ workflow`，成功返回后记录 `✅ workflow`；错误原样传播，已正式提交
的较早年度保留，重跑通过 Meta hit 续建。有效年度对象不隐式刷新或覆盖，未来年份不进入
范围。

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
5. Standard 从 `app_config.data.feature_sets` 与 `label_sets` 分别选择全部且仅选择
   `enabled=true` 的 operation；Level-2 选择空的 feature 与 label operation 集。

Standard 允许 Feature 配置全部 disabled，也允许 Label 配置全部 disabled；任一空 operation
集都由 workflow 记录一条 `reason=no_enabled_config` warning，再自然成功。Enabled
identity 必须在 workflow 准备阶段解析到精确 builder，否则在任何日期 I/O 前失败。
Level-2 暂时仍组装并各执行一次空的 `FeatureBuildStep` 与
`LabelBuildStep`，但不读写 derived 数据。

Tushare manifest 是受代码审查的执行清单，不通过配置、Broker 反射或 capability provider
动态展开。Level-2 配置则只表达文件 identity、启停和输出映射。除 calendar 外的所选
source 在 workflow 准备阶段转换为完整 `SourceConfig` 后直接绑定到
`FactMaterializeStep`；固定的 Tushare calendar 由 `CalendarMaterializeStep` 按年度对象
直接承担。

Broker adapter 在首次 raw Meta miss 时才构造，并按 broker 在整个 workflow 内缓存一次。
全 Meta hit 不构造 adapter。一次 fetch 仍可拥有自己的网络 session。Normalize operation
在准备阶段绑定 source/profile；日期循环不得重复解析 capability。

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

Calendar 的 processed 与 raw Meta hit 分别记录 `♻️ calendar processed meta hit` 与
`♻️ calendar raw meta hit`；每个新发布年度记录一次 `✅ calendar publish`，Step 成功后以
`✅ calendar materialize` 聚合 years、reused、published 与 trade_dates。Fact 不记录逐日
ingest/normalize start 或 finish；每个 raw 与 processed Meta hit 分别记录 `♻️ raw meta hit`
与 `♻️ processed meta hit`。每个实际 raw ingest 记录 `✅ raw ingest` 及其 elapsed_seconds；
每个 processed publish 记录 `✅ processed publish` 及其中 normalize_seconds；不可用 source
记录 `⚠️ fact source` 及本次尝试的 elapsed_seconds。每个成功完成的日期记录
`✅ fact date` 及其 elapsed_seconds。Meta hit 不计入 raw ingest 或 normalize runs。Step 成功后
先以单个多行 `✅ Fact operation summary` 按 source 汇总 raw ingest、按 target 汇总 normalize
的总耗时、平均耗时和真实 runs；没有实际执行的 raw ingest 或 normalize 时不输出该摘要。
最后以 `✅ fact materialize` 聚合 trade_dates、raw_reused、raw_fetched、processed_reused、
processed_published 与 unavailable。失败仍按现有边界原样传播。

所有 fact 日期完成后，一个 `FeatureBuildStep` 才在自己的一次 `run` 中按到达日升序生成
全部 selected feature operation；一个 `LabelBuildStep` 再按相同日期顺序生成全部 selected
label operation。Feature 输出 Meta miss 时，Step 从精确 builder 读取
`lookback_sessions=L`，通过 Access 取得截至目标日的最近 `L + 1` 个正式交易日，并把完整
tuple 交给 builder；有效 Meta hit 不解析历史 session、不读取 facts 或重算。每个 label set
只有一个 maturity：builder 的 `lookahead=L` 时，Access 返回截至到达日的最近 `L + 1` 个
正式交易日；完整 tuple 交给 builder，首日是 label
partition identity，末日是 maturity。多个 label set（包括不同 lookahead）在同一个
`LabelBuildStep` 中各自解析窗口、复用 Meta 和发布分区，互不改变对方的 maturity。空
operation 集自然不产生数据；Pipeline 不隐式扩大请求范围。

Feature/label builder 和 Access 不记录运行日志。Private 发布函数也不记录日志；具体 Step
在每个 operation 完成后以 `♻️ feature meta hit` / `♻️ label meta hit` 表示复用，以
`✅ feature publish` / `✅ label publish` 表示发布，label 日志同时携带 partition date 与
maturity date。调度、计算或发布错误原样传播，不追加重复错误日志。

两个 kind 的显式 step 顺序都固定为 calendar materialize → fact materialize → feature
build → label build。Standard 的 derived operation 来自 enabled 配置；Level-2 的两个
derived operation 集为空。其他差异只存在于 workflow 准备阶段选择的 source 与 normalize
实现。Pipeline 只按 workflow 传入的单一 tuple 执行，不知道
也不校验这些领域顺序。`stock_basic`、`stock_st` 和 `suspend_d` normalize 产生零行时必须
把它作为有效空集合发布 payload 并提交 Meta；其他 normalize、feature 或 label 产生零行
必须失败。
`stock_basic` 的 `2019-04-01` 是无记录且源 DataFrame 不携带列的正式案例：normalize 必须
提供空 `symbol` 与 `list_date` 列，producer 必须发布零行
`processed/stock_basic/v1/trade_date=2019-04-01/data.parquet` 及同目录 `meta.json`，不得使用
其他日期填充。
`stock_st` 的 `2019-04-01` 是该规则的正式案例：零行、零列 raw 对象必须产生含空 `symbol`
列的零行
`processed/stock_st/v1/trade_date=2019-04-01/data.parquet` 及同目录 `meta.json`，不得跳过
normalize 或把 processed 对象留作缺失。成功对象必须先发布 payload 再提交 Meta；
Meta reuse、lineage、Level-2 symbol slices 与 staging/raw 选择继续由各 producer owner 负责。
Calendar bootstrap、Data、Standard fact cold-start 与 Feature backfill workflow 保留
`▶️ workflow` 和成功后的 `✅ workflow` 业务日志；Training 和 Backtest workflow 不新增
workflow 边界日志。

## Standard fact cold-start workflow

`run_standard_fact_bootstrap` 是 CLI-only `data-standard-bootstrap` 的唯一 workflow，直接消费
已校验的 `StandardFactBootstrapSubmission(start, end)`。闭区间精确表示调用方要求写入的
calendar 与 Standard facts，不表示 Feature 目标范围。Workflow 不读取 Feature/Label 配置，
不从 builder 读取 lookback，也不隐式扩大日期范围。

Workflow 与 Standard data workflow 使用同一个 Tushare active manifest 解析规则、同一个
固定 calendar source、相同 broker/normalize 绑定和相同 lazy adapter cache 语义。它从收到
的 `PathManager` 创建唯一 Access，只显式组装 calendar materialize → fact materialize 两个
Step，并调用一次 `DataPipeline.run()`。Calendar、fact、Meta reuse、缺失日期、空正式 session
集、日志与部分提交后的重跑语义和 Standard data workflow 完全相同；不组装 Feature 或 Label
Step，不写 derived、experiment 或 Job 状态。

Workflow 的 Instrumentation identity 固定为
`data-standard-bootstrap_{start}_{end}`。进入执行时记录 `▶️ workflow`，成功返回后记录
`✅ workflow`，两条日志的 `kind` 均为 `data-standard-bootstrap`。错误原样传播。

## Feature backfill workflow

`run_feature_backfill` 是 CLI-only `data-feature-backfill` 的唯一 workflow，直接消费已校验的
`FeatureBackfillSubmission(feature_set, version, start, end)`。`feature_set/version` 精确选择
一个 registry identity；闭区间精确表示目标 Feature 分区，不表示输入 facts 范围。

Workflow 从收到的 `PathManager` 创建唯一 Access，先构造一个只含所选 identity 的
`FeatureBuildStep`，从而在任何日期 I/O 前完成 registry 解析；随后通过同一个 Access 把请求
闭区间解析为升序正式目标 session。它只显式组装该单一 Step 并调用一次
`DataPipeline.run()`。目标 session 集为空时 Step 仍执行一次并自然成功。

Backfill 不组装 Calendar、Fact 或 Label Step，不创建 broker adapter，不下载或写入 raw、
processed、label、experiment 或 Job 状态。每个目标 Meta miss 的历史依赖、发布和失败语义
完全由同一个 `FeatureBuildStep`、精确 builder、Access 与 derived-partition 发布边界拥有；
有效 Meta hit 不读取历史输入，较早目标已提交而较晚目标失败时保留已提交分区，重跑从 miss
处续建。

Workflow 的 Instrumentation identity 固定为
`data-feature-backfill_{feature_set}_{version}_{start}_{end}`。正式目标 session 解析完成后记录
`▶️ workflow`，成功返回后记录 `✅ workflow`；两条日志携带 kind、feature_set、version、
start、end 与 targets。错误原样传播。

## Training workflow

Training runtime 范围只来自 `TrainingSubmission`。Model group 只来自 `ModelConfig.group`，
workflow 通过 `training.models` 的显式不可变 catalog 取得 trainer，不在 Step 内按名称分支；
当前 catalog 支持 `sgd_regression`。`group` 必须非空；未登记名称在 experiment identity、
Access 与 Instrumentation 之前抛 `ValueError`。配置必须显式选择非空、有序且不重复的
`feature_columns`；不存在“空列表表示全部列”。实际训练 DataFrame 的列及顺序进入已拟合
预处理对象，成为 `feature_names` 的唯一权威；`params.json` 只投影该事实。

Feature producer 及其 feature set/version 拥有价格口径。Training、evaluation、artifact
loader 与 backtest 都只消费同一 feature set/version，不读取复权因子，也不再做 raw/qfq/hfq
转换。当前 feature/label 的精确 schema、复权口径、观测时间与 maturity 由
[`docs/data/daily_feature_label_contract.md`](data/daily_feature_label_contract.md) 所有。
Producer 只通过 workflow 绑定的同一个 Access 读取具名正式对象，不接收 `PathManager` 或
processed version，也不直接解析 processed 路径或 Meta。

所选 label builder 的 set-level `lookahead` 是 `eval_offset` 的唯一来源；配置的
`label_column` 必须精确等于该 builder 唯一的 `label_column`，否则在日历读取前失败。Workflow
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
提供的日期。Dataset loader 直接消费完整 `train_dates` 和 `eval_date`，读取所选 feature 与
label，要求两者 `(symbol, trade_date)` key 的值和顺序精确一致，不执行 join；只删除 label
为 NaN 的行，并保留 feature NaN 交给预处理。Missing
只表示 NaN；正负无穷是无效输入，必须在 dataset 或 preprocessing 边界以 `ValueError`
失败，不得转换成 NaN、填充值或零。

一个具体的 `FittedPreprocessor` 同时拥有实际训练列、拟合状态和唯一的 `transform` 实现。
`constant` 使用配置给出的有限数；`mean` 与 `median` 只从训练行拟合，全 NaN 列必须失败，
不得自动回落为 `0.0`；`drop` 跳过任一 feature 为 NaN 的整行，并由 `transform` 同时返回
原输入长度的布尔保留 mask 和保留行的转换结果。训练侧 fit 后也调用这个 `transform`，按
mask 同步选择 label；evaluation 与 runtime 不另写预处理逻辑。

Workflow 以 `per_window_steps` 显式声明 dataset load → train-only preprocess fit/transform →
catalog-selected fresh model train 并构造就绪 `InferenceModel` → 由同一个
`InferenceModel.predict` 执行 Rank IC，并以 `final_steps` 声明 artifact persist → report。
训练行全部被 drop 时 preprocessing 失败；评估保留不足两行或 Rank IC 非有限值时 evaluation
以 `RuntimeError` 失败。Training step 只写有限的 `metrics[f"ic@{eval_date}"]`。

最终 training artifact 精确为 `params.json`、`metrics.json` 和 `inference.pkl`。Params schema
只包含：

```text
experiment_id, model_group, asof_day,
feature_set, feature_version, feature_names,
label_set, label_version, label_column, label_lookahead
```

Metrics 必须是非空的有限数映射，key 精确匹配 `ic@YYYY-MM-DD`。Persist 依次原子发布 params、
metrics，并最后原子发布包含原始模型与 `FittedPreprocessor` 的就绪 `InferenceModel`；不存在
独立 model/preprocess 文件。Report 与其他 reader 共用 artifact owner 的 JSON/schema loader。
公共返回值为 `None`。

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

Component builder 直接加载所选 training experiment 的 `inference.pkl`。推理中的
`missing.method="drop"` 对所有请求 symbol 使用同一个 fitted transform：被跳过的 symbol
当天没有 score；所有 symbol 都被跳过仍是成功的“无信号”日。未持有的跳过 symbol 不产生
新仓位；已持有的跳过 symbol 保持原数量，并占用 portfolio 的持仓容量，不得因缺少 score
隐式生成清仓目标。

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
