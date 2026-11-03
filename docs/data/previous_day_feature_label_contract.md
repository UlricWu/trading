# 前日信息盘前 Feature 与次日最高价 Label

- **状态**：采用实现草案；随 owner、代码与验证一起审查，合入前不代表目标分支已采用。
- **Owner 边界**：本文件唯一拥有 `previous_day_daily_l2/v1`、
  `previous_day_eligibility/v1`、`next_day_high_return_rank/v1` 与
  `next_day_high_top_decile/v1` 的样本、公式、资格及时间含义。
- 路径与 Meta 由 [storage_layout](storage_layout.md) 拥有；已有日频、14:30 L2、分钟与
  价格复权输入各自沿用原 owner。本身份不修改已有 v1 的含义。

## 样本及输入

T 为目标交易日，P、T+1 从正式交易日历解析。样本键为
`(symbol, trade_date=T, decision_ts_utc=T 09:00)`，时间为上海时区转 UTC 微秒。
原候选与顺序来自 P 日 `l2_stock_1430/v1`。先连接 P 日 `tushare_daily_basic/v1`，
对七列在原候选中按有效值、升序、average tie 做百分位排名，再做资格过滤。
七列与顺序为 close_return_1d、open_gap_1d、log_amount、
max_drawdown_20d_asof_tminus1、close_volatility_60d_asof_tminus1、
close_return_5d_asof_tminus1、turnover_rate_mean_20d_asof_tminus1，完整列名前缀为 `f_d_`。

Feature 的39个模型列顺序为 P 日32列 L2（保持原顺序、数值和缺失，名称加 `lag_`），
然后七列 `lag_f_d_*_rank`。L2 窗口截至 P 14:30，为5/15/30/60分钟。
不得使用 T 日 L2 或重新排名资格过滤后的子集。额外数据列为 `source_date=P`、
`feature_ready_ts=P 22:00`，以及日频原值 `baseline_amount=f_d_log_amount`、
`baseline_momentum=f_d_close_return_5d_asof_tminus1`、`baseline_reversal=-baseline_momentum`。
这些额外列不自动进入模型。

## 前日状态资格

使用 P 日 stock_basic、stock_st、suspend_d。资格只依赖状态与原候选键，不依赖标签、
特征缺失或 T 日最终状态。依次记录首次排除原因：成员缺席 `source_uncovered`、上市日期
缺失/非法 `listing_date_unknown`、上市日期晚于 T `not_yet_listed`、ST `prior_st`、
延续停牌 `prior_suspended`、停复牌状态不明 `suspension_unknown`；其余为 `eligible`。
源缺席不解释为摘牌，不附加上市120日要求。

P 日全天 S 且无 R 视为延续停牌；单独 R 保留。S 的所有合法区间均早于15:00结束时保留；
区间格式为逗号分隔的 HH:MM-HH:MM，要求09:30 ≤ start < end ≤15:00。
缺少时序的全天 S/R 冲突、非法/倒置/零长度区间视为不明；截至15:00的 S 若同时有 R，
仍视为不明。未知 suspend_type 为输入错误。

资格对象保留所有原候选的 symbol、list_date、suspension_state、reason、eligible、T、P、
assumed_available_ts 与 decision_ts_utc。Feature 只发布 eligible 行，保持原顺序。
资格假设为 P 状态于 P 22:00可用；真实历史接收时间未知。该条件情景不保证排除 T 新增
风险，不声称严格历史时点回放。T 日盘前状态及14:30正成交检查属于另外的规则，不能混入。

## 标签

在 T 日原 `l2_stock_1430/v1` universe 上计算后复权收益：入场为 T 连续竞价
`[14:31,14:36)` 的总成交额/总成交量；退出参考为 T+1 所有有效分钟（含竞价）的 high
最大值。价格按各日 adj_factor 使用已有 hfq 规则调整，缺失或非正价格/因子保持未知。
有效收益升序 average-tie 百分位排名为 `y_rank_return`；严格 `>0.9` 为 `y_event=1`，
其余有效标签为0，未知标签保持 null。最高价只作为监督参考，不表示能按该价格成交。

排名完成后按 Feature 的合格 P universe 对齐：T universe 中缺席的 symbol 保留未知标签，
不重新排名。两种 Label 分别保留对应目标列及相同样本键，另有
`label_ready_ts=T+1 22:00`。标签成熟与输入可用时间是假设边界，不能用文件 mtime 证明。

## 构建、发布与恢复

`data-previous-day-backfill --start/--end` 只消费已提交的本地日历、上述 Feature/状态、
T/T+1 两市分钟与复权因子；不联网补取。保留原 Feature/Label 不变，写入新的身份。
每个产物分别原子写 Parquet 后提交 Meta，不组成多文件事务；有效 Meta 复用，非法 Meta
或必要输入缺失失败，保留已成功产物。空目标交易日范围失败。

四类新对象的 Meta 不使用单上游关系；Parquet metadata 的 `source_inputs` JSON 保存直接
输入 payload 与 Meta 的存储相对路径及 SHA-256、`availability_basis` 固定为
`previous_session_22_assumption`、`historical_received_at=null`。Feature/资格只绑定 P 输入，
Label 另绑定 T universe 与 T/T+1 价格输入；该记录不宣称有历史实际接收证据。
正式运行不读取 research 代码或 Notebook。冻结数据恢复需在隔离根验证输入及输出逐值一致。
