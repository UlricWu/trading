# Offline 工作流契约

- **状态**：正式 owner
- **适用范围**：offline data、offline training 与 daily-alpha backtest 的配置选择、日程、
  步骤图、运行结果和实验命名。
- **CLI owner**：[`docs/engineering/cli_contract.md`](engineering/cli_contract.md)

## 共同边界

Workflow 只消费 composition root 已创建的配置、路径门面和身份。Workflow 装配
instrumentation、registry、context、step 和 pipeline；step 拥有各自业务行为。步骤失败
必须传播，不得通过空制品或日志改写为成功。

Training 和 backtest 在执行 step 前检查最终 experiment 目录。目录已存在时必须失败，
不得覆盖、续跑或恢复。

## Data workflow

`run_offline_standard_data` 固定执行 ingest → normalize → feature → label；
`run_offline_level2_data` 固定执行 ingest → normalize。它们不是带 group 参数的通用入口。

配置选择规则：

1. 只选择 `source.group` 分别为 `offline_standard` 或 `offline_level2` 的条目。
2. `enabled=false` 的 source 被排除。
3. `use_broker_sources=true` 的 source 按 broker registry names 展开，每项使用同名
   `raw_object` 和单元素同名 `outputs`。
4. 重复 effective source name 或没有 effective source 必须失败。
5. feature 和 label 只保留 enabled 且 group 相同的配置。

Standard feature step 只允许 `tushare_daily_basic`；label step 只允许
`daily_t1_net_excess_rank` 与 `daily_forward_excess_rank`。Level-2 不运行 feature 或 label。

Ingest 必须尝试所有 effective sources。已有已提交 raw metadata 或本次成功获取 payload
都表示该 source 可用；单个 source 无 payload 不得提前停止。仅当所有 effective sources
既无已提交 raw 又未获取到 payload 时返回 `DataRunStatus.SKIPPED`，固定原因为
`no_source_payload`，后续步骤不执行。部分 source 可用、部分 source 无 payload 时必须在
完成全部 source 尝试后失败。全部 source 可用时返回 `DataRunStatus.SUCCESS`。空 source
配置和任何后续 step 的空返回也是错误。

## Training workflow

当前训练实现固定使用 `sgd_regression`，不是运行参数。Experiment namespace 固定为：

```text
training_{start_date}_{end_date}_{experiment_id}
```

label builder 的 `target_lookahead(label_column)` 是 `eval_offset` 的唯一来源。默认日程
使用 Access 在请求闭区间内返回的正式 `daily_bar` 交易日，按日期升序。注入的日程结果
越界、重复或逆序必须失败。

- `train_window_days=0` 表示 expanding。
- 正整数 N 表示包含当前 training end 的 N 个交易日滚动窗口。
- 每个 training end 向后移动 `eval_offset` 得到单日 eval；历史或未来日期不足的 entry
  被排除；最终无 entry 必须失败。

每日步骤为 dataset build → preprocess → model train → IC evaluate；最终步骤为 artifact
persist → report。持久化 identity 字段名是 `experiment_id`。

## Backtest workflow

Experiment namespace 固定为：

```text
backtest_{start_date}_{end_date}_{experiment_id}
```

Workflow 直接接收 `BacktestConfig`。`backtest_mode`、model reference 和 strategy 只来自该
对象。默认日程使用 Access 在请求闭区间内返回的正式 `daily_bar` 交易日，并对相邻交易日
生成 timing：当前日是 signal date 与 feature date，下一日是 forward date；无 timing
必须失败。

`BacktestConfig` 还必须显式提供 `min_list_calendar_days`、`exclude_st_sessions` 和
`exclude_suspended`。Signal step 把这三个值原样传给
`docs/engineering/access.md` 定义的 `stock_universe()`，不得在 timing、step 或 Access
中设置另一套隐藏默认。当前 base config 明确使用 `120` 个自然日、最近 `20` 个可回放
交易日和剔除当日停牌。

`BacktestConfig` 不接受静态 `symbols` universe；每个 signal date 的股票集合只能由上述
Access 查询产生。

每项 timing 执行 signal → signal evaluate → tradable-alpha evaluate → portfolio → risk
evaluate → execution evaluate → accounting → full-backtest；最终执行 metrics persist →
report。初始 cash 只来自 `BacktestConfig.init_cash`。
