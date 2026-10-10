# Offline 工作流契约

- **状态**：正式 owner
- **适用范围**：offline data、CLI-only backfill、offline training 与 daily-alpha backtest
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

run_daily_alpha_backtest(
    *, backtest_config: BacktestConfig, path_manager: PathManager,
    submission: BacktestSubmission, experiment_id: str,
) -> None
```

`src/workflows` 只负责自身拥有的入口前置条件、实验身份、日程解析和依赖组装，并通过不可变 tuple 显式
声明具体 step 及其顺序。Data 只使用一个 `DataPipeline`，Training 与 Backtest 分别使用
`TrainingPipeline` 和 `BacktestPipeline`。`DataPipeline` 只拥有 workflow 传入的单一 `steps`
tuple 和 Instrumentation 作用域，不知道 data kind、日期 schedule、source、step 分类或具体
step 类型；它必须严格按传入顺序把同一个 Context 交给每个 step，一次 `run` 中每个 step
实例只调用一次。TrainingPipeline 拥有一个已解析的训练窗口及单一 Step 链，
BacktestPipeline 拥有 timing schedule。
所有 Pipeline 都不得创建、发现、排序或按类型重排 step。

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
原样传播异常。Data 与 Training 各用它执行一次完整 Step 链；Backtest 分别对每个 timing
和最终 Step 链调用。各领域 Pipeline 仍拥有自身的日程、
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
Context 每次 `run()` 新建，训练值和逐日评价观察只在这次 Step 链内传递。只有确需跨 timing 延续的 backtest 值进入
`BacktestState`。

Data Context 是可信内部载体，构造时不执行日期校验。请求范围由
[`CLI 契约`](engineering/cli_contract.md#通用边界)约定的 submission 构造边界保证；
workflow 自行生成范围时负责在执行准备前建立相同的日期格式与顺序不变量。`start/end`
在同一次 Step 链中保持不变，Context、DataPipeline 和内部 data Step 不重复校验它们。
Calendar Step 对该范围执行年度物化与正式交易日解析；Access 的独立 public 请求校验继续由
[`Access 契约`](engineering/access.md#错误归属)定义。

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
`finally` 累计耗时和次数；异常只附加 scope 与 step 名称的 note，保留原异常对象及类型，
不在此记录 traceback。Step 不接收也不调用 Instrumentation；只有
`run_steps(...)` 负责默认测量。同名 step 累计总耗时、次数和平均值；离开 Instrumentation
作用域时只输出一个多行、等宽对齐的“步骤耗时”日志事件，首行以 `scope=...` 定位运行，
依次列出每个 step 的总耗时，再列出“步骤耗时合计”。合计只累加被 measure 包围的步骤，
不包含 workflow 准备或 Job 启动等耗时，必须注明“不代表任务总耗时”，不得称为端到端耗时。
Workflow 可通过 `descriptions` 提供 step 的中文含义，Instrumentation 独立保存该映射，
展示时附在对应行后，不改用于累计的代码名称。训练 ReportStep 的含义为“诊断计算与报告
输出”，包含材料读取和诊断计算，不能只称为 HTML 渲染。同名 step 在同一作用域内实际
执行多次时，该行才额外列出平均耗时和 runs。正常退出以 `✅` 和 `INFO` 开头，异常退出
以 `❌` 和 `ERROR` 开头；续行不重复
时间、level、源码位置或状态符号。准备阶段或空 schedule/timing 在进入作用域前失败，
因此不输出 timeline。现有 step label 固定为：

```text
CalendarMaterializeStep, FactMaterializeStep, FeatureBuildStep, LabelBuildStep,
Level2FactMaterializeStep, Level2MinuteBuildStep, Stock1430MaterializeStep, Stock1430DailyMaterializeStep,
Stock1430DailyL2MaterializeStep,
DatasetBuildStep, PreprocessStep, ModelTrainStep, PredictionStep, ICEvaluateStep,
ArtifactPersistStep, SignalStep, SignalEvalStep, TradableAlphaEvalStep,
PortfolioStep, RiskEvalStep, ExecutionEvalStep, AccountingStep,
FullBacktestStep, FactsPersistStep, MetricsPersistStep, ReportStep
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

Runtime mode、model ref、strategy、start 和 end 只来自 `BacktestSubmission`。
`BacktestConfig` 只拥有静态 `init_cash` 与 `min_listing_calendar_days`；不得再声明 name、
dates、mode、model 或 strategy。

Workflow 通过 Access 读取正式交易日，并把相邻日期映射为
`BacktestTiming(signal_date, forward_date)`；signal date 同时是 feature date。无 timing 时
在 component 构造和 Instrumentation 前抛 `ValueError`。

起止日期与 mode 必须显式选择，不由训练日期推导，也不默认运行完整回测。首轮固定模型
由 model_ref 指定；入口读取该文件同目录 `schedule.json` 的 TrainingRunRecord，要求其中
model_ref 与所用模型相同。首个决策时刻由请求范围内首个正式交易日的 15:00 计算，要求
label_maturity_ts_us <= 首个决策时刻。范围起点是休市日时采用范围内首个交易日，而不是
起点午夜；不因标签尚未成熟而自动把起点推迟到后续交易日。

加载后核对模型的 train_dates、Feature/Label 身份、列顺序及标签列与记录一致，再记录
模型加载成功事件。归档 request 不作为反序列化入口。训练记录缺失、格式错误、模型关联
或时序不合规均失败；加载失败不得记录加载成功事件。训练记录和回测
时钟由服务端解析，调用者不提供 model_use，也不手填生效时间或证据引用。

label_maturity_ts_us 只是理论必要下界，不是实际训练完成时间或历史模型可用证明。
这一路径验证给定模型在训练标签成熟约束下的条件回测；今天的成功加载和日志实际时间
不能证明过去已经拥有模型，服务端也不由模型文件时间或记录路径伪造历史可用性证据。
本入口仍只有一个固定模型，不增加周期换模管理器。14:30 决策模型不得被该 15:00 日频
交易入口静默使用。

Component builder 直接接收 submission 的 mode、绝对 Path model_ref、strategy，以及静态
config。五种 mode 都构造同一套其余 capability、执行完整八层图并持久化
全部 metrics。执行映射固定为：

```text
signal_eval, tradable_alpha_eval, risk_eval -> IdealExecution
execution_eval, full_backtest              -> ExecutionOrchestrator
```

Simulated execution 的 slippage 固定为 `5.0` bp；risk 固定为 `NoOpRiskManager`，均不是
配置项或 fallback。

Component builder 通过 `load(model_ref)` 直接恢复指定模型，不扫描目录或从实验选择最后模型。推理中的
`missing.method="drop"` 对所有请求 symbol 使用同一个 fitted transform：被跳过的 symbol
当天没有 score；所有 symbol 都被跳过仍是成功的“无信号”日。未持有的跳过 symbol 不产生
新仓位；已持有的跳过 symbol 保持原数量，并占用 portfolio 的持仓容量，不得因缺少 score
隐式生成清仓目标。

`BacktestState` 只保存跨 timing 持续的 portfolio、ledger、equity、signal/target tape、五类
evaluation frame、模型目标 IC observations、bar/signal count、last mark prices 和 trade dates。它构造后立即可用，不含
path/config/experiment/timing/bar/price/score/target 等单 timing scratch 值。每层从最小
`BacktestContext` 读取前一层结果并写入自己的命名结果；具体 step 明确把需持久的
frame/event/accounting 结果追加到 state，不提供通用 `record_results`。

SignalStep 对当时 universe 的每个输入保留完整键和预测，无分数行仍在记录中，按日期保存
到本次 backtest 的 predictions 目录。记录来自实际一次预测，不为持久化再次调用模型。
SignalEvalStep 用共用 evaluate_prediction_record 对模型声明的 Label 评价，写入
metrics.model_evaluation；原 signal_eval 继续表示 T+1 raw-close 收益诊断，不能冒充
模型目标的评价。两者使用同一 rank_ic 数学实现，但标签及样本口径分别保留。
原收益诊断也保留 Rank IC 配对数和全部无定义原因，汇总报告有定义日期数及原因计数。
空分数是合法无信号，不因无法构造收益配对而失败；有分数时仍要求价格输入，
非法分数或费用不转换为正常无值。

Workflow 以 `per_timing_steps` 显式声明 signal → portfolio → risk evaluate → execution
evaluate → accounting → signal evaluate → tradable-alpha evaluate → full-backtest，并以
`final_steps` 声明 facts persist → metrics persist → report。
当次预测与目标、执行先完成，再读取未来价格和标签；后验评价不进入此前决策的依赖链。
初始 cash 只来自静态 config，universe 的 listing age 原样交给 Access。日线执行价格与
phase 语义保持不变。公共返回值为 `None`。

FactsPersistStep 在全部 timing 完成后，先保存实际执行与估值事实，再进入指标计算与报告。
它只序列化已有 ExecutionLedger 和 EquityCurve，不重新撮合、结算、补造交易事件或重算
估值。只有收益、费用和回撤摘要无法回答某笔成交如何收费、回撤在哪些时点形成；因此
execution.parquet 和 equity.parquet 是核查这些结果的明细来源，不能由日志或汇总替代。

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

两个明细都复用公共单文件 Parquet writer，依次原子发布 execution、equity；全部成功后
才执行 metrics persist 与 report。写入失败原样传播，已发布文件保留，后续步骤不执行；
它们不组成多文件事务。Timing 尚未完成时不进入最终持久化，也不在异常路径补写部分汇总。
这些制品支持执行和权益核查，不宣称已经归档原始指令、全部行情、环境或完整复现包。

运行参数随最终指标保存到现有 `metrics.json.parameters`，其他顶层指标字段保持原口径。
该对象的字段由本节唯一规定：

| 字段 | 保存内容 |
| --- | --- |
| `mode` | 本次显式选择的五种模式之一 |
| `start`、`end` | 用户请求的原始起止日期，不替换为筛选后的交易日 |
| `model_ref` | 本次指定模型文件的绝对路径 |
| `strategy` | 已解析的策略类型及完整参数，包含实际采用的默认值 |
| `backtest_config` | 本次实际采用的静态回测配置：`init_cash`、`min_listing_calendar_days` |

例如请求从周六 `2026-07-04` 开始，实际首个决策在 `2026-07-06`，仍保存
`parameters.start="2026-07-04"`；指标原有 `start_date` 继续表示实际处理的首个交易日。
策略未填写 `target_quantity` 而采用默认 `100` 时，参数快照中明确保存 `100`，便于直接
解释结果，不必用后来的默认配置反推本次运行。

Workflow 在确定新 experiment 名称后、读取日历或模型前，一次生成参数 JSON 快照并输出
一条带 `experiment=...` 的“回测任务”INFO 开始事件。续行按模式、开始日期、结束日期、
模型、策略、运行配置分列中文标签及 `key=<JSON value>`，避免把路径与所有配置挤在一行。
这些值从同一份 JSON 快照读取，最终 MetricsPersistStep 把该快照与指标一起原子写入；
不得在日志和指标出口分别重新组装参数。参数中的数值保留序列化精度和 JSON 类型，
不使用指标展示中的舍入或百分比格式。HTML 报告继续从 metrics 展示完整参数。
日志沿用公共 logger 进入 CLI stderr，HTTP 任务由 JobRuntime 捕获到该 Job 日志，不写入 Flask system log，
不新增 sink。加载、日历或执行失败而尚未写出 metrics 时，仍可用启动日志定位请求与配置；
不为失败补写空指标，也不把开始事件当成模型校验通过或任务成功。

回测默认日志服务于阅读任务状态与结果，不复制完整 `metrics.json`。组件身份、特征缺失
分布、评分分位数、逐日预测路径、逐日账本计数及 metrics 发布细节降为 `DEBUG`；无分数
等既有警告仍输出。模型核验通过后，INFO 仅说明实际首个决策日期、决策日数，不重复模型
长路径或内部 epoch 微秒时间。BacktestPipeline 由 workflow 注入单调 clock，每完成一个
timing 后，距上次进度至少 5 秒且仍有剩余日期时才输出当前日期、累计日期进度、评分行数
及持仓数；短任务不打印逐日进度，不增加心跳线程。移除逐日 INFO 后，失败 timing 必须在
原异常上附加 signal/forward 日期，仍由外层终止边界输出原 traceback。

ReportStep 在 HTML 成功发布后只输出一条中文“回测报告”摘要：实际决策区间及日数、期末
权益/现金/持仓、区间收益/最大回撤/夏普、订单提交/完全成交/拒绝笔数、成交股数与数量成交率、
费用及其佣金/税费/滑点组成、模型目标 Rank IC 及有效日期覆盖、次日原始收盘收益 Rank IC
及其有效日期覆盖、可交易样本覆盖与风控影响。两类 IC 使用不同标签和样本口径，不能合并
为一个“IC”；完全成交订单数与成交股数也不能混称“成交数”。只格式化已经保存的指标，不
在日志阶段重算收益、调整费用或重新评价模型。金额使用千分位和两位小数，收益及回撤使用
四位小数的百分比（例如 `0.0012%`，避免小收益显示为零），比例使用百分比，IC 使用六位
小数，计数显示整数；合法 null 明确显示“无值”，无交易时明确说明未提交订单。

摘要不展开逐日 observations、最终持仓字典或运行参数；完整结果仍可从 metrics/HTML
读取。制品位置只写一次实验根目录，随后按相对文件名说明预测、执行、权益、指标和报告
用途。实际决策末日可能早于请求结束日期，因为请求区间最后一个交易日用于次日评价，
摘要须说明这一差异。步骤耗时继续使用公共 Instrumentation，workflow 提供中文步骤说明。
这样既能直接看到关键结果，又保留失败定位与精细核查入口；不回写历史 Job 日志。

独立 `binding.json` 不再生成。模型合法性由前述运行时核验保证；预测文件已经保留模型
内容摘要与决策时刻，因此删除该文件不删除核验或预测证据。`event` 属于日志事件，
`training_record` 继续从模型同目录的 `schedule.json` 读取，不整份复制到回测结果；
首个决策时点等推导值也不混入用户运行参数。

选择 metrics 承载参数，是因为保存或比较一个结果时必须同时知道它的模式、日期、模型、
策略和配置；只放日志会让这种解释依赖另一份文件及日志格式。启动日志则覆盖尚无结果时
的排错需要。两者使用同一快照，各有明确的读取场景，无须第三份独立参数制品。该快照只保存
这里列出的运行参数，不宣称已归档完整应用配置、代码版本、运行环境、历史输入修订或
模型历史可用证明。metrics 写出后 ReportStep 仍可能失败，文件存在不能证明整个任务成功。

## 失败现场

制品持久化或 report 失败时原异常传播，已经创建的 experiment 目录和制品保留为失败
现场；它们不表示成功，也不被本次 workflow 自动清理。后续同名运行仍因目录存在而失败。
