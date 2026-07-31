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

`run_offline_data(app_config, path_manager, submission)` 是唯一 Python workflow 入口，直接
消费已经校验的 `DataSubmission(kind, start, end)`。`kind` 只允许
`data-standard` 或 `data-level2`，完整闭区间是一个 workflow 执行单位。单日使用
`start == end`，不得拆成每日独立 Job。

配置选择规则：

1. `data-standard` 与 `data-level2` 分别选择 `source.group` 为
   `offline_standard` 与 `offline_level2` 的条目。
2. `enabled=false` 的 source 被排除。
3. `use_broker_sources=true` 的 source 按 broker registry names 展开，每项使用同名
   `raw_object` 和单元素同名 `outputs`。
4. 重复 effective source name 或没有 effective source 必须失败。
5. Feature 与 label 不使用 group。所有 enabled feature 必须属于
   `{"tushare_daily_basic"}`，所有 enabled label 必须属于
   `{"daily_t1_net_excess_rank", "daily_forward_excess_rank"}`；额外 enabled identity
   必须在 workflow 开始前失败，不能静默忽略。

Standard feature step 只允许 `tushare_daily_basic`；label step 只允许
`daily_t1_net_excess_rank` 与 `daily_forward_excess_rank`。Level-2 不运行 feature 或 label。
`tushare_daily_basic` 日线 feature partition 不包含 `phase`；phase 的身份与边界由
[`docs/data/market_phase.md`](data/market_phase.md) 定义。

两个 kind 都先按自然日升序完成整个 `[start, end]` 的 `trade_calendar` ingest 与
normalize。日历 source、查询、schema、开市判定和 `daily_bar` 关系只由
[`docs/data/source_contract.md`](data/source_contract.md) 定义。日历完整后，workflow
取得其中全部正式交易日，再按日期升序执行 kind 对应的事实层：

- Standard 对每个正式交易日执行除 `trade_calendar` 外的 standard ingest 与 normalize。
- Level-2 对每个正式交易日执行 Level-2 ingest 与 normalize。
- 休市日不运行上述事实步骤。只包含休市日的范围返回 `DataRunStatus.SUCCESS`。

每个日期的 ingest 必须尝试该 kind 的全部 effective fact sources。已有已提交 raw Meta
或本次成功获取 payload 都表示该 source 可用；单个 source 无 payload 不得提前停止。部分
source 可用、部分 source 无 payload 时，必须在尝试完该日期全部 source 后失败。
Standard 的正式开市日全部 source 都无 payload 时也是数据缺失并失败。Level-2 只有在
范围包含正式交易日、且所有正式交易日的全部 effective source 都无 payload 时返回
`DataRunStatus.SKIPPED`，固定原因为 `no_source_payload`；部分日期完整、部分日期全部
缺失必须失败。

整个范围的事实层完成后，Standard 按正式交易日升序运行 feature，并在同一个到达日生成
已经成熟的 label：

```text
arrival D:
    feature(D)
    daily_t1_net_excess_rank(target = D 前第 2 个正式交易日)
    daily_forward_excess_rank(target = D 前第 5 个正式交易日)
```

通用规则是：label builder 全部 output column 的最大 `target_lookahead` 为 `L`；
workflow 取截至并包含到达日 `D` 的最近 `L + 1` 个正式交易日，将第一个日期作为 label
partition identity，并把完整窗口直接交给 builder。范围开始前所需的日历、processed
事实和 feature 历史必须已经存在；workflow 不隐式扩大请求范围。任一必要对象缺失时
失败。

Normalize、feature 或 label 产生零行都是失败，不得只记录 warning、不得省略 payload。
`DataRunStatus.SUCCESS` 保证请求范围内全部应有的日历、事实、feature 和已成熟 label
对象都存在有效 payload 与 Meta；已有有效对象可以直接复用。Ingest step 只以 `bool`
表达当前日期是否存在可处理输入；normalize、feature 和 label 成功时返回 `None`。

## Training workflow

当前训练实现固定使用 `sgd_regression`，不是运行参数。Experiment namespace 固定为：

```text
training_{start_date}_{end_date}_{experiment_id}
```

label builder 的 `target_lookahead(label_column)` 是 `eval_offset` 的唯一来源。默认日程
使用 Access 在请求闭区间内返回的正式交易日历日期，按日期升序。注入的日程结果越界、
重复或逆序必须失败。

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
对象。默认日程使用 Access 在请求闭区间内返回的正式交易日历日期，并对相邻交易日生成
timing：当前日是 signal date 与 feature date，下一日是 forward date；无 timing 必须
失败。

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
