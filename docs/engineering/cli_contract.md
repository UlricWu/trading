# CLI 契约

- **状态**：正式 owner
- **适用范围**：`AppConfig` 加载契约，以及 `python -m src.cli` 的命令、参数、退出码和 composition-root 副作用。
- **业务工作流 owner**：[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md)
- **HTTP job owner**：[`docs/engineering/job_api_contract.md`](job_api_contract.md)

## 通用边界

CLI 把不可信文本解析为 `src.jobs.requests` 已构造的 data、fact bootstrap、Feature
backfill、Level-2 minute backfill、H03 14:30 backfill、H04 fusion backfill、training 或 backtest submission，
加载一次 `AppConfig`，
创建 `PathManager` 并调用一次工作流。HTTP Job API
也必须复用同一构造边界，不得各自实现日期、mode、model ref 或 strategy 校验。
CLI 在命令分派前按日志技术 owner 配置一次公共 logger 的 stderr sink，不记录原始 JSON。
Backtest 命令统一准备运行身份及参数快照并记录开始日志；其余业务运行日志由 workflow
及其 Step 负责。Typer 负责参数错误并以 `2` 退出，
未处理的配置或运行错误以 `1` 退出。

日期必须是规范 `YYYY-MM-DD`。范围必须满足 `start <= end`。Training 和 backtest 的
`EXPERIMENT_ID` 必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`。两个命令的
`--experiment-id` 声明绑定同一个 Typer 参数校验回调，在进入命令函数前建立该契约；
非法值以参数错误退出 `2`，不构造 submission、不加载配置或调用 workflow。
命令函数与 workflow 使用已校验的 ID，不再手动检查；PathManager 不拥有 CLI ID 格式规则。

Submission 的日期与范围保证由 `src.jobs.requests` 中相应的 `create_*_submission` 函数
建立。TrainingSubmission 与五种 BacktestSubmission 使用验证模型；数据类 submission 的 dataclass 构造函数不执行
校验，只允许可信内部调用者提供已经满足契约的值。CLI 和 HTTP 均使用共享构造函数，
不另建业务校验。

## AppConfig

`AppConfig.load()` 是完整应用配置的唯一加载入口。调用者一次获得 `environment`、
`storage_root`、`secret`、`data`、`models` 和 `feature_groups`，无需分别识别或加载配置区段。
进程变量 `ENV` 选择项目根目录的 `.env.dev`、`.env.test` 或 `.env.prod`，缺省为
`dev`；加载过程不得把文件内容写入进程环境。`ZERO_STORAGE_ROOT` 仍由进程环境提供，
路径可用性由 `PathManager` 校验。

所选 `.env` 是 Tushare 运行配置的正式来源。`TUSHARE_TOKEN` 必须非空白，
`TUSHARE_GATEWAY` 缺失或为空时为 `None`，表示使用 Tushare SDK 默认地址；非空值覆盖
SDK 地址。FTP 配置不再读取或要求。Level-2 使用配置指定且已登录的百度网盘客户端，
客户端凭证由客户端管理。

`override` 只能包含 `data`、`models` 或 `feature_groups` 根键；因此不能改变
`environment`、`storage_root` 或 `secret`。mapping 递归合并，标量、列表和显式
`None` 直接替换，不定义字段级特殊合并规则。合并后必须校验完整 `AppConfig`。HTTP Job
API 和所有 CLI 命令都不得用 request runtime 字段构造 config override。配置读取、
override 拒绝和最终 schema 校验错误均归 `AppConfig.load()`，不在下游组件重复校验。

## Data

```text
python -m src.cli data-calendar
python -m src.cli data-standard-bootstrap --start YYYY-MM-DD --end YYYY-MM-DD
python -m src.cli data-standard --start YYYY-MM-DD --end YYYY-MM-DD
python -m src.cli data-level2 --start YYYY-MM-DD --end YYYY-MM-DD
python -m src.cli data-level2-minute-backfill \
  --start YYYY-MM-DD --end YYYY-MM-DD
python -m src.cli data-stock-1430-backfill \
  --start YYYY-MM-DD --end YYYY-MM-DD
python -m src.cli data-stock-1430-fusion-backfill \
  --start YYYY-MM-DD --end YYYY-MM-DD
python -m src.cli data-feature-backfill \
  --feature-set FEATURE_SET --version VERSION \
  --start YYYY-MM-DD --end YYYY-MM-DD
```

`data-calendar` 是仅供人工运维的无参数 CLI，不属于 HTTP Job API 或定时任务。CLI 在
composition root 通过 `DateTimeUtils.today()` 取得一次 Asia/Shanghai 当前日期，并把该
明确日期传给 calendar bootstrap workflow。Workflow 固定从 `2016-01-01` 开始，到当前
年份的 `12-31` 结束，只复用或物化范围涉及的完整 `trade_calendar` 年度 raw 与 processed
对象；不执行 fact、feature 或 label。命令成功时退出 `0`，配置、Tushare、normalize、
Meta 或其他运行错误以 `1` 退出。命令不接受日期、年份、refresh 或其他参数。

`data-standard-bootstrap` 是 CLI-only 的 Standard facts 冷启动入口。它的闭区间只表示显式
请求的 calendar 与 Standard facts 范围；workflow 不选择 Feature/Label operation，不构建
derived 对象，也不根据 Feature lookback 隐式扩大范围。它构造
`StandardFactBootstrapSubmission` 并只调用一次 `run_standard_fact_bootstrap`。范围内没有
正式交易 session 时成功；任一正式交易日的 Standard fact 缺失或其他运行错误时退出 `1`。
该命令不属于 HTTP Job API、cron 或日常 `data-standard`。

`data-standard` 与 `data-level2` 构造不同 kind 的 `DataSubmission`，但都只调用一次固定的
`run_offline_data` workflow，不接受 group、run ID 或实验身份。
完整闭区间是一次 workflow 执行单位；单日必须传相同的 `--start` 与 `--end`，不提供
位置参数 `DATE` 或其他单日形式。数据对象由 source、version 和 date 标识，不属于一次
experiment。这两个命令成功时都退出 `0`；任一正式交易日 fact 缺失或其他运行错误都退出
`1`，不得改写成 skipped。

`data-level2` 的闭区间按到达日解释，按需获取有输出 source 的网盘 raw 并补齐逐笔事实，还构建
两市股票分钟事实、当日 H03 Feature 和当天成熟的上一 session H03 Label。日期安排、必要
历史输入、步骤顺序与部分提交语义由工作流 owner 定义；HTTP 与 cron 复用同一入口。
已有各人工 backfill 命令仍按自身目标分区解释日期，不改成到达日。

Level-2 raw 维护命令为 `uv run python -m scripts.migrate_level2_archive`，只影响
Level-2 本地来源记录和压缩缓存。仍先按 AppConfig 要求提供 `ENV`、`ZERO_STORAGE_ROOT`
及 Tushare 配置；`--storage-root` 显式选择维护存储根，`--start/--end` 可限制分区日期。
默认只预检并报告，`--report <path>` 额外输出 JSON 清单；`--apply` 转换已匹配 Meta，
`--apply --cleanup` 才按存储 owner 回收已匹配文件。日期限制同时约束转换和清理。
未匹配分区保留并报告，不阻止其他已匹配分区；传输、无效 Meta 或写入错误非零退出。
此命令不进入 Job API、cron 或日常 pipeline，不上传或修改网盘文件。

`data-level2-minute-backfill` 是 CLI-only 的两市股票一分钟事实历史回填入口。`start/end`
闭区间只表示目标分钟分区；它构造 `Level2MinuteBackfillSubmission` 并只调用一次
`run_level2_minute_backfill`。Workflow 只消费已经提交的 calendar、`sh_trade/v1` 与
`sz_trade/v1`，不调用 broker，不下载或写入 raw，不执行日常 `data-level2`，也不构建
Feature、Label 或 experiment。范围内没有正式交易 session 时成功；显式目标缺少任一必要
逐笔对象、分钟构建或发布失败时退出 `1`，不得跳过日期或交易所。该命令不接受 exchange、
version、batch size、group 或 experiment identity，不属于 HTTP Job API、cron 或 MQTT。
分钟对象的 schema、聚合和 lineage 由
[`docs/data/level2_minute_contract.md`](../data/level2_minute_contract.md) 所有。

`data-stock-1430-backfill` 是 CLI-only 的固定 H03 Feature/Label 回填入口。`start/end` 闭区间
只表示目标 `T` 分区；它构造 `Stock1430BackfillSubmission` 并只调用一次
`run_stock_1430_backfill`。Workflow 只消费已提交的 calendar、两市
`stock_trade_1m/v1`、`adj_factor/v1` 和在同次执行中先复用或发布的 H03 Feature，不调用
broker，不写 raw、processed、experiment 或 Job 状态。范围内没有正式交易 session 时成功；
显式目标缺少下一正式 session、任一必要整日对象或构建发布失败时退出 `1`。命令不接受
dataset、version、exchange、grid、feature set、label set、batch size 或 experiment identity，
不属于 HTTP Job API、cron 或 MQTT。固定 identity、时间、计算、Meta reuse 和错误边界由
[`stock_1430_feature_label_contract.md`](../data/stock_1430_feature_label_contract.md) 所有。

`data-feature-backfill` 是 CLI-only 的单 Feature 历史回填入口。`FEATURE_SET` 与 `VERSION`
共同选择一个精确 registry identity，`start/end` 闭区间只表示目标 Feature 分区。它构造
`FeatureBackfillSubmission` 并只调用一次 `run_feature_backfill`。Workflow 只消费已经提交的
calendar 与 Standard facts，不调用 broker，不写 raw、processed、label 或 experiment；
每个 Meta miss 的历史输入 session 数只从精确 builder 的 `lookback_sessions` 取得。范围内
没有正式交易 session 时成功；显式目标缺少完整历史或必要 facts 时退出 `1`，不得跳过或
缩小范围。该命令不属于 HTTP Job API 或 cron。

`data-stock-1430-fusion-backfill` 是 CLI-only 的固定 H04 Feature 回填入口。它构造
`Stock1430FusionBackfillSubmission(start, end)`，只调用一次
`run_stock_1430_fusion_backfill`；闭区间仅表示输出 T。Workflow 只消费已提交的 calendar、
`l2_stock_1430/v1(T)` 和 `tushare_daily_basic/v1(P)`，P 由正式日历解析。
它不构建上游或 Label，不调用 broker，不写 raw、processed、labels、experiment 或 Job 状态。
空目标 session 集成功；必要输入或构建发布失败退出 1，参数错误退出 2。
命令仅接受 `--start/--end`，不提供 version、feature set、refresh、grid 或 experiment 参数，
不属于 HTTP Job、日常 data workflow 或 cron。融合领域契约由
[`stock_1430_daily_l2_contract.md`](../data/stock_1430_daily_l2_contract.md) 拥有。

`data-previous-day-backfill --start YYYY-MM-DD --end YYYY-MM-DD` 是 CLI-only 的盘前数据
构建入口，闭区间由 TrainingDateRange 校验，调用 run_previous_day_backfill。读取本地已提交
输入、生成资格/Feature/Label，具体身份及失败边界由
[前日数据 owner](../data/previous_day_feature_label_contract.md) 拥有；不调用 broker。
首次选择 previous_day 特征组前，须在训练服务实际使用的 ZERO_STORAGE_ROOT 中完成此
初始化。另一个存储根中的验证产物不会自动进入当前服务；仅增加模型与组名配置不会
生成对应数据。以本地开发服务的数据目录为例：

```bash
ZERO_STORAGE_ROOT=/home/wsw/app/data uv run --locked python -m src.cli \
  data-previous-day-backfill --start 2026-02-03 --end 2026-09-17
```

完成后再提交同一训练请求。该命令建立数据分区，不创建训练 Job；后续 POST 仍只创建
一个训练任务。训练本身不联网补取数据，也不会自动切换到研究证据目录。

## Training

```text
python -m src.cli train \
  --train-start YYYY-MM-DD --train-end YYYY-MM-DD \
  --model MODEL --feature-group FEATURE_GROUP --label-group LABEL_GROUP \
  [--eval-start YYYY-MM-DD --eval-end YYYY-MM-DD] \
  [--validation-sessions INTEGER] [--eval-gap-sessions INTEGER] \
  --experiment-id EXPERIMENT_ID
```

train 起止日期、模型名、特征组名与标签组名必填。eval 两端必须同时提供或同时省略；
起止相同表示单日评价，省略表示纯训练，不另加模式参数。日期为规范闭区间，
`start <= end`；不接受 windows JSON、逐日日期、cutoff、可用性声明或决策微秒数。
可选名称、准确含义、组合限制和日历/成熟时序由
[training workflow](../offline_workflow_contract.md#training-workflow)拥有。
例如完整的多日固定模型评价调用为：

```bash
python -m src.cli train \
  --train-start 2026-07-01 --train-end 2026-07-31 \
  --eval-start 2026-08-10 --eval-end 2026-08-31 \
  --model sgd_regression \
  --feature-group daily_basic_selected_v1 \
  --label-group daily_close_return_rank_d1/v1 \
  --experiment-id example-run
```

模型超参数、随机种子和预处理由所选 AppConfig.models 条目拥有；特征组定义由
AppConfig.feature_groups 拥有，不从请求构造配置 override。CLI 将共享构造边界校验后的
TrainingSubmission、两个配置映射、experiment ID 和 PathManager 直接交给 workflow。
参数格式、未知选项或不兼容的数据组合退出 2；配置、日历、数据、训练、保存或评价失败
退出 1；成功退出 0。示例不保证目标环境已有相应数据。无交易日或数据缺失不缩短区间。

任务仅拟合一次，模型 ref 位于 training/model.joblib，并记录在 schedule.json；
多日预测分别写入 training/predictions/YYYY-MM-DD.parquet。评价与报告不参与模型构造或
发布判定。未请求内部验证和最终评估时不读取评价输入，也不创建预测、评价或报告文件。

`--validation-sessions` 与 `--eval-gap-sessions` 映射 Job API 的同名业务字段，复用同一
TrainingSubmission 校验。内部验证取训练区间末尾交易日；最终评估间隔按中间跳过的交易日
数计算，默认均为0。内部验证与最终评估共享已冻结模型，指标分别保存。

通过 HTTP 创建 training Job 时，Job API 使用同一个 Job UUID 作为必填 EXPERIMENT_ID；
HTTP 不接受客户端提供此字段。

## Backtest

```text
python -m src.cli backtest --mode MODE --experiment-id EXPERIMENT_ID --input-json JSON
```

三个参数都必填。JSON 必须是 object，只包含所选任务的字段，不包含 kind、mode 或
experiment_id；mode 只由 `--mode` 传入。CLI 在加载 AppConfig、创建 PathManager 或进入
workflow 前调用 `create_backtest_submission` 校验。额外字段、缺少必需参数、非法数值
或相对引用路径均拒绝，不保留旧 `--model-ref`/`--strategy-json` 或隐式字段兼容。
mode 只接受下表五个值；旧任务名称 `tradable_alpha_eval`、`risk_eval` 不作为别名接受。
调用方为每次回测提供新的唯一 experiment_id，HTTP 沿用 Job UUID；CLI 保持上述 ID 格式
校验，不检查实验目录重名。运行当天日期及目录命名由
[Backtest workflow](../offline_workflow_contract.md#共同边界)定义。

| MODE / Submission | JSON 字段 |
| --- | --- |
| signal_eval / SignalEvalSubmission | start、end、signal、min_listing_calendar_days、strategy、initial_account、execution |
| risk_rule_eval / RiskRuleEvalSubmission | start、end、signal、min_listing_calendar_days、strategy、initial_account、risk、execution |
| execution_eval / ExecutionEvalSubmission | start、end、order_refs、initial_account、execution |
| strategy_eval / StrategyEvalSubmission | start、end、signal、min_listing_calendar_days、strategy、initial_account、execution |
| full_backtest / FullBacktestSubmission | start、end、signal、min_listing_calendar_days、strategy、initial_account、risk、execution |

五种请求都使用规范 `YYYY-MM-DD` 且 start<=end。信号来源是二选一的 object：
`{"type":"model","model_ref":"/absolute/model.joblib"}` 或
`{"type":"recorded","prediction_refs":["/absolute/prediction.parquet"]}`。
四种含 signal 的 submission 顶层都必须显式提供 `min_listing_calendar_days`，无论信号来源
是 model 还是 recorded；值为非负整数，不接受 bool、字符串或小数，没有默认值。
execution 不接受该字段。其资格筛选作用由
[Backtest workflow](../offline_workflow_contract.md#backtest-workflow) 定义。
order_refs 和 prediction_refs 是有序绝对路径数组；空事实可报告无样本。
模型同目录训练记录、预测身份和首次实际决策的成熟时间核验由
[Backtest workflow](../offline_workflow_contract.md#backtest-workflow) 拥有，不要求调用者
提供 model_use、生效时间、binding_refs 或训练记录路径。

交易配置的稳定字段如下；未标可选的字段均必填：

| 对象 | 字段及构造约束 |
| --- | --- |
| initial_account | ts_us 整数、trade_date、非负有限 Decimal cash、positions 非负整数映射、sellable 同键映射且不超过持仓、average_cost 同键非负有限 Decimal 映射、非负有限 peak_equity；ts_us 必须早于模拟终点 |
| execution | market_ref 绝对 JSON 路径、end_ts_us 整数、quantity_rules 证券映射、fees、正数有限 annual_factor、fill；risk_check_ts_us 按模式约束如下 |
| quantity_rules 每证券值 | buy_min_qty、buy_step_qty、sell_min_qty、sell_step_qty、fill_step_qty 都是正整数，price_tick 是正数有限 Decimal，enforce_t1 是显式 bool；不可用 bool 代替数量 |
| fees | commission_rate、stamp_tax_rate、transfer_rate、min_commission，均为非负有限 Decimal；不使用隐含费率或最低佣金 |
| risk | rules 非空数组，按声明顺序选具名规则；每项为 type=max_single_name 和非负有限 max_ratio，或 type=max_gross_exposure 和非负有限 max_gross_ratio，或 type=max_drawdown 和 [0,1] max_drawdown |
| strategy | type、params、order_window_minutes、quote；两种成交假设均须完整 quote。窗口分钟为正整数 |
| quote | buy_offset_bp、sell_offset_bp，均为 [0,10000) 的有限 Decimal；不接受额外字段 |

execution.fill 是必须显式提供的带 type 判别字段的对象，不根据 null 或缺失滑点推导成交行为：

| execution.fill | 构造与适用范围 |
| --- | --- |
| `{"type":"quoted"}` | 生效时按申报价全额成交，不接受 slippage_bp；signal_eval 固定使用，strategy/risk 也可选择 |
| `{"type":"limit_price","slippage_bp":0}` | 滑点为必填 [0,10000) 有限数；价量约束，适用于 strategy/risk，execution/full 固定使用 |

所有 fill 对象拒绝额外字段，旧的 execution.slippage_bp 不再接受。信号驱动路径必须提供
完整 strategy.quote，省略或 null 均拒绝。execution_eval 的报价取自冻结委托，不接受 strategy。
成交配置仅包含表中两个选项；参考价研究的适用边界及原 reference 的移除理由由
[Offline owner](../offline_workflow_contract.md#移除-reference-成交假设的原因) 定义。

signal_eval/strategy_eval 不接受 risk；risk_rule_eval/full_backtest 必须提供非空 rules。
只有 risk/full 可提供非空 risk_check_ts_us 整数数组，省略为空。risk 对一个场景执行一次
带指定风控的完整账户回放，评价控制动作及实际剩余风险；不接收 case_refs 或隐式生成比较基线。缺少规则或成交模型是非法请求，
不能解释为自动关闭能力。

strategy.params 只允许与 type 对应的结构：

```text
threshold: {threshold: finite-number, target_quantity: positive-int=100}
topk_hysteresis: {max_positions: positive-int, entry_threshold: finite-number,
  exit_threshold: finite-number <= entry_threshold, rebalance_interval_minutes: positive-int=1,
  keep_winners: bool=false, target_quantity: positive-int=100}
fixed_holding: {max_positions: positive-int, decision_time: local-time, exit_time: local-time,
  initial_exit_dates: {symbol: YYYY-MM-DD}}
```

fixed_holding 的两个 time 不带 UTC offset，使用市场时区。三种策略的 order_window_minutes
在交易请求必需；原两个构造器的股数默认值仍如上，新策略没有默认资金、名额或时点。
完整选择/资金/退出和风险计算语义由 Offline owner 定义，本文件只拥有参数构造。
signal_eval 和价量约束 strategy_eval 的完整请求见 [Job API 示例](job_api_contract.md#post-jobs)。
用于 CLI 时移除 kind/mode，将其余字段编码为 --input-json，并用 --mode 指定对应任务；
引用文件必须实际存在。signal_eval 保留预测指标，同时要求显式策略、初始账户、市场、
费用与数量规则，不接受原先只有预测的请求。

CLI 不构造 config override。加载一次 AppConfig 后，统一生成实验名称，将已解析 submission
序列化为参数快照并初始化 `BacktestMetrics`，使用该快照记录开始日志，再把 `experiment_name`
和 `metrics` 随 submission 传给 `run_backtest`。快照、日志及最终制品的内容由
[Backtest 结果契约](../offline_workflow_contract.md#backtest-workflow)定义；CLI 此时只构造内存记录，
不写参数或 metrics 文件。回测业务参数统一来自 submission，不再加载独立 BacktestConfig；
示例中的 min_listing_calendar_days=120 是显式任务输入，不是应用默认设置。
初始现金只来自 initial_account。HTTP 使用同一字段模型，build_cli_command 用一个
`--input-json` 参数保留嵌套 JSON，Job UUID 作为 experiment ID。
