# 14:30 日频与 Level-2 Feature 融合契约

- **状态**：拟议正式 owner（`research/stock-1430` H04 候选）
- **适用范围**：`stock_1430_daily_l2/v1` 的输入绑定、P/T、schema、对齐、排名和消费边界。
- **输入 owner**：[`daily_feature_label_contract.md`](daily_feature_label_contract.md)、
  [`stock_1430_feature_label_contract.md`](stock_1430_feature_label_contract.md)
- **日历 owner**：[`../engineering/access.md`](../engineering/access.md)
- **编排 owner**：[`../offline_workflow_contract.md`](../offline_workflow_contract.md)
- **存储 owner**：[`storage_layout.md`](storage_layout.md)

## Identity 与时间

```text
output = features/stock_1430_daily_l2/v1/trade_date=T/data.parquet
P      = T 之前紧邻的正式交易 session
input  = features/l2_stock_1430/v1/trade_date=T/data.parquet
       + features/tushare_daily_basic/v1/trade_date=P/data.parquet
grid   = stock_1430_v1
key    = (symbol, trade_date, decision_ts_utc)
```

Step 在输出 Meta miss 后调用 `Access.recent_trade_dates(end_date=T, sessions=2)` 取得
升序的 `[P, T]`。正式日历唯一拥有相邻关系；builder 接收解析后的 `date`，不再次查询或
推断 session。跨周末、长假、年度边界使用相同规则。

Daily P 的 `close_return_1d`、`open_gap_1d` 和 `log_amount` 包含 P 日信息；带
`asof_tminus1` 的源字段窗口结束于 P 的前一 session，即相对 T 的第二个前序 session。
输出同名后缀仍引用源分区 P。H04 不重新计算日频历史、收益或复权，也不读取 daily T。

## Schema、对齐与排名

输出直接继承 L2 T 的三字段 key、行集合、行序和全部 32 个 Feature 数组。三字段 key 为
非空 `string, string, int64`；timestamp 是 T 日上海时间 14:30 对应的 UTC epoch
microseconds。32 列保持 H03 的 window-major 顺序、类型、null 和数值，包括四个非空
observed-minute ratio。

随后按下表顺序追加七个 nullable `float64`，输出共 42 列，即 3 key + 39 Feature：

| P 分区源字段 | 输出字段 |
| --- | --- |
| `f_d_close_return_1d` | `f_d_close_return_1d_rank` |
| `f_d_open_gap_1d` | `f_d_open_gap_1d_rank` |
| `f_d_log_amount` | `f_d_log_amount_rank` |
| `f_d_max_drawdown_20d_asof_tminus1` | `f_d_max_drawdown_20d_asof_tminus1_rank` |
| `f_d_close_volatility_60d_asof_tminus1` | `f_d_close_volatility_60d_asof_tminus1_rank` |
| `f_d_close_return_5d_asof_tminus1` | `f_d_close_return_5d_asof_tminus1_rank` |
| `f_d_turnover_rate_mean_20d_asof_tminus1` | `f_d_turnover_rate_mean_20d_asof_tminus1_rank` |

先以 symbol 将 daily P 七个原始值对齐到 L2 T 的有序行集合，再逐列执行：

```text
ascending=True, method="average", pct=True, valid values only
rank = 平均序位 / 该列在 L2 T universe 内的有效值数
```

Daily 多余 symbol 不参与排名；L2 symbol 缺少 daily 行时保留该行并令七列为 null。
源 null/NaN 是逻辑缺失，保持 null 且不参与分母。各列有效值数独立，不预先做 complete-case
删行。一个有效值的 rank 为 1；两个相同有效值均为 0.75；`[10,20,20,null]` 得到
`[1/3,5/6,5/6,null]`。所有量保持升序，包括负值形式的最大回撤。

非空 L2 输入配合全缺失日频量仍产生非空融合输出；七列可以全部为 null。输出不添加输入日期、
缺失掩码或其他辅助列。排名有效值位于 `(0,1]`；不保证最高并列值的 rank 等于 1。

## 计算 API 与输入责任

```python
build_stock_1430_daily_l2_features(
    l2_features: pa.Table,
    daily_features: pa.Table,
    *,
    trade_date: date,
    previous_trade_date: date,
) -> pa.Table
```

Step 先通过 `meta.require` 获取两个精确 Feature identity 的 payload，拒绝输入 Meta 的
`upstream` / `symbol_slices` 关系字段，在 context manager
内读取 Parquet。L2 读取完整表；daily 只投影 `symbol, trade_date` 和上述七列。必要列缺失或
重名必须失败，不通过列交集、重排或补 null 伪造输入兼容。

Feature payload 没有经过 Access 的 processed 表读取边界，因此 builder 负责一次输入表
校验：

- L2 schema 的有序字段、类型及可空性精确等于 H03 Feature schema；忽略非业务 Arrow
  schema metadata。三 key 的类型、非空、唯一性、T、14:30 和规范顺序复用 H03 的具名校验。
- Daily 两个 key 必须是非空逻辑字符串，symbol 唯一且每行日期精确等于 P；逻辑字符串按
  `table_ops` 契约解释，包括 Arrow string/large_string。必要的七列必须为 float64。
  上游其余九个 Feature 不参与 H04 的计算或列选择。
- 七个日频数值可为有限值或逻辑缺失；正负无穷表示无效输入，整体失败，不参与 rank，也不
  静默转换为 null。
- 有效 daily P 不能是空分区。L2 空表可形成固定 schema 空输出，由共享发布边界拒绝。

Builder 不修改输入表，不重新验证上游分钟或日频公式，不对刚构造的输出重复扫描。
H03 共享的 schema、decision time 和 key 校验只保留一个声明；H04 不复制 H03 业务常量。

## 发布、复用与版本

H04 复用 `steps._derived_partition._publish_derived_partition`。Meta 精确只含
`payload` 和 `size_bytes`；禁止 `upstream` 和 `symbol_slices`。只有 Meta 不存在是 miss；
孤立 payload 不构成可消费对象。有效输出 Meta 立即复用，不打开其 Parquet 内容，也不解析 P
或读取两个上游。已有无效 Meta 必须失败，不覆盖、不降级为 miss。

Miss 才读取 P/T 输入。必要输入整分区缺失或无效时失败，不读取更早日、daily T、其他版本，
也不生成 L2-only 结果。发布先原子写 payload，再提交 Meta；不增加跨对象事务或并发协调。
较早日期成功、较晚日期失败时已提交结果保留，重跑按 Meta 从 miss 续建。

输入 set/version、P/T、universe、schema、公式、排序、null 或 rank 语义变化必须使用新
H04 version。同版本上游内容修订不自动使已有输出失效，Meta 不检测同尺寸内容替换。
实际输入摘要和可恢复内容由研究证据保存，不加入运行时对象 Meta。

本契约只描述计算的时间截断，不证明历史文件从未事后修订或生产系统当时已按时物化。
历史可见性声明仍需上游 as-of 证据。H04 不定义 Label、训练、因子价值、交易、HTTP、cron、
实时源、缺失填充、中性化、自动刷新或未来版本。
