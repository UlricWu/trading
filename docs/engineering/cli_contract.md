# CLI 契约

- **状态**：正式 owner
- **适用范围**：`python -m src.cli` 的命令、参数、退出码和 composition-root 副作用。
- **业务工作流 owner**：[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md)
- **HTTP job owner**：[`docs/engineering/job_api_contract.md`](job_api_contract.md)

## 通用边界

CLI 只把不可信文本解析为 `src.jobs.requests` 已构造的 data、training 或 backtest
submission，加载一次 `AppConfig`，创建 `PathManager` 并调用一次工作流。HTTP Job API
也必须复用同一构造边界，不得各自实现日期、mode、model experiment 或 strategy 校验。
CLI 不记录 start/done 或原始 JSON；workflow 负责业务运行日志。Typer 负责参数错误并以
`2` 退出，未处理的配置或运行错误以 `1` 退出。

日期必须是规范 `YYYY-MM-DD`。范围必须满足 `start <= end`。Training 和 backtest 的
`EXPERIMENT_ID` 必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`。

## Data

```text
python -m src.cli data-standard DATE
python -m src.cli data-level2 DATE
```

两个命令分别调用固定的 standard 或 Level-2 workflow，不接受 group、run ID 或实验身份。
数据对象由 source、version 和 date 标识，不属于一次 experiment。Workflow 返回
`DataRunStatus.SUCCESS` 时退出 `0`；返回 `DataRunStatus.SKIPPED` 时退出 `75`。其他错误退出
`1`，不得改写成 skipped。

## Training

```text
python -m src.cli train \
  --start YYYY-MM-DD \
  --end YYYY-MM-DD \
  --experiment-id EXPERIMENT_ID
```

Training 只使用基础 `AppConfig.model`，不接受 group 或配置 JSON override。CLI 将已校验
范围、experiment ID、`ModelConfig` 和 `PathManager` 直接交给 training workflow。
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

CLI 只覆盖 `backtest.backtest_mode`、`backtest.model` 和完整的 `backtest.strategy`，然后把
最终 `BacktestConfig` 交给 workflow。它不接受 group、run ID、model name 或可选策略。
通过 HTTP 创建 backtest Job 时，Job API 使用同一个 Job UUID 作为该必填
`EXPERIMENT_ID`；HTTP 不接受客户端提供此字段。
