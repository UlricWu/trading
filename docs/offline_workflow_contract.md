# Offline 工作流契约

- **状态**：正式 owner
- **适用范围**：offline data、offline training 与 daily-alpha backtest 的配置选择、日程、
  步骤图、运行结果和实验命名。
- **CLI owner**：[`docs/engineering/cli_contract.md`](engineering/cli_contract.md)

## 共同边界

Workflow 只消费 composition root 已创建的配置、路径门面和身份。Workflow 创建
instrumentation、registry、context 和具体 step，并直接拥有日程展开、skip 分支与最终
步骤。公共 `run_steps(context, steps, instrumentation)` 只按给定顺序调用 step，不拥有
业务状态、分支、错误转换或返回结果。具体 step 是可调用对象，消费 workflow 已构造为
可用状态的 context；步骤失败必须传播，不得通过空制品或日志改写为成功。

Training 和 backtest 在执行 step 前检查最终 experiment 目录。目录已存在时必须失败，
不得覆盖、续跑或恢复。

Instrumentation 只衡量具体 step 的运行耗时。Workflow 进入具体 step 执行阶段时创建
一个 Instrumentation；step 身份是具体类名。同名 step 多次执行时按名称累计总耗时与
执行次数，并同时输出平均耗时；最后输出全部 step 的总耗时。一旦进入 Instrumentation
作用域，成功、data skip 或 step 异常都必须且只能输出一次 timeline，发生异常的该次
step 耗时计入统计，原异常继续传播。Instrumentation 不记录业务 start/done，不决定
skip，不校验 context，也不改变 step 返回值。

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
`tushare_daily_basic` 日线 feature partition 不包含 `phase`；phase 的身份与边界由
[`docs/data/market_phase.md`](data/market_phase.md) 定义。

Ingest 必须尝试所有 effective sources。已有已提交 raw metadata 或本次成功获取 payload
都表示该 source 可用；单个 source 无 payload 不得提前停止。仅当所有 effective sources
既无已提交 raw 又未获取到 payload 时返回 `DataRunStatus.SKIPPED`，固定原因为
`no_source_payload`，后续步骤不执行。部分 source 可用、部分 source 无 payload 时必须在
完成全部 source 尝试后失败。全部 source 可用时返回 `DataRunStatus.SUCCESS`。空 source
配置是错误。Ingest step 只以 `bool` 向 workflow 表达是否存在可处理输入；normalize、
feature 和 label step 成功时返回 `None`。

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
persist → report。Training workflow 的公共返回值为 `None`，运行结果只由既有 training
artifact 表达；不返回 runtime context。持久化 identity 字段名是 `experiment_id`，
既有 artifact schema 不因本次执行编排改变。

Dataset build 消费 feature 与 label 已对齐的全部日线行，不按交易 phase 过滤。它仍负责
既有的价格复权、feature/label 列选择、无穷值转缺失、可配置缺失行删除和最终索引一致性
检查。

## Backtest workflow

Experiment namespace 固定为：

```text
backtest_{start_date}_{end_date}_{experiment_id}
```

Workflow 直接接收 `BacktestConfig`。`backtest_mode`、model reference 和 strategy 只来自该
对象。默认日程使用 Access 在请求闭区间内返回的正式 `daily_bar` 交易日，并对相邻交易日
生成 timing：当前日是 signal date 与 feature date，下一日是 forward date；无 timing
必须失败。

`BacktestConfig` 还必须显式提供 `min_listing_calendar_days`。Signal step 把该值原样
传给 `docs/engineering/access.md` 定义的 `universe()`；当日 ST 与停牌由该正式
universe 固定剔除，timing 和 step 不得实现另一套筛选。当前 base config 明确使用
`120` 个自然日。

`BacktestConfig` 不接受静态 `symbols` universe；每个 signal date 的股票集合只能由上述
Access 查询产生。

每项 timing 执行 signal → signal evaluate → tradable-alpha evaluate → portfolio → risk
evaluate → execution evaluate → accounting → full-backtest；最终执行 metrics persist →
report。Backtest workflow 的公共返回值为 `None`，运行结果只由既有 backtest artifact
表达；不返回 runtime context。初始 cash 只来自 `BacktestConfig.init_cash`。

通用 `MarketDataView` 只提供可观察市场事实，不提供 `phase`；`DailyView` 也不得为日线
bar 构造 `CONTINUOUS`。Daily-alpha replay 的单个日线 bar 由 daily trade gate 直接判定
为可执行，执行编排和订单 validation 不再重复检查 phase。当前 execution evaluate 继续
使用 signal date 的 raw close 作为当日回放成交价；本契约不把该价格解释为连续竞价或
盘后固定价格成交。
