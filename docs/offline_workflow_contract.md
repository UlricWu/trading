# Offline 工作流契约

- **状态**：正式 owner
- **适用范围**：offline data、CLI-only backfill、offline training 与五种 backtest 验证任务
  的配置选择、日程、calendar bootstrap、执行编排、运行结果和实验命名。
- **CLI owner**：[`docs/engineering/cli_contract.md`](engineering/cli_contract.md)

## 共同边界

`src/workflows` 只提供以下 workflow composition root：

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

run_level2_minute_backfill(
    *, path_manager: PathManager,
    submission: Level2MinuteBackfillSubmission,
) -> None

run_stock_1430_backfill(
    *, path_manager: PathManager,
    submission: Stock1430BackfillSubmission,
) -> None

run_stock_1430_fusion_backfill(
    *, path_manager: PathManager,
    submission: Stock1430FusionBackfillSubmission,
) -> None

run_offline_training(
    *, models: Mapping[str, ModelPreset],
    feature_groups: Mapping[str, FeatureGroupConfig], path_manager: PathManager,
    submission: TrainingSubmission, experiment_id: str,
) -> None

run_backtest(*, submission: BacktestSubmission, path_manager: PathManager,
             experiment_name: str, metrics: BacktestMetrics) -> None
```

`src/workflows` 只负责自身拥有的入口前置条件、运行元数据、日程解析和依赖组装，并通过不可变 tuple 显式
声明具体 step 及其顺序。Data 只使用一个 `DataPipeline`，Training 使用 `TrainingPipeline`；
五种任务的账户回放统一使用 `BacktestPipeline`。`DataPipeline` 只拥有 workflow 传入的单一 `steps`
tuple 和 Instrumentation 作用域，不知道 data kind、日期 schedule、source、step 分类或具体
step 类型；它必须严格按传入顺序把同一个 Context 交给每个 step，一次 `run` 中每个 step
实例只调用一次。TrainingPipeline 拥有一个已解析的训练窗口及单一 Step 链，
BacktestPipeline 拥有一个运行级 Context、动态时钟，以及 workflow 显式传入的
startup_steps、tick_steps、result_steps 三组不可变 Step tuple：启动与结果各执行一次，
逐时点组在每个时钟时点执行一次。
所有 Pipeline 都不得创建、发现、排序或按类型重排 step。

Backtest 的一个 CLI 命令统一准备实验名称和初始 `BacktestMetrics`，将有类型的 submission
交给 `run_backtest`；CLI 不组装 Step。Workflow 绑定请求、输入访问、账户/策略/风控/执行
配置和输出路径，显式声明启动、逐时点与结果三组 Step。准备输入、账户回放、事实保存、
结果评价、结果发布仍是五段业务阶段，但不要求每段对应一个 Step。PrepareInputsStep
准备输入，InitializeAccountStep 创建账户对象与时钟，BacktestPipeline 遍历时钟。
Workflow 通过选择 Step 和绑定参数表达信号/冻结委托、可选风控、策略及成交假设的区别；
Pipeline 不接收 mode，也不按 Step 类型选择业务。Workflow 不重新命名实验、重建请求
参数快照或重复记录任务开始日志；GenerateSignalStep 在当前决策时点生成预测。

公共编排抽象只允许结构化 `PipelineStep[ContextT]` 协议和 `run_steps(...)` 保序执行器。
`run_steps(...)` 自动以具体 step 类名调用 `Instrumentation.measure(...)`；该公共调用形式
由 [`docs/engineering/technology_stack_decisions.md`](engineering/technology_stack_decisions.md#instrumentation-公共调用形式)
拥有。每个 step 必须返回同一领域的 Context，业务终止以异常表达。不得建立通用
Pipeline/Workflow 基类、DAG、step registry、依赖声明、priority 或 before/after 规则。

`src/data_system/steps` 是 offline data 业务行为的唯一实现目录；日期循环和 operation
调度由具体 Step 拥有。`steps` 包内部的 private `_publish_parquet_object` 统一承担
Calendar、Fact、Feature/Label 与 Level-2 minute 的表制品发布：接收已构建的
`pyarrow.Table`、`ObjectPaths`、可选的 `upstream_meta_path` 和 `symbol_slices`，先通过
`write_parquet_atomic` 发布完整 payload，再调用 `meta.commit` 提交 Meta。该顺序遵循
[存储契约](data/storage_layout.md#object-side-meta)，不构成多文件事务；错误原样传播。
该函数不判断复用、空表是否合法或具体数据内容，也不选择 broker、执行计算或记录日志。
Raw payload 已由 broker 写入，Step 直接调用 `meta.commit`，不经该表制品发布函数。

Calendar 的 processed 年度对象与 Feature/Label 分区另共用 private `_publish_partition`。
该函数唯一拥有这些对象的输出 Meta reuse 校验、miss 后调用构建能力、输出非空检查和发布；
有效 Meta 返回 `None`，无效 Meta 原样失败，仅 Meta 不存在时同步调用一次 `build()`，再调用
`_publish_parquet_object` 完成发布并返回行数。构建 callable 不被保存或延迟到本次调用之外。
具体 Step 决定日期、对象身份及 miss 时所需的输入和计算；根据返回结果记录复用或发布日志。
发布边界不拥有业务计算或日志，不重复校验 Access 或 producer 已保证的输入内容。
Calendar 新发布时记录该年度 raw Meta；已有 Calendar Meta 的直接 upstream 继续由
`meta.find` 按存储契约校验。Feature/Label 不提供 upstream，已有 Meta 包含 `upstream`
时必须失败；所有这些对象的已有 Meta 包含 `symbol_slices` 时必须失败。Fact 和 Level-2
minute 的复用条件、空表规则、upstream/index 与日志仍遵循各自 owner；具体 Step 准备表
及相关参数后调用共享发布函数。

Broker、builder 与 normalize 分别保留在
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
原样传播异常。Data 与 Training 各调用它执行一次完整 Step 链；Backtest 分别调用它执行
启动组、每个时点的交易组和结果组。各领域 Pipeline 拥有 Context 创建和整个 workflow
的 Instrumentation 作用域；TrainingPipeline 接收已解析的训练窗口，BacktestPipeline
遍历本次初始化的账户时钟并创建每个时点的 TickState。`run_steps(...)` 不拥有这些生命周期。

该公共规则必须表达为函数而不是公共 Pipeline 基类或执行器对象。单段 Step 链执行没有独立
身份、状态或资源生命周期；把 `Instrumentation` 保存到公共对象并增加 `run_step(...)`，只会
包装 `Instrumentation.measure(...)` 与 `step.run(...)`。让三个领域 Pipeline 继承公共实现，
属于仅为复用循环而继承，并使子类隐式依赖父类状态。让一个通用 Pipeline 同时理解单次执行、
window、timing、final phase 或是否管理 Instrumentation，则需要模式分支并混合不同领域
schedule。因此不得新增 `BasePipeline`、`StepExecutor`、`PipelineRunner`、`StepChain` 或具有
同等职责的包装对象。

若未来不再要求跨领域统一的 Context 串联和默认测量，`run_steps(...)` 应直接删除，并把 Step 链循环
内联到各具体领域 Pipeline 的 `run()`；不得把链执行移入 workflow、Step 或 Instrumentation，
也不得以三个同构 private helper 代替。当前契约仍要求上述公共不变量，因此保留
`run_steps(...)` 是满足语义的最小实现实体。

Data、Training 与 Backtest 各自使用最小领域 Context，只保存相邻 step 必须传递的值；
config、registry、Access、PathManager、Instrumentation 和 experiment identity 绑定到具体
step 或 Pipeline，不进入 Context。Data Context 保存请求的 `start`、`end`，并由 calendar
step 写入后续 step 所需的 `trade_dates`。Training 与 Backtest Context 每次 `run()` 新建。
Training 的训练值和逐日评价观察只在这次 Step 链内传递。`BacktestContext` 保存 metrics、
PrepareInputsStep 产出的 `BacktestInputs`、InitializeAccountStep 产出的 `BacktestState`、
本次市场、执行器、策略构造器、RiskManager 和时钟，以及当前 TickState。运行对象是独立
Step 必须共享的具名领域对象，不另建组件字典、依赖查找器或配置容器。每次初始化新建
这些对象，不把可变账户或策略实例保存在可重复使用的 Step 上。输入与运行对象尚未创建时
为 None；账户 State 初始化后即持续积累，是否回放完成由进入结果组的控制顺序表达，
不另加完成标记。无策略或风控时相应对象为 None。已声明的顺序建立内部消费前置条件，
下游不增加重复校验或兜底。Step 必须消费前一步返回的 Context，不能读取 workflow 手持
的旧 Context、旧 State 或旧 metrics；该要求也适用于相邻组和相邻时点。

`BacktestInputs` 只保存已校验行情与信号批次索引或冻结委托日程，不携带 Access、配置、
时钟或策略对象；信号索引独立保存为只读映射。`BacktestState` 保存该账户的交易事实，
包括实际预测引用、决策日期和已完成的风险观察。TickState 只保存当前时点、session、
信号批次、原始目标、执行目标、撤单请求和待完成风控观察，每个 tick 新建，回放结束后
Context.tick 清为 None。拆成独立 Step 后，这些值确实需要跨 Step 传递；逐时点生命周期
防止前一时点的信号、目标或撤单再次生效。唤醒由 ScheduleWakeupsStep 根据当前挂单与
策略日程计算并登记，不另外积累临时唤醒状态，也不创建收尾 Context。
固定快照的风险评价继续使用 RiskCase 与 RiskObservation，不创建交易账户。
`BacktestMetrics` 的 mode、parameters 和 not_run 是输出记录，不是供 Step 选择算法或
发现依赖的运行配置；评价结果仅在回放并保存事实后填入，不进入交易决策。

Data Context 是可信内部载体，构造时不执行日期校验。请求范围由
[`CLI 契约`](engineering/cli_contract.md#通用边界)约定的 submission 构造边界保证；
workflow 自行生成范围时负责在执行准备前建立相同的日期格式与顺序不变量。`start/end`
在同一次 Step 链中保持不变，Context、DataPipeline 和内部 data Step 不重复校验它们。
Calendar Step 对该范围执行年度物化与正式交易日解析；Access 的独立 public 请求校验继续由
[`Access 契约`](engineering/access.md#错误归属)定义。

需要 Access 的 workflow 从收到的同一个 `PathManager` 创建唯一
`Access(pm=path_manager, processed_version="v1")`；固定风险案例不创建无用 Access。公共
入口不接受第二个 `Access`，从而不能把不同 storage root 组合到同一次执行。正式 processed
version 固定为 `v1`，配置和 submission 都不得选择它。

Training 与 backtest 的 experiment name 分别固定为：

```text
training_{start}_{end}_{experiment_id}
backtest_{today}_{experiment_id}
```

Training 沿用日期范围命名，最终 experiment 目录已存在时，在日历读取和执行前以
`FileExistsError` 失败；该检查不创建、预留、锁定、清理或恢复目录。

五种 Backtest 共用同一名称格式。`today` 是 CLI 完成请求解析和配置加载后通过 `DateTimeUtils.today()`
读取一次的 Asia/Shanghai 当天日期，格式为 `YYYY-MM-DD`；它不取自回测 start/end、市场
交易日或风控案例。调用方为每次运行提供新的唯一 experiment_id，HTTP 使用 Job UUID。
日期加 UUID/唯一 ID 视为不会重名，不执行目录存在性预检、重名拒绝或排他创建。CLI 与
workflow 不预建运行目录；首次制品写入时由持久化工具创建所需父目录，输入准备或执行
在首次写入前失败不创建运行目录。写入过程中失败可能留下目录或先前已写制品，按
[失败现场](#失败现场)保留。日期范围、mode 与案例引用保存在参数中，目录不再编码这些内容。

Instrumentation 衡量 workflow 显式组装的 step，返回 `step.run(context)` 的原始结果，以
`finally` 累计耗时和次数；异常只附加 scope 与 step 名称的 note，保留原异常对象及类型，
不在此记录 traceback。Step 不接收也不调用 Instrumentation；只有
`run_steps(...)` 负责默认测量。同名 step 累计总耗时、次数和平均值；离开 Instrumentation
作用域时只输出一个多行、等宽对齐的“步骤耗时”日志事件，首行以 `scope=...` 定位运行，
依次列出每个 step 的总耗时，再列出“步骤耗时合计”。合计只累加被 measure 包围的步骤，
不包含 Step 之外的依赖组装或 CLI/Job 启动等耗时，必须注明“不代表任务总耗时”，不得称为端到端耗时。
Backtest 的输入读取属于 PrepareInputsStep，因此包含在该步骤及合计中。
Workflow 可通过 `descriptions` 提供 step 的中文含义，Instrumentation 独立保存该映射，
展示时附在对应行后，不改用于累计的代码名称。训练 ReportStep 的含义为“诊断计算与报告
输出”，包含材料读取和诊断计算，不能只称为 HTML 渲染。同名 step 在同一作用域内实际
执行多次时，该行才额外列出平均耗时和 runs。正常退出以 `✅` 和 `INFO` 开头，异常退出
以 `❌` 和 `ERROR` 开头；续行不重复
时间、level、源码位置或状态符号。进入作用域前的入口或日程解析失败不输出 timeline；
Backtest 输入准备和账户初始化位于启动组内，失败必须输出对应 Step 的失败 timeline。
启动和结果 Step 各测量一次；每个 tick 中实际装配的 Step 各测量一次，ValueAccountStep
在决策前后各调用一次，因此累计次数为时点数的两倍。未装配的业务步骤没有计时行。
现有 step label 固定为：

```text
CalendarMaterializeStep, FactMaterializeStep, FeatureBuildStep, LabelBuildStep,
Level2FactMaterializeStep, Level2MinuteBuildStep, Stock1430MaterializeStep, Stock1430DailyMaterializeStep,
Stock1430DailyL2MaterializeStep,
DatasetBuildStep, PreprocessStep, ModelTrainStep, PredictionStep, ICEvaluateStep,
ArtifactPersistStep, MetricsPersistStep, ReportStep,
PrepareInputsStep, InitializeAccountStep, UpdateMarketStep, AdvanceOrdersStep, ValueAccountStep,
GenerateSignalStep, GenerateTargetsStep, ApplyRiskStep, SubmitTargetsStep, SubmitFrozenCommandsStep,
CompleteRiskObservationStep, ScheduleWakeupsStep, FactsPersistStep, EvaluateResultsStep, PublishResultsStep
```

Workflow 不统一包装异常，不把失败改成空制品或 success，也不重复记录 traceback。

## Calendar bootstrap workflow

`run_trade_calendar_bootstrap` 是 CLI-only `data-calendar` 的唯一 workflow。CLI 在 composition
root 读取一次 Asia/Shanghai 当前日期，并以显式 `as_of_date` 传入；workflow 不读取当前
时间。`as_of_date` 必须是规范系统日期且不早于 `2016-01-01`。早于该日的输入必须在
依赖组装、Instrumentation、运行日志和 I/O 前以 `ValueError` 失败。Bootstrap 范围固定为：

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
4. 绑定固定的 Tushare calendar broker/normalize，并由 workflow 按所选 fact source family
   直接绑定一个 broker class 和一个固定 broker normalize callable；
5. Standard 从 `app_config.data.feature_sets` 与 `label_sets` 分别选择全部且仅选择
   `enabled=true` 的 operation；Level-2 固定选择两市股票分钟事实以及 H03 14:30
   Feature/Label，不读取日频 feature/label registry 配置。

Standard 允许 Feature 配置全部 disabled，也允许 Label 配置全部 disabled；任一空 operation
集都由 workflow 记录一条 `reason=no_enabled_config` warning，再自然成功。Enabled
identity 必须在 workflow 准备阶段解析到精确 builder，否则在任何日期 I/O 前失败。
Level-2 不组装通用 `FeatureBuildStep` 或 `LabelBuildStep`；分钟与 H03 派生对象使用
下述固定步骤，identity、version、计算和 Meta 契约沿用各自数据 owner。

Tushare manifest 是受代码审查的执行清单，不通过配置、Broker 反射或 capability provider
动态展开。Level-2 配置则只表达文件 identity、启停和输出映射。除 calendar 外的所选
source 在 workflow 准备阶段转换为完整 `SourceConfig`，Standard 绑定到
`FactMaterializeStep`，Level-2 绑定到 `Level2FactMaterializeStep`；固定的 Tushare calendar 由 `CalendarMaterializeStep` 按年度对象
直接承担。

每个 Fact Step 的所选 source 属于同一个 broker。Workflow 拥有 source 非空、source family
选择和具体执行依赖的绑定。Workflow 在对应 source family 分支直接引用具体 broker class
与 normalize callable，不经过 broker name 映射表。Standard facts 固定使用 `TushareBroker`，
Level-2 facts 固定使用 `Level2Broker`；normalize callable 遵循 source owner 的固定对应关系。
Step 接收已绑定的零参数 `get_broker` callable 与 `normalize_operation`，不接收
AppConfig、broker class 或 adapter cache，也不重复检查这些已建立的关系；正确绑定由
workflow 装配测试验证。Calendar 和 Standard Fact 的 callable 返回 `TushareBroker`，
Level-2 Fact 的 callable 返回 `Level2Broker`。
Level-2 `outputs=[]` 的 source 仅存云端，不执行本地 ingest 或 normalize。

Broker adapter 在首次需要来源 I/O 时才构造，并按 broker 在整个 workflow 内缓存一次。
`src/workflows` 的 private `_get_broker` 唯一实现实例查询、构造及成功后的缓存写入；
workflow 使用 `functools.partial` 绑定具体 class、AppConfig 和本次运行独有的 cache，
把零参数 callable 交给 Step。Calendar 与 Standard Fact 只在 raw Meta miss 后调用该能力，不再自行
构造或管理实例。同一 workflow 中每个 broker 名称绑定同一 class 与配置，Standard 的
Calendar 与 Fact 因而复用同一 Tushare 实例。构造异常原样传播且不写入缓存；不同 workflow
运行不共享实例。全 Meta hit 不调用供给能力、不构造 adapter。一次 fetch 仍可拥有自己的
网络 session。Normalize operation 在 workflow 准备阶段绑定；日期循环只调用已绑定的
callable。

两个 kind 的 workflow 都只显式组装一个线性 step tuple。`CalendarMaterializeStep` 先在自己
的一次 `run` 中按自然年升序复用或物化完整 `[start, end]` 所需的 `trade_calendar` 年度
对象，再通过同一个 Access 把正式交易日写入 Context。随后：

- Standard 的 `FactMaterializeStep` 在自己的一次 `run` 中对每个正式交易日执行所选 fact source 的
  ingest 与 normalize；
- 休市日不执行 fact；只包含休市日的范围成功。

Standard 单个日期 ingest 必须尝试全部 selected fact source。已有 raw Meta 与本次成功提交的 raw
对象都表示可用。Ingest 保留命中时取得的 `MetaRecord`；新下载直接使用 Broker 返回的 raw
路径提交 Meta，并取得该记录。成功返回按 `source_name` 对应的全部 raw 记录，全部无
payload 返回空映射，部分可用必须在尝试完全部 source 后抛 `RuntimeError`。同一次日期
执行的 normalize 直接消费这些记录，不为每个 output 再读取同一 raw Meta；只有 processed
Meta miss 才选择物理输入，同一 source 的多个 output 复用本次选定的输入路径。发布边界
仍按存储契约校验并记录直接 upstream。Standard 的任一正式交易日全部 fact source 缺失
都必须失败；
`FactMaterializeStep` 必须完成范围内所有正式交易日的尝试后，一次报告全部缺失日期。不得
返回 workflow 级 skipped 或跳过缺失日期。只包含休市日、因而没有正式交易日的范围成功。

Level-2 按日期、source 逐个处理有输出的来源：先校验 processed Meta 与 symbol slices；
全部命中时不构造网盘客户端、不要求本地 cache。仅对 miss 准备 raw：复用已登记网盘
记录，或查询来源并提交 raw Meta；复用同尺寸完整 cache，否则从确切网盘路径下载。
迁移前的本地 raw、以及已登记网盘但尚未清理的同尺寸本地 raw 可以直接作为输入。
随后调用既有 normalizer，先发布非空逐笔 payload 再提交带 symbol slices 和 raw upstream
的 Meta。不新增格式映射，不改变分钟、Feature、Label 的 schema 或成熟日期。

日期内逐个发布，部分成功保留；明确缺失的 source 汇总为 `source@date`，范围结束后
失败。认证、传输、Meta、解压及标准化错误立即传播。产生了新逐笔对象且当日所选来源
均可用后，按[存储 owner](data/storage_layout.md#level-2-网盘-raw-与本地缓存)清理旧 cache；
全 processed hit 不因清理而访问网盘。下载、标准化与清理共用 Level-2 cache 锁，避免并发
Job 在转换期间删掉 raw；Standard 的流程不取此锁。

Calendar 的 `_materialize_year` 按顺序显式执行：复用合格 processed 年度对象并返回；通过
`_ensure_raw_calendar` 复用或获取 raw 日历并保证 raw Meta 已提交；调用 `normalize_tushare`
得到标准表；发布 processed 分区。年度 ingest、raw Meta 和直接 raw 输入选择由
`_ensure_raw_calendar` 拥有，成功时返回 raw payload 路径。只有 processed Meta miss 才执行
raw 准备和 normalize，raw Meta hit 也必须经过共享发布边界的输出非空检查。Broker 对空响应
的拒绝继续发生在 raw 写入前。Standard Fact 的对应责任由 `FactMaterializeStep` 直接承担。
两个 Step 共享 workflow 的 lazy broker adapter cache。仅当某日全部所选 fact source 可用
时才 normalize；不得拆分独立 ingest/normalize Pipeline Step，也不得引入 Materializer 或
其他转发对象。

Calendar 的 processed 分区结果由 Calendar Step 记录，`who` 精确携带
`calendar; calendar_year=<year> output=<payload_path>`；raw Meta hit 继续由 Calendar
记录 `♻️ calendar raw meta hit`。Step 成功后以 `✅ calendar materialize` 只聚合 years 与
trade_dates，不再为日志累计 reused/published。Standard Fact 不记录逐日
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

Feature/label builder、Access 和 private 发布函数不记录运行日志。分区结果日志由具体 Step
输出：发布函数返回 `None` 后记录一次 `♻️ {who}`；新分区 Meta 提交成功并返回行数后记录一次
`✅ {who} rows={rows}`。Feature/Label Step 提供 `who`，以 `feature` 或 `label` 为具名对象，
以 `;` 分隔数据集、version 和 partition date 上下文；label 同时携带 maturity date。
发布函数的 `int | None` 只供调用 Step 记录分区结果，不累计通用运行状态。
Calendar 的 `_materialize_year` 无返回值；各 Step 的 `run` 返回原 `DataContext`。
调度、计算或发布错误原样传播，失败分区不输出结果日志，也不追加重复错误日志。

Standard 的显式 step 顺序固定为 calendar materialize → fact materialize → feature build →
label build，derived operation 来自 enabled 配置。Level-2 固定为 calendar materialize →
`Level2FactMaterializeStep` → `Level2MinuteBuildStep` → `Stock1430DailyMaterializeStep`。每个 Step
完成整个请求范围后才进入下一 Step，Pipeline 只执行 workflow 传入的单一 tuple，不知道
也不校验这些领域顺序。

Level-2 请求范围表示到达日 `A`。分钟 Step 复用人工分钟回填的实现、固定 V1 和有限 batch
边界，为范围内每个正式 session 构建两市分钟事实。每日 H03 Step 按到达日升序先复用或
发布 `l2_stock_1430/v1(A)`，再通过 `Access.recent_trade_dates(end_date=A, sessions=2)`
确定上一正式 session `T`，复用或发布 `l2_stock_1430_t1_vwap_rank/v1(T)`；Label 的 maturity
为 `A=T+1`。例如周一到达时生成周一 Feature 和前一周五 Label，不请求周二输入，也不生产
周一尚未成熟的 Label。

首个到达日前一 session 的 Feature、两市分钟事实，以及 Label 对应 T/A 的日频 factor，
都必须已由各自入口提交。每日 Step 不扩大请求范围、不自动补建这些历史或 Standard 输入；
Label Meta miss 时缺少必要对象必须失败。当前日 Feature 成功后 Label 失败，Feature 保留。
每日与人工 H03 回填复用同一逐分区读取、发布与日志实现；每日 Step 每次解析 Label 目标
日期，之后有效 Meta hit 不再读取该对象的分钟、factor 或 Feature 输入。休市日没有到达
session，不执行分钟或 H03 输入读取。该日常链不生成 H04 融合 Feature。

`stock_basic`、`stock_st` 和 `suspend_d` normalize 产生零行时必须
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
Calendar bootstrap、Data、Standard fact cold-start、Feature backfill 与 Level-2 minute
backfill workflow 保留 `▶️ workflow` 和成功后的 `✅ workflow` 业务日志；Training 和
Backtest workflow 不新增 workflow 边界日志。

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
完全由同一个 `FeatureBuildStep`、精确 builder、Access 与共享分区复用、发布边界拥有；
有效 Meta hit 不读取历史输入，较早目标已提交而较晚目标失败时保留已提交分区，重跑从 miss
处续建。

Workflow 的 Instrumentation identity 固定为
`data-feature-backfill_{feature_set}_{version}_{start}_{end}`。正式目标 session 解析完成后记录
`▶️ workflow`，成功返回后记录 `✅ workflow`；两条日志携带 kind、feature_set、version、
start、end 与 targets。错误原样传播。

## Level-2 minute backfill workflow

`run_level2_minute_backfill` 是 CLI-only `data-level2-minute-backfill` 的唯一 workflow，直接
消费已校验的 `Level2MinuteBackfillSubmission(start, end)`。闭区间精确表示目标分钟事实分区，
不表示逐笔下载范围，也不隐式运行日常 `data-level2`。

Workflow 从收到的 `PathManager` 创建唯一 Access，通过正式 trade calendar 把请求闭区间解析为
升序目标 session，然后只显式组装一个 `Level2MinuteBuildStep` 并调用一次
`DataPipeline.run()`。它不组装 Calendar、Fact、Feature 或 Label Step，不创建 broker adapter，
不读取配置中的 Level-2 source 或 derived operation，也不写 raw、feature、label 或 experiment。
目标 session 集为空时 Step 仍执行一次并自然成功。

`Level2MinuteBuildStep` 按 Context 中的日期顺序执行；每个日期固定先
`sh_stock_trade_1m <- sh_trade`，再 `sz_stock_trade_1m <- sz_trade`。每个输出 miss 时，Step
通过 Access 取得该交易所实际观察到的 symbols，按有限 symbol batch 读取逐笔事实并调用分钟
builder；batch 大小不进入 submission、配置或 Context。输出 schema、计算、空 stock 结果、
lineage、Meta reuse 和发布错误由
[`docs/data/level2_minute_contract.md`](data/level2_minute_contract.md) 所有。

一个日期或交易所失败时原异常传播，后续对象不再执行；此前已经提交的日期或同日上海对象
保留。有效输出 Meta hit 不读取 symbols 或逐笔 payload，重跑从第一个 miss 继续。Workflow
Instrumentation identity 固定为：

```text
data-level2-minute-backfill_{start}_{end}
```

目标日期解析完成后记录 `▶️ workflow`，成功返回后记录 `✅ workflow`；两条日志携带 kind、
start、end 与 targets。Step 对有效输出记录 `♻️ Level-2 minute fact`；对 miss 记录一次
`▶️ Level-2 minute fact`，每完成一个 symbol batch 后至多每 30 秒记录一次
`⏳ Level-2 minute fact`，发布后记录 `✅ Level-2 minute fact publish`。这些日志携带 target、
trade_date 和与事件直接相关的 symbols、进度、tick、行数、耗时或路径，不建立额外 stage
状态机，也不由 Access 或 builder 重复记录。

## 14:30 Feature/Label backfill workflow

14:30 两条派生链以 `*_materialize.py` / `*MaterializeStep` 命名按交易日物化分区的执行步骤，
负责输入读取、Meta 复用和发布。对应 `builders/stock_1430.py` 与
`builders/stock_1430_daily_l2.py` 中的 `build_*` 纯函数接收内存表并返回计算结果。

`run_stock_1430_backfill` 是 CLI-only `data-stock-1430-backfill` 的唯一 workflow，直接消费
已校验的 `Stock1430BackfillSubmission(start, end)`。闭区间精确表示目标 `T` 分区，不表示
H02 分钟或日频 factor 的生产范围。

Workflow 从收到的 `PathManager` 创建唯一固定 `processed_version=v1` Access，通过正式 trade
calendar 把闭区间解析为升序目标 session，只显式组装一个 `Stock1430MaterializeStep` 并调用一次
`DataPipeline.run()`。它不读取配置中的 source、Feature 或 Label registry，不组装 Calendar、
Fact、通用 Feature/Label 或分钟 Step，不创建 broker adapter，也不写 raw、processed、
experiment 或 Job 状态。目标 session 集为空时 Step 仍执行一次并自然成功。

`Stock1430MaterializeStep` 对每个 `T` 固定先处理 `l2_stock_1430/v1` Feature，再处理
`l2_stock_1430_t1_vwap_rank/v1` Label。Feature Meta miss 才通过 Access 读取 `T` 两市分钟事实；
Label Meta miss 才通过 `meta.require` 取得已提交 Feature payload，只读取三字段 key，
再解析下一正式 session `T+1`，并读取 T/T+1
两市分钟事实及 adjustment factor。固定时间、universe、schema、计算和 null 语义由
[`docs/data/stock_1430_feature_label_contract.md`](data/stock_1430_feature_label_contract.md)
所有。

有效 H03 Meta hit 只校验该对象自身 Meta/payload identity，不读取分钟、factor、Feature payload
或当前上游状态。Feature 成功后 Label 失败、或较早目标成功后较晚目标失败时，已经提交的分区
保留；重跑按每个对象 Meta 从首个 miss 续建。H03 Meta 不写 `upstream` 或 `symbol_slices`，
已有关系字段时失败，不覆盖或降级为 miss。

Workflow Instrumentation identity 固定为：

```text
data-stock-1430-backfill_{start}_{end}
```

目标日期解析完成后记录 `▶️ workflow`，成功返回后记录 `✅ workflow`；两条日志携带 kind、
start、end 与 targets。Step 为每个对象提供以 `stock 14:30 Feature` 或 `stock 14:30 Label`
为具名对象、携带 trade_date 和 version 的 `who`；private 发布函数返回复用或发布结果，
由 Step 按共同规则记录，发布时追加行数。错误原样传播。

## 14:30 daily/L2 Feature backfill workflow

`run_stock_1430_fusion_backfill` 是 CLI-only `data-stock-1430-fusion-backfill` 的唯一 workflow。
它消费已校验的 `Stock1430FusionBackfillSubmission(start, end)`，从唯一 PathManager 创建
固定 `processed_version=v1` Access，通过正式 calendar 解析升序目标 T，然后只组装一个
`Stock1430DailyL2MaterializeStep`，调用一次 `DataPipeline.run()`；空目标集合仍执行一次空 Step。

Step 对每个 T 先通过共享 `_publish_partition` 检查输出 Meta。有效命中直接复用，
不解析 P、不读取两个输入或打开输出 Parquet；workflow 的目标范围 calendar 解析仍须完成。
Miss 才用 `recent_trade_dates(end_date=T, sessions=2)` 取得 `[P,T]`，通过 `meta.require`
读取精确 `l2_stock_1430/v1(T)` 与 `tushare_daily_basic/v1(P)` payload。输入读取、输出
schema、对齐和排名由 [`stock_1430_daily_l2_contract.md`](data/stock_1430_daily_l2_contract.md)
拥有。空输出和原子发布继续由共享发布边界处理，不覆盖已有无效 Meta。

Workflow 不读取 source/Feature/Label registry，不构建上游或 Label，不创建 broker，不写
raw、processed、labels、experiment 或 Job 状态；不接入 HTTP、日常 data workflow 或 cron。
任一错误原样传播并终止后续日期；此前已提交分区保留，重跑从 Meta miss 续建。

Instrumentation identity 为 `data-stock-1430-fusion-backfill_{start}_{end}`。
Workflow 在日期解析后记录 `▶️ workflow`，成功后记录 `✅ workflow`，携带 kind、start、end、
targets。Step 的 `who` 为 `stock 14:30 daily/L2 Feature`，携带 trade_date 和 version；
复用记录 `♻️`，发布记录 `✅` 并追加 rows。Access 和 builder 不重复记录分区结果。

## Training workflow

本节拥有训练选择、就绪模型内容、预测返回及 Rank IC 评价语义；物理路径、单文件发布
和读取错误由[存储契约](data/storage_layout.md#experiments)拥有。共享 Workflow →
TrainingPipeline → `run_steps` → Step 结构继续遵循共同边界。

TrainingSubmission 表达一个模型配置的一次训练任务及可选评价，业务输入为 `train_range`、
`model`、`dataset`，以及可选 `eval_range`、`validation_sessions`、`eval_gap_sessions`。
一次 POST 只创建一个 Job；研究中的多模型对照不改变这一任务边界。日期范围是规范 `YYYY-MM-DD` 闭区间；
dataset 分别选择 `feature_group` 与 `label_group`。不接受内部 windows、逐日 train_dates、
训练截止、逐日可用性声明或决策微秒数。请求形状由 CLI / Job API owner 定义，本节拥有
选项含义及执行语义。

模型名称选择 `AppConfig.models` 中的训练配置，特征组名称选择 `AppConfig.feature_groups`
中的身份与有序列；标签组直接使用已有 `label_set/version` 身份，不另建标签别名 registry。
当前提供以下选项：

| 选择 | 名称 | 准确含义 |
| --- | --- | --- |
| model | `sgd_regression` | 新建 SGDRegressor，按配置执行一次 partial_fit；基础配置为 alpha=0.0005、l1_ratio=0.0、random_state=7，缺失特征填 0，不缩放；未显式配置的估计器参数遵循锁定 sklearn 版本，其中 loss=squared_error、penalty=l2，因此 l1_ratio 不改变当前 L2 惩罚 |
| feature_group | `daily_basic_selected_v1` | `tushare_daily_basic/v1` 的七个有序列，见下文 |
| feature_group | `daily_basic_all_v1` | 同一日频 Feature 的全部 16 个数值列，按数据 owner 的 schema 顺序 |
| feature_group | `l2_stock_1430_all_v1` | `l2_stock_1430/v1` 的全部 32 个数值列，按数据 owner 的 schema 顺序 |
| label_group | `daily_close_return_rank_d1/v1` | 当前收盘到下一正式交易日收盘的复权收益横截面排名 |
| label_group | `daily_close_return_rank_d3/v1` | 当前收盘到第 3 个后续正式交易日收盘的复权收益横截面排名 |
| label_group | `daily_close_return_rank_d5/v1` | 当前收盘到第 5 个后续正式交易日收盘的复权收益横截面排名 |
| label_group | `l2_stock_1430_t1_vwap_rank/v1` | T 日 [14:31,14:36) 到下一正式交易日同一窗口的复权 VWAP 收益横截面排名 |

新增模型预设与盘前数据选择如下；完整估计器参数保存在 base.yml 和每次 schedule。
所有新增模型缺失填0.5、不缩放，日期等权。Ridge 使用 alpha=100、cholesky；Logistic 使用
C=0.01、lbfgs、max_iter=1000、tol=1e-8。两个直方图梯度提升模型共同使用 max_iter=100、
max_leaf_nodes=7、max_depth=3、min_samples_leaf=500、learning_rate=0.05、
l2_regularization=10、max_bins=63、early_stopping=false、random_state=20260919。
拟合与预测限定一个数值库线程；ConvergenceWarning 作为拟合失败。

| 选择 | 名称 | 含义 |
| --- | --- | --- |
| model | ridge_regression / logistic_regression | 完整 fit 的排名回归 / 二元事件分类 |
| model | hist_gradient_boosting_regressor / hist_gradient_boosting_classifier | 完整 fit 的浅树排名回归 / 二元事件分类 |
| feature_group | previous_day_daily_v1 | 盘前数据的7列日频排名 |
| feature_group | previous_day_daily_l2_price_v1 / previous_day_daily_l2_activity_v1 / previous_day_daily_l2_direction_v1 | 保留39列原顺序，选择日频加对应8/16/8列 L2，共15/23/15列 |
| feature_group | previous_day_daily_l2_v1 | 完整39列 |
| label_group | next_day_high_return_rank/v1 | y_rank_return |
| label_group | next_day_high_top_decile/v1 | y_event；严格原排名>0.9 |

这些特征组都指向 previous_day_daily_l2/v1；只有两个 next_day_high 标签可以搭配盘前
特征组，分类模型只搭配事件标签，回归模型只搭配排名标签。数据公式、共同资格与条件
可用时间由[前日数据 owner](data/previous_day_feature_label_contract.md)唯一拥有，训练不重新排名。
价格组选择 edge_vwap/high_low 列，方向组选择 tick_signed 列，其余为活跃度组。

七列的实际训练顺序为 `f_d_close_return_1d`、`f_d_open_gap_1d`、`f_d_log_amount`、
`f_d_max_drawdown_20d_asof_tminus1`、`f_d_close_volatility_60d_asof_tminus1`、
`f_d_close_return_5d_asof_tminus1`、`f_d_turnover_rate_mean_20d_asof_tminus1`。
两种日频特征组只允许搭配三个日频标签；14:30 特征组只允许搭配 14:30 标签。
已有四种标签均读取 `y_rank_return`，预测目标是排名分数，不是收益率本身。
未知名称和不同决策网格的组合在 submission 构造时拒绝，不创建 Job。
研究候选 `stock_1430_daily_l2/v1` 不在选项中，分区存在不表示可用于本入口。
Feature producer 拥有数值含义、价格口径、排名细节和可见性，训练及消费侧不重新计算；
完整定义由[日频 owner](data/daily_feature_label_contract.md)及
[14:30 owner](data/stock_1430_feature_label_contract.md)拥有。

Workflow 在任何数据 I/O 和 Instrumentation 前查找所选配置并通过不可变 catalog 取得
trainer；未配置的已知名称或不支持的模型实现必须失败。解析后的 ModelConfig 组合
该训练配置与实际 Feature/Label 身份、非空有序且不重复的 feature_columns 和 label_column。
拟合后的特征顺序由 preprocessor 唯一持有，模型不另存参数投影。

Workflow 从唯一 PathManager 创建 Access，按正式日历解析每个闭区间中的全部交易日，
两端允许休市日，不以周一至周五近似交易日。空训练区间或显式空评价区间失败；省略
eval_range 或为 null 表示不请求最终评估。最终评估样本必须晚于整个 train_range，单日评估使用相同
起止日期，多日预测始终使用同一个已冻结模型。实际日期非空、升序且唯一，不从文件列表或过滤后的拟合样本
重算；某日样本全被过滤，模型仍保留该训练日期。缺少目标日期的必要输入必须失败，
不缩短区间。Job scope 和 experiment 名称的 start/end 是请求区间的整体包络，不重新选择样本。

日频 d1/d3/d5 的最低标签成熟边界为 T+h 15:00，14:30 标签为 T+1 14:36，h 和下一
session 由精确标签定义及正式日历解析。最后一个训练样本的成熟时点不得晚于首个评价
决策；日频评价决策固定为当日 15:00，14:30 评价固定为当日 14:30。违反时在训练前失败，
不能靠过滤未知标签掩盖尚未成熟的日期。纯训练不虚构历史训练截止，只要求当前完整输入
可消费，同时记录所选训练标签的理论成熟下界供消费侧核验必要条件。

这是固定样本的离线训练与评价。Meta 只证明当前 payload 可消费，没有历史实际可用时间；
当前文件存在、mtime、理论成熟时间或本次运行时间都不能证明历史上已经拿到相同输入或
完成训练，也不能排除同版本事后修订。成熟校验防止已知的时间泄漏，但不构成历史部署
或严格 point-in-time 回放证明；此类结论仍须真实输入修订及模型可用记录，不能由普通
训练请求的客户端声明代替。

训练前在 schedule.json 保存一个 TrainingRunRecord 对象，字段精确为 `request`（已校验
请求的 JSON，包含默认值）、`train_dates`（实际拟合日期）、`validation_dates`、`purged_dates`、
`eval_dates`（最终评估日期）、`model_ref`、`configuration`
（解析后的 ModelConfig）与 `label_maturity_ts_us`（上述理论下界）。消费侧读取解析字段，
不把归档 request 重新反序列化为公共请求 DTO。记录日期与配置可解释当次选择，但不归档
全部训练输入、代码或环境；不增加模型内时间字段或发布确认字段。计划中的 ref 不证明
文件已经成功发布。

DatasetBuildStep 只读取 train_dates，通过数据侧
[`read_features` / `read_labels`](engineering/access.md#featurelabel-分区读取)消费完整样本键及
有序数值列。Feature/Label 的键、值和顺序必须精确一致，不执行 join；只删除 label 为 NaN
的行，保留 feature NaN。日频键为 `(symbol, trade_date)`，决策分区还保留
`decision_ts_utc`，不得丢掉第三列后匹配。正负无穷是错误，不转为缺失或零。

FittedPreprocessor 继续唯一拥有训练列、拟合状态与 transform。Constant 使用配置有限数；
mean/median 只在训练行拟合，全缺失列失败；drop 返回原行长 mask 与保留行转换结果，
训练侧按同一 mask 同步选择标签，全部训练行丢弃则失败。评价及 runtime 使用已拟合状态，
不重新 fit。每个任务由 catalog trainer 从新对象训练；当前 SGD 仍只调用一次 partial_fit，
SGD 的 `model_params.random_state` 必须显式给出整数，禁止缺失、None 和 bool；
当前 base.yml 明确选择 7，不在 trainer 内补默认种子。种子与输入内容、顺序、一次
partial_fit 及锁定环境共同构成确定性重训条件，不承诺任意算法或跨环境逐位一致。

ModelPreset.sample_weight 为 uniform（默认）或 date_equal；后者在删除未知标签后按日期
赋予每行 1/该日有效训练行数，再将全部权重归一到均值1，并传入 trainer。预处理若删除行，
同步选择权重并重新归一；未删除行不二次归一。不能按未来标签筛选推理样本。

每次训练一次构造完整 InferenceModel，内容精确为：

```text
model, preprocess, feature_set, feature_version,
train_dates, label_set, label_version, label_column, prediction_method
```

prediction_method 为 value 或 positive_probability。后者要求 classes_ 恰为[0,1]的
二元概率模型，返回 predict_proba 的正例列，不能用0/1类别值进行 Top50 排序。

`feature_names` 仍来自 preprocess。来源身份来自本次实际消费的已校验配置，train_dates
直接来自窗口；保存与加载不重新校验这些来源字段，回测只在运行绑定边界核对它们与所引
训练记录的关联。模型内不建立 provenance 外层、独立来源容器、配置副本或输入快照。
模型预测返回 float64 ndarray，长度与输入相同、顺序
相同；drop 行为留下 NaN，零是有效预测。没有保留行时返回等长全 NaN（空输入返回空数组）
并不调用预测器。预测器返回的维度、行数及有限性必须在回填前验证，非法输出抛 RuntimeError。

一次任务只拟合一个模型，steps 为 dataset load → train-only preprocess → fresh model train
→ ArtifactPersistStep；需要内部验证或最终评估时接 PredictionStep → ICEvaluateStep
→ MetricsPersistStep。TrainingPipeline 执行一次链，返回 None。模型保存不等待评价；后续
错误保留已交付模型。TrainingWindow 包含实际 train_dates、model_ref 和逐日 EvaluationDay，
后者包含日期、决策时间、预测路径和 validation/evaluation 阶段。

validation_sessions 为非负整数，默认0。大于0时，将 train_range 的最后 N 个正式交易日
留作内部验证，其余日期作为拟合候选。按标签成熟时间剔除在首个验证决策时尚不可用的
候选日期，记录 purged_dates；实际拟合日期不能为空。预处理与样本权重只在实际拟合行上
计算。模型参数已由配置固定，验证仅计算独立指标，不做调参、早停、追加训练或全区间重拟合。
验证后的同一模型继续用于 eval_range，最后评估标签不参与训练和验证。所有分区仍需完整；
边界隔离是显式日期划分，不是缺数据时的缩短区间。

例如一个134日训练区间留27日验证，若次日22:00才有标签而验证在09:00决策，则分界前
1日需要隔离：106日拟合、1日隔离、27日验证。另设17日 eval_range，这17日始终只用于
最终评估。134和17是请求日期在正式日历上的计数，不是服务端硬编码的周期。

盘前标签按 T+1 22:00 成熟，其他标签保持原理论成熟时点。首个最终评估决策不能早于
整个 train_range 最后日期的标签成熟时点；内部验证不能绕过最终评估的信息边界。
当前输入版本和情景就绪时间不证明真实历史部署。

eval_gap_sessions 表示 train_range 最后一个交易日与 eval_range 首个交易日之间至少
跳过的交易日数，默认0，不含两端，休市日不计数。周五结束训练区间、周一开市时，0允许
最早周一评估，1要求最早周二。违反间隔失败，不暗改显式日期；间隔不替代标签成熟检查。

验证和最终评估的逐日预测共享 predictions 目录，阶段由 schedule 中的日期清单确定；
IC 分别写 validation-metrics.json 和 metrics.json，不混合汇总。盘前 Top50 结果按 stage
分别汇总并生成 HTML；其他模型使用已有诊断报告，最终评估指标不混入内部验证。

盘前评价读取保存的模型分数，先按分数降序、symbol升序冻结 Top50，缺分数排末尾；然后
连接原排名标签。资格候选保留未知标签，不补选。令 N 为有效池、P 为池中排名>0.9数、
K 为固定名单中有效数、H 为命中数，则 Precision=H/K、Recall=H/P、F1=2H/(K+P)、
随机基准=P/N、Lift=Precision/随机基准；零分母为 null。保存全部原数量、未知/有效数量
与逐日名单，池覆盖率=有效池/原池、名单覆盖率=有效名单/原名单；按日期等权汇总，
旧固定分母 Precision 下界/上界单列保留。五个数据规则
对照分别按原成交额、动量、反转、波动率排名、换手率排名选取，均不额外训练模型或创建 Job。

Workflow 在解析模型及数据组身份、确认 experiment 未存在后，进入日历读取前输出一条
“训练任务”开始事件：中文表格说明模型、特征组及列数、标签组、请求训练/评价区间、
特征缺失处理、内部验证日数与拟合方式；表外解释当前标签的预测目标及排名分数量纲。
完整参数、特征顺序和实际交易日由已有 schedule.json 归档，日志不展开全部参数及列名。
准备阶段失败不宣称已写入运行配置。只有 Pipeline 正常返回，workflow 才写任务完成事件；
纯训练明确写“本次未请求评价”，提供模型和运行配置路径，不宣称生成 metrics 或 HTML。

过程日志由掌握事实的阶段产生：数据准备以首末日期及交易日数表示范围，分别记录原始
Feature 行数、因标签缺失移除的行数和标签筛选后行数；预处理记录含缺失特征的行数、
填充的单元格数、移除行数及实际拟合行数。填充与删行是不同动作，不能共用“跳过”计数。
SGD 拟合前输出样本数与列数，partial_fit 返回后读取 n_iter_ 记录实际轮数；不在阻塞的
fit 内虚构百分比或收敛进度。模型成功写入时记录具体 model_ref，保证后续评价失败仍能
定位已交付模型。预测和 IC 评价各输出开始及一条批量完成摘要，不再逐日重复长路径；
逐日记录继续保存于原有预测 Parquet、metrics 和 HTML。

DatasetBuildStep、PredictionStep 和 ICEvaluateStep 由 workflow 注入单调 clock；循环在
完成一个交易日后，距上次进度至少 5 秒且仍有剩余日期时才输出累计日期进度。开始、完成
及失败事件不受此限制，不增加后台心跳线程。逐日读取或写入失败时，在原异常上附加日期
及相关 ref 的 note 后重新抛出；报告材料失败同样附加定位信息，不补默认值或重写已有制品。
原始 traceback 仍仅由最外层终止边界输出，已成功保存的模型及前序预测保留。

PredictionStep 加载这个具体已发布 ref 一次，按每个 EvaluationDay 用模型的特征身份及
顺序读取评价 Feature，保留全部原样本键，调用同一 predict，然后写入 workflow 指定的
逐日路径。保存位置由本次运行拥有，不从模型 ref 隐式推断；不同日期分别保存，不能覆盖
前日记录。全部预测完成后才进入标签评价。正常无预测行也保留，NaN 写为 Parquet null。
公共 `save_prediction_record` 同时保存模型字节及实际同序 Feature 表的 SHA-256，
metadata 由 `PredictionInputs` 定义；这只检测材料变化，不复制历史输入或证明历史可用。
`verify_prediction_record(ref, pm=...)` 重新读取所声明分区，以完整原键选择原顺序，
核对输入及模型摘要，再严格比较等长 float64 预测（包括 NaN 位置）；失败不重写原记录。
输入和代码/环境保持可恢复仍是任务责任，不将摘要解释成数据归档。

ICEvaluateStep 遍历窗口的逐日 prediction_ref，通过共用 `evaluate_prediction_record` 读取实际
落盘预测，随后读取模型自身标签身份及列对应的 Label。按完整键选取记录对应的标签并保持
预测顺序；记录中的键在标签中不存在是错误，存在键上的 NaN 才是普通缺失。评价允许
使用侧先按当时可见资格选出的输入子集，标签中的其他样本不能补入名单。缺失标签不能
提前改变预测输入行集合。该函数不调用 predict，可独立重跑，再追加 ICObservation。
TrainingPipeline 每次 run 新建 Context，观察不泄漏到另一运行；随后 MetricsPersistStep
保存逐日观察及汇总，ReportStep 从这个明确模型 ref、metrics 及其引用的逐日预测与标签
构建报告；训练诊断使用本次 Context 保留的实际拟合样本，口径见下文。
公共返回值为 None。

共用纯函数 `rank_ic(predictions, labels) -> (value, pair_count, undefined_reasons)` 不加载模型、
不做 I/O 或记录日志。它只使用同序有限配对，NaN 是普通缺失，Inf 和非法数组是错误；
平均并列排名的 Pearson 相关即 Rank IC。有定义时返回有限 float 与空原因 tuple。数学
无定义时返回 None，并按以下顺序交付全部成立原因：

- `insufficient_pairs`：配对少于 2；
- `constant_prediction`：至少一个配对且预测全部相同；
- `constant_label`：至少一个配对且标签全部相同。

零配对仅有第一项；一个配对三项全部成立；其他无法解释的非有限计算结果抛 RuntimeError。
正常无定义不阻止模型交付或评价记录，不补零、不删日期、也不设最小横截面 50 的默认门槛。
每项观察字段为 model_ref、decision_ts_us、input_count、no_prediction_count、
missing_label_count、value、pair_count、undefined_reasons。其中 missing_label_count 只计
有预测但缺标签的行，满足 input_count = no_prediction_count + missing_label_count + pair_count。
从持久化预测产生的观察还包含 prediction_ref、label_ref 和 label_sha256，明确本次评价
采用的预测及标签修订；直接评价内存数组时三者为 None。标签改变会产生另一评价事实，
不会改变已保存的预测。

Metrics JSON 有 observations 与 summary 两项。Summary 精确包含 date_count、
defined_date_count、mean_ic、undefined_reason_counts；均值只按有定义日期等权计算，
全无定义则 mean_ic=null，所有原观察仍保留。None 写为 null，原因 tuple 写为数组，
序列化禁止 NaN/Infinity。Metrics JSON 继续保存上述 IC 事实与来源；以下附加诊断写入
HTML 报告和 ReportStep 日志，不改写 metrics 或增加制品。

ReportStep 的结束摘要必须包括实际训练日期、拟合样本数、特征数、实际训练轮数、评价
日期及行覆盖、核心指标，以及模型、metrics、report、schedule 的完整路径。训练样本数是删除缺失
标签并完成预处理筛选后的实际行数；实际轮数读取已交付 SGD 模型的 `n_iter_`，不能把
配置的 `max_iter` 当作实际轮数。当前一次 partial_fit 对应一轮，报告步骤不再次训练。
日期展示为可读日期；逐日决策时点由 UTC 微秒转换为带偏移的 `Asia/Shanghai` 时间。

摘要同时给出以下指标，日志与 HTML 必须使用同一次计算结果：

- IC 均值继续按有定义日期等权计算；波动使用这些日期的样本标准差（ddof=1），不足
  两个有定义日期时无值。正 IC 天数只计 IC > 0，比例的分母是有定义日期数；零 IC 不计
  为正，全无定义时比例无值。全部评价日期及无值原因仍保留，不能删除无值日抬高覆盖率。
- 回归 MAE、MSE、RMSE、R² 合并全部评价日的有限预测与有限标签配对，按样本等权计算，
  不平均逐日误差。IC 无定义日仍可能有可用误差样本，例如单配对日。零配对时误差无值；
  不足两个配对或标签为常量时 R² 无值。R² 使用合并样本的标签均值计算总平方和，
  不将负值截成零。评价输入仍分为无预测、有预测但缺标签、有效配对三部分。
  样本覆盖率为有效配对数 / 评价输入行数，输入为零时无值；IC 日期覆盖率为有定义日期数 /
  全部评价日期数，两者不得互换。计数标明样本行或交易日，不把样本行数当成股票数。
- 误差基线在完全相同的有效样本上固定预测排名 0.5，不从评价标签拟合基线参数。
  输出基线 RMSE 及“模型 RMSE − 基线 RMSE”，负值表示模型误差更小；基线 RMSE 大于零时，
  同时将该差值 / 基线 RMSE 解释为误差高或低的百分比，差值为零时说明相同。基线 RMSE
  为零时保留绝对差值，相对变化显示“无值”，包括双方误差都为零的情况，不输出 Infinity。
  R² 的基线是各自样本集的标签均值，与固定预测 0.5 不同；负 R² 表示误差大于均值基线。
  这些比较针对排名标签，不表示交易收益或收益率误差。
- 训练 MAE、MSE、RMSE、R² 使用本次实际拟合的已转换特征与对应标签，由已交付模型的
  原始估计器预测后计算；不得重复应用 preprocessor，也不重新读取可能变化的训练分区。
  输出“评估 MAE − 训练 MAE”和“评估 RMSE − 训练 RMSE”。训练误差是拟合内误差，
  差距也可能来自样本分布变化，不能单凭差值宣布过拟合或模型可上线。

报告读取评价标签时复用 IC 评价的完整键对齐，并核对标签路径与内容摘要仍属于 metrics
记录的同一修订；预测中的模型引用、模型摘要及决策时点必须与所报告的模型和观察一致。
发现材料不一致时失败，不能混合旧 IC 与新标签误差。评价误差只使用已保存的预测，
不重新预测评价集。报告保留模型完整来源、逐日观察、无值原因和覆盖率；数学无定义在
HTML 和日志均显示“无值”，不补零或隐藏。

ReportStep 先记录“诊断计算与报告输出”开始事件，在 HTML 成功发布后写入一条 INFO 级
多行“训练报告”事件，首行保留状态符号和 `experiment=...` 定位上下文。正文先给出事实
解读：平均每日 Rank IC 为正、负、零或无值；正 IC 日期数及其比例；评估 RMSE 对固定
0.5 基线的绝对及相对比较；实际轮数与未监测收敛。全无有效配对明确说无法评价回归误差；
IC 无定义不代表没有可计算误差的样本。该解读与 HTML 使用同一实现，不加模型好坏、通过
或可上线的阈值。正 IC 日比例不能称为准确率或交易胜率；排序方向与数值误差可能表现不同。

随后按“训练与评价覆盖”“排序表现”“误差与基线”输出三张中文文本表格。正 IC 日期数和
比例合并展示；误差表同时比较训练、评估及已有的 MAE/RMSE 差距。列按中英文显示宽度
对齐，计数使用千位分隔，指标保留六位小数，比例显示两位小数的百分数，差值显示正负号；
差值和比例仍按原始精度计算，不用已舍入的显示值相减。此规则只影响展示，不改变计算或
metrics。制品按“模型”“IC 指标 JSON”“完整诊断 HTML 报告”“运行配置”准确命名，避免
把仅含 IC 的 JSON 称为全部指标；四条路径各占一行并完整保留，避免挤宽表格。沿用公共
日志进入 CLI stderr 及 Job 日志，不新增日志文件写入器，也不回写历史 Job 日志。
当前没有逐轮损失历史，诊断明确显示逐轮损失未记录、收敛尚未评估；实际
轮数和最终误差不能证明收敛。需要观察收敛时，再明确逐轮损失的采样方式和训练行为，
不能在报告阶段重训或虚构损失曲线。


## Backtest workflow

本节唯一拥有五种回测任务、交易编排、账户与执行假设、策略和风险规则及输出解释。
任务字段与 CLI/HTTP 映射分别由 [CLI](engineering/cli_contract.md#backtest) 和
[Job API](engineering/job_api_contract.md) 拥有。数据含义、交易日历、Feature/Label 的
可见时刻和价格口径继续由对应数据 owner 定义。回测参数统一来自各自的 submission；
signal/strategy/risk/full 使用其中的 `min_listing_calendar_days` 在模型推理时筛选股票资格，
已有预测记录只按请求日期选择，不按本次参数重新筛选证券。该值随任务记录，不从应用
配置补齐或覆盖；现金、成本、时点、数量规则和风险阈值也必须由交易任务提供。
不从旧代码的初始资金、费率、滑点或 NoOp 反推业务默认值。

### 五种任务与组装

五种 mode 表达评价目的，通过输入和账户运行配置，共用一条五段 BacktestPipeline。
信号与冻结委托的区别是指令来源，risk 关注指定风控在账户路径上的控制效果；这些差异
不定义多条工作流，也不需要按 mode 分派到不同的账户执行入口。输入具备什么事实，
就计算相应指标，不因 mode 名称隐藏已有的信号、成交或收益评价。

| mode | 回放中的决策行为 | 单账户配置 | 运行及评价 |
| --- | --- | --- | --- |
| `signal_eval` | 信号、策略、目标提交 | 已保存预测，或给定模型产生的新预测；明确策略和账户；无业务风控；`quoted` 成交 | 一次；预测质量及按申报价成交的账户表现 |
| `strategy_eval` | 信号、策略、目标提交 | 信号、所选策略和账户；无业务风控；明确 `quoted` 或 `limit_price` | 一次；信号、成交及组合指标 |
| `risk_rule_eval` | 信号、策略、风控、目标提交 | 信号、策略、账户、非空风控；明确 `quoted` 或 `limit_price` | 一次；规则覆盖、控制动作、实际暴露与剩余风险，附成交及组合指标 |
| `execution_eval` | 固定委托/撤单提交 | 固定指令、账户及 `limit_price`；无业务风控 | 一次；成交、费用、收益、回撤及期末持仓 |
| `full_backtest` | 信号、策略、风控、目标提交 | 信号、策略、非空风控、账户及 `limit_price` | 一次完整反馈回放；信号、风控、成交及组合指标 |

`run_backtest` 公共返回值为 `None`。每个请求声明一个场景，一次运行使用一个账户、
一份指标和一个输出目录。risk/full 配置指定业务风控，其余模式无业务风控；不为 risk
创建隐式基线、第二个账户或对照收尾链。Workflow 仅绑定依赖并按下列顺序构造不可变
startup_steps、tick_steps、result_steps 三组 Step tuple。BacktestPipeline 创建 Context，
在同一次 Instrumentation 作用域内顺序执行启动组，遍历动态时钟执行逐时点组，然后执行
结果组；每一组都调用共享 run_steps 并接续它返回的 Context。

```text
startup_steps:
  PrepareInputsStep → InitializeAccountStep

tick_steps，逐时点执行:
  UpdateMarketStep → AdvanceOrdersStep → ValueAccountStep
  → 信号路径：GenerateSignalStep → GenerateTargetsStep → [ApplyRiskStep] → SubmitTargetsStep
    或冻结委托路径：SubmitFrozenCommandsStep
  → ValueAccountStep → [CompleteRiskObservationStep] → ScheduleWakeupsStep

result_steps:
  FactsPersistStep → EvaluateResultsStep → PublishResultsStep
```

方括号中的两个 Step 仅在配置业务风控时共同装配；冻结委托路径不装配信号、策略与风控
Step。分支由 workflow 构造 tuple 时确定，Pipeline 不在每个 tick 中检查 mode、输入类型
或具体 Step 类型。策略、成交和费用配置绑定在 InitializeAccountStep；GenerateTargetsStep
消费它创建的本次构造器，避免重复保存一份策略配置。GenerateSignalStep 绑定预测路径，
SubmitTargetsStep 绑定订单窗口，估值前后复用同一种 ValueAccountStep，位置表达先后，
无需额外阶段开关。

| 业务阶段 | 实际 Step 与完成边界 |
| --- | --- |
| 准备输入 | PrepareInputsStep 读取并校验输入，写入 BacktestInputs；不推理、不读标签 |
| 账户回放 | InitializeAccountStep 创建本次运行对象；各 tick Step 逐项推进账户、决策和执行，时钟耗尽后结束 |
| 保存事实 | FactsPersistStep 保存 execution/equity、orders、风控前 targets，以及存在的 risk-cases |
| 评价结果 | EvaluateResultsStep 此时才读取评价标签，汇总已保存的事实及实际预测引用，不重新交易或执行风控规则 |
| 发布结果 | PublishResultsStep 先保存 metrics，再由 ReportingBundle 重读生成 HTML 与摘要；报告成功才算整个运行成功 |

五段继续表达输入可用、交易完成、事实已保存、评价完成和结果已发布的边界。逐时点动作
在装配处显式可见，读者不必进入一个总控 Step 才能找到信号、风控和订单的先后关系。
每个 Step 只完成一个业务动作，直接使用现有领域能力；Step 内不再运行另一条 Step 链，
不增加通用运行计划、registry、空策略或 NoOpRiskManager。

PrepareInputsStep 严格读取一次行情，检查正式日历覆盖及与初始账户、风险检查时点、
执行配置的一致性，将 `ReplayMarketInput` 与决策输入写入 BacktestInputs。
行情输入提供不依赖推进状态的 `session_at()`
和 `timestamps()` 查询，准备、委托日程及后续时钟组装直接消费它，不为校验构造临时
ReplayMarket。信号输入由 `prepare_signals` 一次准备；冻结委托由
`OrderCommandSchedule.from_files()` 严格解析为 OrderIntent/CancelOrders，再调用该类
构造函数。日程统一检查时间顺序、请求日期及模拟区间，成功后建立只读索引；市场、日期
和时间边界不保存为日程状态。同刻指令保留文件与数组的原始顺序并保存为 tuple，不借用
调用方的可变容器。`timestamps()` 返回唯一指令时点，`at(ts_us)` 返回该时点 tuple，
无指令时为空；查询不消费指令。下游不重复校验或分组。

InitializeAccountStep 每次运行创建独立 PortfolioState、ReplayMarket、执行器、时钟、
挂单和 ledger；信号路径另建策略，有风控时另建 RiskManager。账户保留初始可卖数量与
权益峰值，执行器装配费用和成交模型，固定持有策略初始化校验初始退出计划并登记时钟。
ReplayMarket 保存本次可见价格、读取游标和观察批次；初始化不消费时钟、不生成预测。
成功创建后才把这些对象交给 Context。workflow 不读取策略内部计划，不装配费率公式
或时钟来源。InitializeAccountStep 按实际配置记录 applied_risk 与 not_run。交易、风险、
执行、唯一 SettlementEngine 和指标公式继续使用共同领域实现，不为各模式增加 Pipeline
子类或另一套公式。
同一信号、策略、账户和成交参数下，signal_eval 与 strategy_eval 的账户结果及信号指标
必须相同；名称只描述目的，不能暗中改变交易或指标公式。risk 与 full 在相同信号、策略、
账户、风控及 limit_price 配置下也必须产生相同的账户事实和评价，仅 mode/请求标识不同。

冻结委托可以理解为已完成策略决策后的指令输入，在订单提交边界接入公共回放。
它保留委托数量、提交时间、订单条件和撤单顺序，不重新执行组合构造。例如，已有 50 股时，
“买入 100 股”与“目标持仓 100 股”分别意味着新增 100 股和新增 50 股；不能为了统一入口
把固定委托转换成信号分数或目标仓位。冻结的是指令，成交和账户状态仍由公共执行与记账能力计算。

BacktestPipeline 只负责三组执行顺序、动态时钟循环、每刻新建 TickState，以及进度和
异常上下文；不创建或筛选业务 Step。单时点的必要先后关系由 tick_steps tuple 表达：

```text
更新可见行情、切换交易日 → 推进旧订单与结算 → 决策前估值
→ 信号/策略生成并记录原始目标 → 可选风控 → 提交目标/撤单
  或按原顺序提交冻结委托/撤单
→ 提交后估值 → 可选风控 outcome → 登记策略及订单到期唤醒
```

GenerateSignalStep 从准备好的只读索引取当前批次，调用共享 produce_signal；模型输入
此时才推理和保存预测，已保存预测不重复推理。每个唯一时钟时点只执行一次信号 Step，
因此每个决策只生产一次，无需另建跨时点预测缓存。它记录原信号、实际预测引用及决策
日期，非信号时点保持当前 signal 为 None。

GenerateTargetsStep 在普通策略有本时点信号时生成目标；fixed_holding 每个时点推进
有状态日程，允许没有新信号时退出。缺分数时保留持仓与挂单承诺，目标记录到原始 tape，
并同时设为当前 execution_target。ApplyRiskStep 在每个时点固定原始请求与检查前快照，
调用本次 RiskManager，仅更新 execution_target 和撤单请求，保持 original_target 不变。
因此未装配风控时原始目标自然进入提交；配置风控后，即使没有新信号或策略目标，也要
检查现有承诺。None 目标表示不提交，显式零数量目标表示清仓。

SubmitTargetsStep 只为同方向挂单不能覆盖的新增数量计算订单期限；缩减旧订单保留原报价
与到期时刻，不要求新的未来交易窗口。SubmitFrozenCommandsStep 保持同刻指令的原数量
及次序。ScheduleWakeupsStep 合并挂单到期与固定持有策略的新唤醒，排序去重后登记到
本次时钟；动态时钟在明确终点停止。

成交由显式 execution.fill 选择 venue。提交阶段可能立即结算，因此提交后再次执行
ValueAccountStep；决策前估值服务于当前目标，提交后估值包含新成交及费用，针对的是
不同账户状态。同刻 EquityCurve 保存最终快照，首次决策前权益与既有峰值仍保留为收益、
回撤基准，并在正式收盘时记录日末权益。有业务风控时，观察暂存在 TickState；第二次
估值后 CompleteRiskObservationStep 保留原 case 与 decision，复制实际权益、现金、峰值、
持仓及挂单得到 outcome，再一次写入 State。它不重算规则或推断成交，也不覆盖观察列表
最后一项。下一时点检查前快照已经包含期间成交和估值变化，两者共同描述动作与后续反馈。

EvaluateResultsStep 只评价已完成并保存的交易事实。有信号输入才读取标签并计算信号评价；
有实际业务风控才汇总已记录的观察。每条路径均计算成交与组合指标，不把 execution_eval
限制为订单统计。最后从同一 State 提取期末账户与实际预测引用，再交给 PublishResultsStep。

### 风控有效性评价

risk_rule_eval 要回答的问题是：在明确的策略、账户、行情与成交约束下，指定风控能否实现
声明的控制目标；什么情形下仍有风险、剩余多少、持续多久，执行中发生了什么。
它默认运行一次带指定风控的完整账户回放。名称中的 rule 不将任务降为纯规则函数测试，
也不隐含一条无风控收益基线。

**为什么这样定义。** 风控首先承担风险预算与损失响应职责。规则正确改变目标只是控制链
的中间产物，风险由实际持仓、尚可能成交的买单和市场状态共同决定。例如单票上限 20%，
规则把目标缩到 20%，但账户原来有 40% 持仓，卖单因 T+1 或无流动性无法完成，此时规则
计算可以正确，控制目标仍未实现。只核对输出会漏掉这个风险；只观察收益也无法证明
控制有效，违规持仓恰好上涨仍然可能赚钱。

三个问题需要不同证据，不能用同一个“通过”替代：

| 问题 | 所需证据 | 在本设计中的位置 |
| --- | --- | --- |
| 给定快照时规则算得对不对 | 已知答案的正常、边界、异常案例；核对目标及撤单 | RiskCases 与单元/回归测试，是有效性评价的前提 |
| 风控在反馈交易过程中是否达到目标 | 本账户的前后快照、控制动作、真实模拟成交、残余风险及执行约束 | risk 的主要结果 |
| 相比某基线改善什么、付出多少增量代价 | 明确基线的受控比较、相同数据与执行假设、独立账户路径 | 独立的策略/风控研究，不是 risk 的必选执行步骤 |

第三个问题有价值，但“相对改善”不能替代“满足约束”。例如风险暴露从基线的 80% 降到
40%，相对改善仍不满足 20% 上限；反之，合规减仓使上涨阶段收益减少，也不能仅据收益差
判定风控无效。是否值得采用某套风控，需要结合投资目标、风险偏好、多个场景和费用
再作决定，单次 risk 报告不自动作采用决定。某些限额本身就是必须满足的约束，关闭所有
风控也未必是有业务意义的比较基线。

规则正确性、控制效果和经济价值因而分别保留证据。删除默认第二账户后，控制目标、
动作、成交及剩余暴露仍能完整表达并验证，所以第二账户不是 risk 的必要实体。
确需比较时可以用显式配置分别运行公共账户流水线，比较的是完整反馈路径：两次均独立
推进现金、持仓、挂单和策略状态，不能把基线目标序列固定为有风控路径的策略反馈。
当前不增加比较平台、自动基线或比较 API。

**从目标到可观察事实。** 当前三条规则分别评价：

| 配置 | 风险度量与控制目标 | 未消除风险的金额口径 |
| --- | --- | --- |
| max_single_name | 正权益下每个证券的持仓加未成交买入承诺不得超过 max_ratio × equity；value 为其中最大比例 | 各证券超出各自上限的金额之和 |
| max_gross_exposure | 正权益下全部持仓加未成交买入承诺不得超过 max_gross_ratio × equity；value 为总比例 | 总承诺金额超过上限的部分 |
| max_drawdown | 达到阈值时要求清仓；value 为 (peak_equity − equity) / peak_equity | 当本时点检查前或执行后达到阈值时，仍存在的持仓与买入承诺总金额 |

持仓按当时可见价格估值，买入承诺按剩余数量乘同一可见价格；未成交卖单不能抵扣持仓。
这同时保留全账户的 held_notional 和 pending_buy_notional，区分已经承担的暴露与可能新增的暴露。
待消除金额既可以通过已成交减仓降低，也可以通过取消未成交买单降低，不全部等同于卖出金额。
单票度量覆盖持仓和买入承诺的全部证券，不能只检查策略本次给出的目标。
边界判断及金额计算采用输入十进制值，避免恰好到达比例上限时产生浮点假超限。
不将各条规则的金额相加作为组合风险，因为同一持仓可能同时属于多个约束。
同类规则允许按配置顺序分别列出，不按规则名覆盖不同阈值。

回撤规则是损失发生后的响应机制，不是亏损上限保证。跳空可能一步越过阈值，T+1、
部分成交和无流动性都可能推迟清仓；即使已清仓，历史峰值对应的回撤仍在。因此报告
保留实际回撤值，同时以尚未消除的暴露评价清仓响应，不能把清仓后仍高于阈值的回撤
当成仍未清仓。此评价不新增永久停机/锁仓语义；规则每次仍按当前快照决定。
费用造成执行后首次达到阈值时也记录残余暴露，后续检查才能作出新动作，不回填过去决定。
非正权益使敞口比例无定义，非正峰值使回撤无定义；保存 null 与明确原因，不能视为满足
目标。引擎的非正权益保护仍保留在原规则观察中，不凭评价器推断一个新的交易规则。

**场景与证据范围。** 一个请求就是一个明示场景，使用已有行情、初始账户、数量/费用、
成交模型及风险检查时点表达正常、边界或压力条件。压力检验应覆盖价格跳变、无法成交、
部分成交、T+1、挂单与费用反馈等实际失效模式；当前不增加自动压力生成器或新请求字段。
quoted 只能证明其全额成交假设下的效果，不能证明流动性或触价约束下也有效；单一历史
区间也不能证明其他市场状态安全。无样本、未触发或无定义均不能输出“风控验证通过”。

评价器只消费已记录快照和账本，不重新执行规则、不读取未来价格，不利用事后成交修改
历史观察。逐时点输出检查前及提交记账后的风险值、所需减仓金额，再按规则汇总观察
次数、检查前超限次数、执行后残余次数、最大金额、期末剩余金额与采样超限时长。
采样时长是当前点仍有残余、且下一点有定义时的时间戳差之和，采用墙钟微秒，包含跨日
间隔；最后一点之后不外推。它是左端采样估计，不是区间内持续超限的证明，也不是精确
成交响应时延。具体订单完成等待时间仍由 execution_eval 负责。

执行原因汇总来自该账户全部 OrderEvent，包含等待、拒绝、撤销、过期等原始 reason；
次数是事件数，不是独立失败订单数。可用快照中订单 ID 和 execution.parquet 追查。
这些事实解释执行环境，但不自动声称每次等待都由风控导致；目标在执行边界被资金或
可卖量裁剪而没有下单时，也不能凭空编造拒单事件。费用、换手、收益和回撤继续由
portfolio/execution_eval 给出，是该路径的运行代价和背景，不声称是风控的增量成本。

**公开行业资料与适用限制。** 以下公开论述支持“风险目标—控制过程—场景检验—经济评价”
的区分，并不证明所有量化机构使用相同内部模式或软件结构：

- [AQR，Alpha Beyond Expected Returns，第 7 页](https://images.aqr.com/-/media/AQR/Documents/Insights/White-Papers/Alpha-Beyond-Expected-Returns.pdf#page=7)
  将波动率目标带来的风险稳定与尾部改善作为重要价值，收益风险比改善是另一层收益。
- [Citadel，What We Do](https://www.citadel.com/what-we-do/)
  描述独立风险职能、风险容忍度、持续监测与压力情景，支持把风险预算及实际暴露作为
  独立评价对象，不能仅以策略收益判断。
- [Two Sigma，A Machine Learning Approach to Regime Modeling](https://www.twosigma.com/articles/a-machine-learning-approach-to-regime-modeling/)
  讨论市场状态划分在情景分析与压力测试中的作用，说明单一区间的表现不足以覆盖风险情形。
- [Man AHL，Active Risk Management in Practice](https://www.man.com/insights/active-risk-management-in-practice)
  比较不同风险管理方法的风险与收益表现，说明对照研究有独立价值；它并不使所有控制验证
  都必须先运行无风控账户。

本项目据此选择上述职责划分；该架构决定来自业务问题与最小证据要求，不是从行业资料
推导出某种统一行业 API。RiskCases 的读写、唯一性检查及 evaluate 继续用于固定快照
回归；这些观察的 outcome 为 None，表示没有交易回放结果。完整回放的每个 RiskObservation
则保存 outcome（本时点执行后 RiskContext），并由独立有效性评价消费。

### 信号来源与历史使用边界

`ModelSignalInput` 直接加载具体模型；不扫描目录、挑选最后模型或隐式训练。
读取模型同目录 `schedule.json`，核对其 model_ref、train_dates、Feature/Label 身份、
特征列顺序及标签列。使用首次实际选中决策时刻验证 `label_maturity_ts_us <= decision_ts_us`。
归档 request 不重新解析为当前请求，缺失、损坏、关联错误及成熟时间不合规原样失败。
这只验证给定模型的条件回测；理论成熟下界和今天加载成功都不是模型当时已存在的证据。
不新增 ModelUse、binding.json 或历史换模平台。

特征由共享 `read_features` 读取并按当日历史合格集合过滤。含 `decision_ts_utc` 的表按
明确时点分组，其他日频特征在当日 15:00 决策；不得将 14:30 特征静默改成 15:00。
准备只提供特征，不提供后验标签；交易 Step 在当前决策才调用一次 `InferenceModel.predict`。
固定预测保留全部实际输入行和缺失预测，按 `predictions/<trade_date>/<ts_us>.parquet` 写出，
同日多个决策不会覆盖。同一时刻只允许一个批次，每批 symbol 唯一。

`RecordedSignalInput` 直接消费预测文件及其已有模型身份，不重新推理。signal_eval、
strategy_eval、risk_rule_eval 和 full 的 `prepare_signals` 在返回前对每个模型的首次实际选中决策应用
同一训练记录及成熟下界，
并检查全部决策满足 `initial_account.ts_us <= decision_ts_us < execution.end_ts_us`。
准备期间每份预测记录只读取一次，同一模型路径及内容摘要复用已加载模型；每份记录声明的
模型身份仍须匹配，不能仅按路径复用而忽略不同摘要。PrepareInputsStep 消费该准备函数
返回的批次，不追加模型使用或模拟区间校验；这些约束由同一准备边界建立，避免同一可信
调用链重复读取和核验。
预测缺失是合法无信号，非法数值或身份错误不是无信号。Threshold/TopK 对缺分数的已有
持仓和有效委托保留承诺并占用容量；不因缺少评分自动平仓。

`start/end` 为 signal/strategy/risk/full 选择决策日期；execution 的范围选择原始命令日期。模拟终点、订单有效窗口、退出日程和后验标签覆盖是独立边界，
不能统一截在决策结束日期，亦不能无限延长至所有订单完成。含信号的账户路径只有交易事实持久化后
才读取评价标签；改变未来价格或标签不得改变此前信号、原始目标和委托。

### 时间、市场与账户

日频、分钟级和混合频率回测共用上述三组 Step。信号的决策日程与执行行情的区间粒度
分别由输入声明，不要求相同，也不需要按频率另建 Pipeline 或交易 Step：

| 场景 | 信号与执行行情 |
| --- | --- |
| 日频信号＋日线成交 | 按日生成目标，使用 `BarTime(unit="trading_day", size=1)` 的完整单日观察推进成交 |
| 分钟信号＋分钟成交 | 已保存预测或带 `decision_ts_utc` 的特征明确逐次决策时间，使用 `unit="minute"` 的完整区间观察；同日允许多次决策 |
| 日频信号＋分钟成交 | 按日生成目标，随后用分钟观察推进挂单与账户；行情更新不自动重复生成日频目标 |

表中成交区间用于 `limit_price`；`quoted` 仍按订单生效时的原报价成交，不随输入频率
改成等待后续区间。信号可用时刻遵循上节，具体撮合条件遵循下文账户级执行模拟。

每个交易任务通过 `execution.market_ref` 提供规范化 JSON 市场引用，明确
`price_basis="raw"`、`quantity_unit="shares"`：

- `sessions` 包含正式交易日、账户日起点、收盘时刻和有序不重叠的 `TradingWindow`。
  声明的日期序列必须与 Access 在该范围读到的正式日历完全一致，不从实际有行情的日期
  推导日历，也不把周一至周五当开市日。
- `marks` 是按可见时间排序的估值价格，每条包含 ts_us、trade_date、prices 和
  kind（mark/daily_close）。同证券同刻只允许一个明确标记；正式 daily_close 必须在该日
  声明的收盘时刻可用。早先标记不能覆盖后来执行观察的价格；同刻显式 mark 拥有估值口径。
- `observations` 是按可用时间排序的 ExecutionObservation，包含稳定 observation_id、
  symbol、完整 BarTime、正数 Decimal price 和可选 volume_qty。BarTime 明确 minute 或
  trading_day、size、交易日、区间起止和可用时刻；minute 长度匹配 size，日线只支持完整
  单日源端区间。执行要求 available=end，拒绝迟到、冲突身份和复用流动性的重叠区间。
  同刻批次先合并，再推进一次。`limit_price` 必须提供成交量；`quoted` 不消费观察量，
  允许其为 null。缺量不能解释为无限流动性，也不能切换成交假设。

来源准备必须已经根据数据 owner 完成数量单位与价格口径转换。这里不猜“股/手”、
不把缺失 VWAP 当零价，也不从一条日线推断盘中触价先后。声明 raw/shares 不是独立的
真实数据质量证明。PrepareInputsStep 当前将完整市场 JSON 一次读入内存，提供本地
有界输入回放；从源端日线、分钟事实自动构建该输入的链路尚未接入。已有 Feature 读取
不能替代执行行情准备。真实数据接入需要在输入准备侧完成上述转换，账户、策略、风控、
成交和结果 Steps 继续共用。全市场、长周期分钟数据的内存与吞吐尚无规模验证；
公司行动现金/股数事件、冲击、盘口排队和真实 broker 也不由此接口补造。

初始账户显式给出快照时刻、账户交易日、现金、持仓、可卖数量、平均成本及既有峰值权益。
持仓、可卖和成本的证券键一致，可卖不超过持仓；空账户明确传空字典。本轮从无挂单
账户起步，挂单由随后命令产生。每次运行独占 PortfolioState、ledger、pending 容器及策略状态。
同日推进不重置可卖数量；跨正式交易日才解除前日买入的 T+1 限制。

ReplayClock 合并账户日程、信号、命令、市场可用时间和显式风险检查；提交和策略日程
还可加入未来唤醒。先推进旧订单并结算，再估值，再产生当前新决策。没有新信号或行情
仍处理日切、风险、退出和到期。动态时钟去重、严格递增、受显式终点裁切。
交易分钟沿 TradingWindow 累加，跳过午休和休市并保留微秒；覆盖不足失败，不能缩短
窗口。交易日持有的入场日为 0，由正式日期序列及显式退出时刻求下一日。

频率支持的回归边界由同版本测试及其输入固定：

- [工作流测试](../tests/workflows/test_backtest.py) 的
  `test_full_with_daily_execution_waits_for_the_subsequent_complete_day` 验证日频信号配日线成交，
  `test_two_minute_decisions_are_distinct_and_extra_wakeups_do_not_change_returns` 验证同日分钟决策
  保持独立且额外空唤醒不改变收益，`test_daily_model_inference_preserves_rows_and_all_skipped_holdings`
  验证日频模型信号配分钟成交，并保留缺失预测行及已有持仓。
- [执行模拟测试](../tests/trading/backtest/execution/test_simulator.py) 的
  `test_daily_observation_uses_same_lifecycle_and_only_a_subsequent_complete_day`、
  `test_partial_fills_last_minute_then_expiry_and_no_automatic_reentry` 和
  `test_incomplete_interval_cannot_fill_new_order_and_no_observation_still_expires`
  分别固定后续完整日线、分钟部分成交后到期、未覆盖完整区间不得成交的边界。

这些测试使用 `scenario`/`daily_scenario` 构造的市场 JSON、本地模型与预测制品，以及
执行器测试中的固定订单和观察；交易日历、股票资格等 Access 输入由测试提供。
证据覆盖声明成交假设下的流程与时间边界，不构成真实源数据质量、真实成交效果或
大规模分钟回测容量的验证。

### 策略目标与退出计划

Threshold 和 TopKHysteresis 保留各自具名选择/固定股数规则。完整目标覆盖已有持仓和
有效委托证券；0 明确请求平仓，缺键不能由下游猜测。策略在风控前记录唯一原始 TargetEvent。
没有新信号时这两个策略不恢复历史目标，也不隐式续开入场窗口。

`fixed_holding` 是独立的固定名单、等现金名额和下一正式交易日退出策略。任务明确提供
max_positions、decision_time、exit_time、初始持仓的 initial_exit_dates 和 order_window_minutes。
时间使用市场时区；信号必须恰在声明决策时刻。初始正持仓必须逐证券给退出日期，
不能从平均成本或当日快照推断其入场日。窗口时长同时用于本策略每次入场及退出尝试。

当时有评分且合格的候选按预测降序、symbol 升序排序。延续持仓和有效买单占名额；
可用现金扣除有效买单预占后按剩余名额等分，以当前可见价格和证券买入步长向下取整。
候选不足时不增补无预测证券，空余名额对应现金保留。未完成卖单仍占持仓和资金，
不得提前花计划卖出所得。随后报价及含费资金接纳可能进一步减少合法下单数量，因此
等权意图不保证最终等权持仓或全部成交。

选中证券的退出计划从信号/入场计划日算下一正式交易日，使用任务明确的 exit_time，
不从首次成交重新起算。入场余量到期撤余，当轮不补选、不由风险定时检查重新买入。
没有新信号也按策略日程提交退出；未完成持仓在下一正式交易日同一时刻继续尝试，
直到模拟终点。期末不虚构强制成交，保留持仓和有效订单事实。日终持仓必须以当时可见、
显式声明的正式不复权日收盘价估值；不能把后来收盘价倒写到更早盘中。

### 风险规则与动作

risk/full 的 rules 必须非空且参数显式给出，`RiskManager.from_config()` 按声明顺序构造
规则链，`RiskManager.apply()` 是固定案例评价和交易回放共用的唯一风险计算入口。
RiskContext 是当时现金、权益、峰值、价格、持仓、有效委托和证券数量步长的不可变快照；
RiskCase 保存风控前完整目标，target=None 唯一表示检查当前承诺。缺失受控证券的价格
或步长必须失败。

固定案例集合由 `RiskCases` 持有，构造时取得独立 tuple 并检查集合内 case_id 唯一；
空集合合法，案例顺序不被排序或去重。`from_files()` 按文件及数组顺序严格解析外部记录，
再调用构造函数；单个案例及上下文沿用各自构造约束，下游不重复检查已建立的契约。
`evaluate(risk=...)` 对每个案例调用一次所选规则链，按原顺序返回 RiskObservation，
不修改案例或账户。同一案例集合可评价不同规则链，不保存所选规则或上次评价结果；
案例持久化与规则计算因此能够分别使用，full 的运行时决策无需依赖案例文件。

- max_single_name：权益×max_ratio 除以价格形成单证券数量上限，按声明步长向下取整，
  与请求数量取较小值。
- max_gross_exposure：上限为权益×max_gross_ratio。先保留当前已成交持仓占用，再对
  新买增量按剩余预算同比例缩减并量化；未完成卖出不释放预算。当前持仓已经超限时，
  按上限/持仓市值比例减仓，目标只能减少，不能反向扩大已有卖出承诺。
- max_drawdown：`峰值－权益 >= max_drawdown×峰值` 时输出显式零目标，边界包含等号。
  比较按输入十进制值计算，避免 10% 恰触边被浮点减法误判为未触发；不引入持续冻结状态。
- 非正权益先禁止新增买入、撤销有效买单，保留合法减仓方向。此时比例敞口规则不作比率
  计算，观察记录 checked=false 和 nonpositive_equity；峰值为正时回撤规则仍执行。
  峰值非正时回撤无定义并记录原因，不能把未检查写成通过。

交易流程中的风险数量步长使用该证券显式 buy_step_qty；最终买卖委托分别按其自身
buy/sell_step_qty 量化。允许、缩减、拒绝及清仓分别记录；RiskDecision.adjusted=None 表示
无待提交目标，零数量映射才表示清仓。风险动作不修改账户，减仓仍需满足可卖数量、
报价和观察量后实际结算。回撤阈值不是损失保证，价格跳变或卖不出必须保留实际结果。

定时检查基于“持仓＋未完成买入－未完成卖出”的现有承诺，不读取 target tape 最后一项。
例如原买 400、已成 360、40 到期后，没有新策略请求就不能恢复 400。敞口预算的计算
不提前扣卖单；动作对账则保留已有卖出方向。缩减买单时撤销冲突旧单，替代单保留原报价
和原到期时刻，不重开窗口。每条规则保存 before/after、triggered、checked 和原因；
报告只汇总当时观察，不再运行第二遍规则。未触发不等于未检查，未检查不等于通过。

### 账户级执行模拟与唯一结算

五种任务的账户路径使用同一提交、挂单、撤单、成交、费用、账户和账本路径。目标路径可以
根据已有持仓/挂单差量与现金/可卖预算生成合法数量；冻结具体订单保持其原数量，
不满足接纳条件时明确拒绝，不能暗中缩量冒充原订单成交。策略输入只生成原始目标；
买卖方向、报价和数量合法化在最终目标进入订单边界后完成。

实现名称按职责区分：`AccountExecutionSimulator` 管理单账户订单生命周期并驱动成交假设，
`*FillModel` 定义成交价格与撮合条件，`SettlementEngine` 协调费用、持仓和账本更新；
数量与报价约束属于 `trading.orders`。这样调用者能从名称看出对象负责账户级模拟、成交假设
还是订单约束，而不会把它们都理解成同一个“execution”。

OrderIntent 显式保存 ts_us、symbol、side、qty、order_id、active_ts_us、expires_ts_us、
limit_price；limit_price 必须是正数有限价格，不接受 null。quoted 和 limit_price 路径的报价
来自提交时可见价格及显式买卖 offset_bp：买价上界向下取
price_tick，卖价下界向上取 price_tick；execution 消费订单原报价。价格模型将观察
价格先加/减显式滑点，再检查买价不高于上限、卖价不低于下限，不能把不匹配价格夹回
限价。两种信号驱动路径均提供 strategy.quote；冻结委托自带原报价，不需要报价生成规则。
limit_price 配置在 fill 中显式提供 slippage_bp，零滑点也检查报价和市场量。
字段组合的构造约束由 [CLI Backtest](engineering/cli_contract.md#backtest) 拥有。

LimitPriceFillModel 只有在
`active <= observation.start < observation.end <= expires` 的完整后续区间可成交。
因此 T 日收盘生成的目标不能用当天已经结束的日线成交；使用下一正式交易日的完整
日线时，只能在该区间结束并可用后撮合，订单期限必须覆盖到该区间结束。分钟路径同理：
14:30 生效的订单可以在 14:30–14:31 的完整区间于 14:31 可用时参与撮合，不能使用
14:29–14:30 的已结束区间。满足时间条件仍需满足价格、数量和账户约束，不保证成交。
该假设下提交时不成交；最后可执行区间先撮合后到期撤余。价格模型的数量预算同时受余量、
该观察量、fill_step_qty、实际现金及可卖量约束。竞争同一观察量或资金时按生效时间、原提交顺序分配，
不按输入证券顺序改变全局优先级。同一完整区间不能证明卖先于买，买入不得预用本批
卖出所得；只有以后时点已结算现金才能用于后续买入。

quoted 配置使用 ImmediateQuoteFillModel：合法接纳的订单在 active_ts_us 按原 limit_price 一次全额
成交，不等待后续行情，不应用滑点、观察量或触价检验。报价仍只使用提交时可见价格；
市场 marks/observations 继续按可见时点更新账户估值，未来收盘价不能参与当时的报价。
数量须同时满足申报和成交步长；目标路径按二者公倍数合法化，冻结订单不合规则拒绝。
买单预占报价金额及费用，卖单还预占费用超过卖出金额的差额，不能因“全额成交”透支现金
或越过可卖量与 T+1。资源在实际结算后释放；同刻按生效时间和提交顺序结算，后提交订单
可使用此前已结算的卖出现金。延迟生效订单必须由时钟在其生效时刻推进，跳过生效时刻失败，
不补记到后来账户日。撤单仍可在生效前释放预占。

该假设产生的 fill_id 为 `<order_id>:quote:<active_ts_us>`，observation_id 为 null；
reference_price 与成交价均为申报价，滑点成本为零。它不伪造市场观察，也不表示真实市场能够
按该价格成交。期末持仓用当时可见价格估值，不为得到利润而强制清仓。输出解释为声明策略、
账户和报价条件下的假设收益；同一预测搭配不同仓位、退出或费用规则可以得到不同收益。

买单按原报价及费用预占现金，卖单按可卖量预占数量；撤销、过期、成交释放
对应占用。T+1 由显式证券规则决定。金额和费用使用 Decimal，佣金最低收费按每单累计
成交金额计算增量，不对每次部分成交重复收最低费；卖出印花税和沪市过户费使用任务
声明费率。本轮不进行未声明的分币舍入，不把示例费率当真实市场成本。SettlementEngine
是费用、持仓和 ledger 的唯一协调入口，以稳定 fill_id 去重；完全相同的回报只应用一次，
冲突身份或超卖在改变账户前失败。

#### 移除 reference 成交假设的原因

正式账户回放只支持上文的 `quoted` 和 `limit_price`。原 `reference` 的含义是：订单在
后续完整观察区间内，以该观察的 price 成交，不提供委托限价，不检查市场成交量，但仍受
现金、可卖量、数量步长、T+1 和费用约束。它既不等同于当前报价立即成交，也不等同于
`limit_price` 配置零滑点；后者仍检查限价和可分配成交量。配置类型和组装只定义现有两种
假设，不保留旧实现，也不增加针对 reference 的专门判断或迁移逻辑。

移除依据是当前评价目标没有必须依赖它才能回答的问题，而它会引入以下解释歧义：

1. **观察到价格不能证明本账户能按该价格成交。** 例如账户资金充足、委托 1,000 股，
   后续观察价为 10 元，观察成交量仅 100 股甚至为零，原 reference 仍可能成交 1,000 股。
   这只能是人为设定的反事实。A 股竞价交易需要买卖申报匹配，并按价格、时间优先分配；
   市价申报也依赖对手盘，并非无条件按行情记录全额成交。依据见
   [深交所《交易规则（2026 年修订）》第 3.3.4、3.4.2—3.4.4 条](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)。
   `limit_price` 也只是现有价量输入下的近似，不能据此声称已经模拟盘口排队或市场冲击。
2. **参考价口径没有被这个选择项定义。** 当前 ExecutionObservation 只保存规范化的
   price、区间和可用时刻；raw/shares 说明价格复权口径和数量单位，不能说明 price 是
   收盘价、区间 VWAP、买卖中间价还是其他基准。把这些价格都解释为账户成交，会混淆
   “以什么衡量表现”和“按什么机制完成交易”。即使没有提前读未来数据，也不代表成交可实现。
3. **现有五种任务已经有明确、可验证的选择。** signal 使用申报价全成假设，execution/full
   使用价量约束；strategy/risk 可以在两者之间声明场景。删除 reference 后，预测评价、
   策略收益、风控效果、冻结委托执行和完整反馈回放仍然可以表达并验证，没有丢失这些任务
   必需的观察结果。保留第三项仅因“也可以运行”，不足以抵偿配置、无报价订单、资金预占
   回退和无成交量分支带来的理解成本。
4. **基准比较不能自动解释为执行成本或收益上界。** 按后续观察价成交可能比申报价好，
   也可能更差；成交量及成交时点还会改变持仓、现金和后续风控/策略决策。因此两条完整账户
   路径的收益差不能全部归因于滑点，reference 的收益也不保证高于有约束回放。

`quoted` 与旧 reference 都包含理想化成分，但有不同的时点和价格定义。保留 quoted 的
理由是 signal 的已定义目标就是检验申报价全成假设下的账户表现，并在报告中明确其限制；
这不能成为继续保留另一种未被当前问题要求的理想化成交的理由。缺少盘口、成交量或
其他真实数据也不构成保留依据：这类缺口不能通过假定成功成交来填补。

#### 哪些问题必须使用参考价

移除的是 `execution.fill.type=reference`，并不是取消研究和评价中的参考价格。
是否“必须使用”应由问题定义判断：没有这个基准就无法计算或解释结果时，参考价才是必需输入。

| 明确要回答的问题 | 必需的 reference | 是否需要参考价虚拟成交 |
| --- | --- | --- |
| 这组真实或模拟成交，相对下单到达价、指定窗口 VWAP/TWAP 或当时买卖中间价付出了多少价格成本？ | 与成交方向、数量、窗口及价格口径绑定的评价基准 | 不需要；比较已发生的成交，不改变账户 |
| 信号从指定入场基准到退出基准的未来收益或排序能力如何？ | 标签契约定义的起止参考价与成熟时间 | 不需要；使用信号/标签评价，不假造交易事实 |
| 如果预先冻结的同一组指令能在指定时点、按指定基准价执行，账户路径会怎样？ | 精确定义的反事实成交价、数量与时点 | 需要独立的参考价成交情景，才能计算其现金、持仓、费用与反馈；当前五种正式模式不提供该情景 |

前两类需要参考价格数据，并不需要恢复旧成交类型。现有 `FillEvent.reference_price` 和
账本同名字段继续保留：limit_price 记录应用滑点前的观察价，quoted 记录申报价。
例如买入观察价 10 元、滑点 10 bp，在限价允许时模拟成交价为 10.01 元，reference_price
仍为 10 元，价格差可供成交评价使用。该字段不能自动解释为到达价或某个 VWAP，其他基准
必须由相应评价问题单独定义。

只有第三类问题确实成为明确研究目标时，才需要研究参考价虚拟成交；“以后也许要用”
不足以在正式执行器中保留实现。届时必须先固定以下边界，不能直接恢复旧的通用 reference：

- **基准身份与时间**：证券、数据来源、原始/复权口径、价格公式、观察窗口、可用时刻和
  假设成交时刻。例如完整分钟 VWAP 在分钟结束后才可用；不能把结束后计算的值用于此前
  报价或风控。用完整日线中的某个价格，也不能自动声称在当日开盘时成交。
- **成交与账户假设**：参考的是原始目标、冻结委托还是已成交数量；是否假设全部完成，
  是否忽略价量约束；仍保留哪些资金、可卖量、T+1、数量和费用条件。零量、停牌、缺失基准
  及订单窗口外的处理须明确，不能用最近价格静默补成一次交易。
- **对照问题与解释范围**：执行质量归因应固定指令及比较口径；若允许不同账户状态继续
  驱动策略或风险，应将差额解释为整个场景的结果，而非单独执行摩擦。并非所有比较都需要
  第二份账户，仅需要价格成本评价时不额外构造一条回放。
- **结果身份与证据**：反事实结果单独标识，不能覆盖实际或约束模型的交易事实；预先确定
  输入、验证场景和验收标准。验证、候选隔离和后续是否采用按
  [研究治理](engineering/research_workflow.md) 执行。上述研究场景不属于当前五种模式的成交配置。

### 评价、制品与日志

交易完成后先由 FactsPersistStep 保存原始 execution/equity、orders.json、targets.json，
运行业务风控的路径另由 `RiskCases.write()` 按原有 JSON 数组格式保存 risk-cases.json；写入只序列化
当时记录的案例，不执行风险规则。随后由 EvaluateResultsStep 评价，PublishResultsStep
写 metrics.json 并生成 report.html。五种模式
均保存一个账户的制品，具体布局由存储 owner 定义。每个文件原子写入，但整体不是多文件事务；
失败保留已写制品且阻止后续步骤。
这些阶段全部由显式 Step 拥有，并处于同一次 Instrumentation 作用域内；完整链包括
PublishResultsStep 中的报告成功后才能正常结束作用域。PrepareInputsStep 的身份或输入
失败会输出失败时间线，但不创建成功制品。

execution.parquet 保留 `ledger.records` 和 `ledger.order_events` 两个现有事实序列：

| 字段 | Parquet 类型与来源 |
| --- | --- |
| `source` | 必需 string，取 `records` 或 `order_events`，区分原始序列 |
| `sequence` | 必需 int64，在各自原始序列中从 0 开始的位置 |
| `ts_us`、`order_id` | 必需 int64，原始事件的 UTC epoch 微秒时间与订单 ID |
| `event` | nullable string，仅 records 行使用原值 `ORDER_SUBMIT`、`ORDER_REJECT` 或 `FILL` |
| `symbol`、`side`、`qty` | 前两列 nullable string，qty 为 nullable int64；来自 records 原值 |
| `reason`、`observation_id` | nullable string，来自对应记录；未记录时为 null |
| `fill_id` | nullable string，来自 FILL 的稳定成交身份 |
| `price`、`reference_price`、`commission`、`stamp_tax`、`transfer_fee` | nullable string，原样保存账本中的十进制价格和费用，不经 float 转换 |
| `meta` | nullable string，仅 records 行保存原始 meta 的 JSON，保留嵌套结构，不展平或覆盖同名主字段 |
| `status`、`remaining_qty`、`candidate_price` | 仅 order_events 行使用，分别为 nullable string、nullable int64、nullable string；status 使用已有枚举值，候选价按 Decimal 文本保存 |

文件先按记录顺序保存 records，再按事件顺序保存 order_events；`source + sequence` 保留
各自顺序。两个来源没有统一的序号，不将文件行序或同一 ts_us 下的人为排序声称为跨来源
因果顺序。OrderEvent 没有的 symbol/side 等字段保持 null，不通过后续状态猜补；可用
order_id 关联账本记录。无成交时不补零价或零费用，无任何执行记录时写带固定 schema 的
零行表，明确区分正常无交易和明细未成功发布。

equity.parquet 按 `EquityCurve.points` 的原有顺序保存六列：`ts_us` 为 int64；
`cash`、`market_value`、`equity`、`peak_equity`、`drawdown` 为 float64。
各列均不为空。保留实际估值时点与计算精度，不重采样为每日结果，不在空日期插值。
同一时点已由 EquityCurve 替换的值只保存最终快照；该文件不是账户状态变更日志。

orders.json 按原顺序保存完整 OrderIntent/CancelOrders，可直接用于 execution_eval；
targets.json 保存风控前目标。risk-cases.json 保存账户与挂单快照，可通过 RiskCases.from_files().evaluate() 复演规则决定；
它不替代 risk_rule_eval 的完整策略输入。
预测引用、模型身份及模型/特征/标签摘要随指标/预测记录保存；
行情原文件和运行环境没有自动复制归档，完整复演还需保留这些输入及准确源码版本。

metrics.json 的稳定顶层结构是 mode、parameters、not_run、prediction_refs，
以及实际执行的 signal_eval、risk_eval、risk_effectiveness、execution_eval、portfolio、final_account。
applied_risk 记录本账户实际业务规则，无业务风控时为 null。risk_eval 保留案例、原始决定
与 outcome 快照，rule_coverage 按规则类型累计检查/触发次数，同类多条规则会分别计数；
risk_effectiveness 按配置顺序保存 rules，
每项含 rule、observations、sample_count、defined_count、observed_breach_count、
residual_breach_count、max_required_reduction_notional、max_residual_reduction_notional、
final_residual_reduction_notional、sampled_breach_duration_us、undefined_reason_counts。
每条 observation 含 ts_us 和 before/after，两者均含 held_notional、pending_buy_notional、
value、required_reduction_notional、undefined_reason。金额单位为账户货币，value 为比例；
最大值仅在有定义观察中求取，无有效观察为 null，期末值不向前取旧值。defined_count
要求同一时点前后均有定义；原因按受影响时点统计，同一原因在前后出现只计一次。
无样本的时长为 null，有样本但无可累计区间为 0。risk_effectiveness 另存该账户全部
execution_reason_counts，不产生无基线支持的收益差值或自动通过标识。
未运行字段不写入；无样本保留零条目计数；数学无定义保存 null+reason；非法数值与 I/O
错误不能被改成成功 null。具体解释：

- 信号先保留逐决策 ICObservation，再对同日有定义决策等权、最后对有定义交易日等权。
  decision_count 与 trade_day_count 分开，缺失日期不补零，不再把 T+1 raw-close 诊断
  冒充模型目标 IC。
- 风控保存原始案例和逐条决定，分别统计 checked 与 triggered；不根据回测收益判断
  规则是否正确，未覆盖规则需另用固定案例验证。
- 成交按原始订单身份和请求数量统计接纳率、完成订单率、累计成交数量率、首次及完全
  成交等待时间与最终未完成原因，不能用 fill 条数当订单完成数。
- 组合净利润 `net_profit = final_equity - initial_equity`，净收益率
  `total_return = final_equity / initial_equity - 1`，使用本次结算路径。初始权益是首次决策前
  的账户估值，独立保存在 State 中；不能用同一时点成交后已替换的首个权益点代替。
  gross_return 是同一路径加回已收费用，不是另一条无费交易路径。收益和 Sharpe 按正式日终权益采样，年化因子任务显式提供；样本不足、
  方差为零、初始权益非正等分别记录无定义原因。换手为成交金额合计除以初始及各日终
  权益的算术平均。事件权益继续用于回撤，因此增加不改变账户/价格的唤醒不会改变日报告。
  组合结果不自动包含未声明的比较基线，不能据此给出模型/策略盈利采用结论。

CLI 在分派前用 `submission.model_dump(mode="json")` 构造一次参数快照，记录全部实际值及
字段定义的默认值；signal/strategy/risk/full 的 min_listing_calendar_days 直接保存在顶层，
execution 不包含该字段，不额外嵌入 backtest_config。原始请求日期不被筛选后的日期替换。CLI 用该快照
初始化 `BacktestMetrics(mode=submission.mode, parameters=parameters, not_run={})`，并在
workflow 读取输入前，按模式、日期、信号/委托、策略、账户、风控、执行和最短上市天数
逐项记录 JSON 开始日志。Workflow 接收初始 metrics，由 InitializeAccountStep 记录实际风控
与未运行原因，再由 EvaluateResultsStep 填充结果；PublishResultsStep 在评价完成后
写入参数及结果，最终 HTML 复用已保存快照，不另生成参数制品。这样既能解释已保存结果，
也能在输入失败、尚无 metrics 文件时定位请求；开始日志不是成功证据。
not_run 由实际组件与输入决定：没有业务风控的账户路径记录 risk=no_business_risk；
冻结委托路径记录 signal=strategy=frozen_commands；quoted 记录
price_feasibility=market_liquidity=quoted_fills。limit_price 不标记价量约束
未运行，有信号的路径不标记信号评价未运行。quoted 报告明确生效即申报价全额成交、保留的
账户约束与期末估值口径；每条账户报告提供委托、成交费用与权益曲线链接。

BacktestPipeline 使用注入的单调时钟，处理后距上次进度至少 5 秒且仍有后续唤醒时输出
当前交易日、时点及累计处理数；短任务没有逐时进度。所有 Step 异常保留原对象，并附加
scope 和 Step 名；回放中的交易处理失败另附 trade_date、ts_us，账户初始化、事后评价或报告
失败不归因到最后交易时点。Instrumentation 测量三组中实际调用的 Step，逐时点动作累计
耗时和次数，输入准备包含在内；
合计仍不包含 Step 之外的依赖组装和 CLI/Job 启动等时间，不能称为端到端耗时。
报告失败时输出失败时间线，不提前输出成功时间线。
报告成功后输出一条中文摘要，按实际模式展示 IC、规则覆盖、收益、订单及数量结果，
不展开逐条观察或持仓字典。金额两位小数、收益/回撤四位百分比、IC 六位小数，null 显示
“无值”；完整明细链接 metrics 和事实制品。metrics 存在不能证明随后的 report 已成功。

## 失败现场

制品持久化或 report 失败时原异常传播，已经创建的 experiment 目录和制品保留为失败
现场；它们不表示成功，也不被本次 workflow 自动清理。重新发起回测使用新的 experiment_id。
