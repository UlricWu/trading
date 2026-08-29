# CLI 契约

- **状态**：正式 owner
- **适用范围**：`AppConfig` 加载契约，以及 `python -m src.cli` 的命令、参数、退出码和 composition-root 副作用。
- **业务工作流 owner**：[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md)
- **HTTP job owner**：[`docs/engineering/job_api_contract.md`](job_api_contract.md)

## 通用边界

CLI 只把不可信文本解析为 `src.jobs.requests` 已构造的 data、fact bootstrap、Feature
backfill、training 或 backtest submission，加载一次 `AppConfig`，创建 `PathManager` 并调用
一次工作流。HTTP Job API
也必须复用同一构造边界，不得各自实现日期、mode、model experiment 或 strategy 校验。
CLI 在命令分派前按日志技术 owner 配置一次公共 logger 的 stderr sink；它不记录
start/done 或原始 JSON，workflow 负责业务运行日志。Typer 负责参数错误并以 `2` 退出，
未处理的配置或运行错误以 `1` 退出。

日期必须是规范 `YYYY-MM-DD`。范围必须满足 `start <= end`。Training 和 backtest 的
`EXPERIMENT_ID` 必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`。

## AppConfig

`AppConfig.load()` 是完整应用配置的唯一加载入口。调用者一次获得 `environment`、
`storage_root`、`secret`、`data`、`model` 和 `backtest`，无需分别识别或加载配置区段。
进程变量 `ENV` 选择项目根目录的 `.env.dev`、`.env.test` 或 `.env.prod`，缺省为
`dev`；加载过程不得把文件内容写入进程环境。`ZERO_STORAGE_ROOT` 仍由进程环境提供，
路径可用性由 `PathManager` 校验。

所选 `.env` 是 FTP 和 Tushare 运行配置的正式来源。`FTP_HOST`、`FTP_USER` 和
`TUSHARE_TOKEN` 必须非空白，`FTP_PASSWORD` 必须非空且不得裁剪。`FTP_PORT` 缺失或为空
时为 `21`，显式值必须是 `1..65535` 的整数。`TUSHARE_GATEWAY` 缺失或为空时为 `None`，
表示使用 Tushare SDK 默认地址；非空值覆盖 SDK 地址。

`override` 只能包含 `data`、`model` 或 `backtest` 根键；因此不能改变
`environment`、`storage_root` 或 `secret`。mapping 递归合并，标量、列表和显式
`None` 直接替换，不定义字段级特殊合并规则。合并后必须校验完整 `AppConfig`。HTTP Job
API 和七个 CLI 命令都不得用 request runtime 字段构造 config override。配置读取、
override 拒绝和最终 schema 校验错误均归 `AppConfig.load()`，不在下游组件重复校验。

## Data

```text
python -m src.cli data-calendar
python -m src.cli data-standard-bootstrap --start YYYY-MM-DD --end YYYY-MM-DD
python -m src.cli data-standard --start YYYY-MM-DD --end YYYY-MM-DD
python -m src.cli data-level2 --start YYYY-MM-DD --end YYYY-MM-DD
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

`data-feature-backfill` 是 CLI-only 的单 Feature 历史回填入口。`FEATURE_SET` 与 `VERSION`
共同选择一个精确 registry identity，`start/end` 闭区间只表示目标 Feature 分区。它构造
`FeatureBackfillSubmission` 并只调用一次 `run_feature_backfill`。Workflow 只消费已经提交的
calendar 与 Standard facts，不调用 broker，不写 raw、processed、label 或 experiment；
每个 Meta miss 的历史输入 session 数只从精确 builder 的 `lookback_sessions` 取得。范围内
没有正式交易 session 时成功；显式目标缺少完整历史或必要 facts 时退出 `1`，不得跳过或
缩小范围。该命令不属于 HTTP Job API 或 cron。

## Training

```text
python -m src.cli train \
  --start YYYY-MM-DD \
  --end YYYY-MM-DD \
  --experiment-id EXPERIMENT_ID
```

Training 只使用基础 `AppConfig.model`，不接受 group 或配置 JSON override。CLI 将已校验
`TrainingSubmission`、experiment ID、`ModelConfig` 和 `PathManager` 直接交给 training
workflow。
通过 HTTP 创建 training Job 时，Job API 使用同一个 Job UUID 作为该必填
`EXPERIMENT_ID`；HTTP 不接受客户端提供此字段。

## Backtest

```text
python -m src.cli backtest \
  --mode MODE \
  --start YYYY-MM-DD \
  --end YYYY-MM-DD \
  --experiment-id EXPERIMENT_ID \
  --model-experiment MODEL_EXPERIMENT \
  --strategy-json JSON
```

`MODE` 必须是 `signal_eval`、`tradable_alpha_eval`、`execution_eval`、`risk_eval` 或
`full_backtest`。`MODEL_EXPERIMENT` 必须是安全单一 basename。`JSON` 必须完整匹配以下
任一对象，额外字段禁止：

```text
{"type":"threshold","params":{"threshold":finite-number,"target_quantity":positive-int=100}}

{"type":"topk_hysteresis","params":{
  "max_positions":positive-int,
  "entry_threshold":finite-number,
  "exit_threshold":finite-number <= entry_threshold,
  "rebalance_interval_minutes":positive-int=1,
  "keep_winners":bool=false,
  "target_quantity":positive-int=100
}}
```

CLI 不构造 config override。它加载一次只含静态执行设置的 `BacktestConfig`，并把该对象、
完整 `BacktestSubmission`、`PathManager` 与 experiment ID 直接交给 workflow。Runtime
mode、model experiment 和 strategy 的唯一 owner 是 submission；`BacktestConfig` 只拥有
`init_cash` 与 `min_listing_calendar_days`。CLI 不接受 group、run ID、model name 或可选
策略。
通过 HTTP 创建 backtest Job 时，Job API 使用同一个 Job UUID 作为该必填
`EXPERIMENT_ID`；HTTP 不接受客户端提供此字段。
