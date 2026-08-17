# 日频 Feature 与 Label 契约

- **状态**：正式 owner
- **适用范围**：`tushare_daily_basic/v1`、`daily_close_return_rank_d1/v1`、
  `daily_close_return_rank_d3/v1`、`daily_close_return_rank_d5/v1` 的身份、schema、
  观测时间、计算和 maturity。
- **编排 owner**：[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md)
- **输入 owner**：[`docs/engineering/access.md`](../engineering/access.md)
- **复权 owner**：[`docs/data/price_adjustment_contract.md`](price_adjustment_contract.md)

## 共同身份

Feature 和 label 分区都以 `(symbol, trade_date)` 为唯一 key。`trade_date=T` 始终是样本的
观测日，不是运行日、训练日或交易动作日期。输出按 `symbol, trade_date` 排序。

完整输入对象、必要列或输入 key 无效时 producer 必须失败。输入对象存在且 identity 有效，
但某个 symbol 缺少计算所需的行或数值时，只把该 symbol 对应结果写为 null。不得以零、前值、
其他 symbol 或其他日期补齐。所有 feature 和 label 数值列均为 nullable float64。

Producer API 只接受已经绑定正式存储和 processed version 的 Access：

```python
TushareDailyBasicV1Builder.build(
    *, access: Access, trade_date: str,
) -> pa.Table

DailyCloseReturnRankV1Builder.lookahead: int
DailyCloseReturnRankV1Builder.label_column: str
DailyCloseReturnRankV1Builder.build(
    *, access: Access, trade_dates: Sequence[str],
) -> pa.Table
```

Producer 不接收 `PathManager`、processed version 或预读 table，不公开 `read_input` /
`build_partition` 两阶段接口。三个 label set 共享同一个按 lookahead 参数化的 producer
实现；label set identity 与 lookahead 的绑定只存在于不可变 registry。

## `tushare_daily_basic/v1`

该 feature set 是收盘后 feature。分区 `T` 的行集合精确等于 `daily_bar(T)` 的 symbol 集合，
因此在 `T` 日正式 daily bar、adjustment factor 和所需历史对象提交后可用。跨日价格统一使用
以 `T` 为 as-of 的 qfq 价格。下文 `O/H/L/C` 表示该 qfq 价格；`V`、`A` 和 `R` 分别表示
source-native `vol`、`amount` 和 `turnover_rate` 数值，producer 不做单位换算。

价格和 adjustment factor 只有有限且大于零时可参与相应计算；`vol`、`amount` 和
`turnover_rate` 只有有限且大于等于零时可参与相应计算。`d` 均指正式交易 session，
不是自然日。

精确 schema 为：

```text
symbol: string
trade_date: string
f_d_close_return_1d: float64?
f_d_open_gap_1d: float64?
f_d_intraday_return: float64?
f_d_range_vs_prev_close: float64?
f_d_log_volume: float64?
f_d_log_amount: float64?
f_d_max_drawdown_20d_asof_tminus1: float64?
f_d_close_volatility_60d_asof_tminus1: float64?
f_d_close_distance_to_high_20d_asof_tminus1: float64?
f_d_amount_mean_5d_asof_tminus1: float64?
f_d_amount_mean_20d_asof_tminus1: float64?
f_d_close_return_5d_asof_tminus1: float64?
f_d_close_return_20d_asof_tminus1: float64?
f_d_close_volatility_20d_asof_tminus1: float64?
f_d_turnover_rate_mean_20d_asof_tminus1: float64?
f_d_close_position_in_range_20d_asof_tminus1: float64?
```

当日字段定义为：

```text
f_d_close_return_1d       = C(T) / C(T-1) - 1
f_d_open_gap_1d           = O(T) / C(T-1) - 1
f_d_intraday_return       = raw_close(T) / raw_open(T) - 1
f_d_range_vs_prev_close   = (H(T) - L(T)) / C(T-1)
f_d_log_volume            = log(1 + V(T))
f_d_log_amount            = log(1 + A(T))
```

后缀 `asof_tminus1` 表示窗口结束于 `T-1`，不包含 `T`。窗口必须包含该 symbol 的完整行和
有效参与值，否则该字段为 null：

```text
f_d_max_drawdown_20d_asof_tminus1
  = 最近 20 个 C 的 min(C(s) / running_max(C)(s) - 1)

f_d_close_volatility_60d_asof_tminus1
  = 最近 60 个 close-to-close return 的样本标准差（ddof=1，使用 61 个 C）

f_d_close_distance_to_high_20d_asof_tminus1
  = C(T-1) / max(最近 20 个 H) - 1

f_d_amount_mean_5d_asof_tminus1
  = 最近 5 个 A 的算术平均

f_d_amount_mean_20d_asof_tminus1
  = 最近 20 个 A 的算术平均

f_d_close_return_5d_asof_tminus1
  = C(T-1) / C(T-6) - 1（5 个 return interval，使用 6 个 C）

f_d_close_return_20d_asof_tminus1
  = C(T-1) / C(T-21) - 1（20 个 return interval，使用 21 个 C）

f_d_close_volatility_20d_asof_tminus1
  = 最近 20 个 close-to-close return 的样本标准差（ddof=1，使用 21 个 C）

f_d_turnover_rate_mean_20d_asof_tminus1
  = 最近 20 个 R 的算术平均

f_d_close_position_in_range_20d_asof_tminus1
  = (C(T-1) - min(最近 20 个 L))
    / (max(最近 20 个 H) - min(最近 20 个 L))
```

最后一式的区间宽度不大于零时结果为 null。

## `daily_close_return_rank_dh/v1`

当前固定存在三个 label set，`h ∈ {1, 3, 5}`：

```text
daily_close_return_rank_d1/v1
daily_close_return_rank_d3/v1
daily_close_return_rank_d5/v1
```

每个 set 只拥有一个观测定义、一个输出 target 和一个 maturity。精确 schema 相同：

```text
symbol: string
trade_date: string
y_rank_return: float64?
```

分区 `T` 的行集合精确等于 `daily_bar(T)` 的 symbol 集合。令 `C_hfq` 为 hfq close：

```text
return_h(symbol, T) = C_hfq(symbol, T+h) / C_hfq(symbol, T) - 1
y_rank_return       = rank_pct_ascending_average_ties(return_h within T universe)
```

rank 只包含 return 有效的 symbol；并列值取平均序位，再除以有效 symbol 数。因此最小有效
rank 大于零，最大有效 rank 等于 `1.0`。缺少 maturity 行、close 或 factor 的 signal symbol
仍保留输出行，但 `y_rank_return=null`。

`lookahead=h` 同时是目标定义和对象 maturity。以到达日 `A` 运行时，`LabelBuildStep` 取得
截至 `A` 的最近 `h+1` 个正式交易日，首日为 `T`，末日为 `A=T+h`，并发布 label 分区
`T`。因此 `T` 收盘后没有 `T` 的 d1/d3/d5 label；它们分别在 `T+1`、`T+3`、`T+5`
正式 session 的输入对象可用后成熟。

close-to-close 观测只定义监督目标，不定义入场、退出、成交可行性、持有期、成本、benchmark
或任何交易策略。同一 feature/label 可以被不同策略消费；这些策略语义不得写回 label。

## 错误归属

- 日期、symbol 请求、正式对象 Meta/payload、对象 key 和分区日期 identity 由 Access 失败；
- producer 所需的额外 daily-bar 列缺失、join key 不唯一或计算输入结构无效时由 producer
  失败；单个 symbol 的行或数值缺失按本文规则产生 null，不升级为整个分区失败；
- label maturity 窗口长度与该 set 的 `lookahead` 不一致时由 label producer 失败；正式历史
  session 不足时由 Access 的日历边界失败；
- 空 feature/label 输出、payload 原子写入和 Meta 提交由 derived-partition 发布边界失败；
- registry identity、model `label_column` 与 label set 不匹配分别在 workflow 准备和 training
  准备边界失败。上述错误都原样传播，不转换为空制品或部分成功。
