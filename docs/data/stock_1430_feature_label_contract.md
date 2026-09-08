# 14:30 Level-2 Feature 与 T+1 Label 契约

- **状态**：拟议正式 owner（`research/stock-1430` H03 adoption 候选）
- **适用范围**：`l2_stock_1430/v1` Feature、
  `l2_stock_1430_t1_vwap_rank/v1` Label 的身份、schema、可见性、计算、发布和复用。
- **输入 owner**：[`level2_minute_contract.md`](level2_minute_contract.md)、
  [`price_adjustment_contract.md`](price_adjustment_contract.md)
- **Access owner**：[`../engineering/access.md`](../engineering/access.md)
- **编排 owner**：[`../offline_workflow_contract.md`](../offline_workflow_contract.md)
- **存储 owner**：[`storage_layout.md`](storage_layout.md)

## 数据集身份与版本

V1 固定产生一对共同采用的数据集：

```text
features/l2_stock_1430/v1/trade_date=T/data.parquet
labels/l2_stock_1430_t1_vwap_rank/v1/trade_date=T/data.parquet
decision grid = stock_1430_v1
```

完整 key 为：

```text
(symbol, trade_date, decision_ts_utc)
```

`trade_date` 是目标正式 session `T`，`decision_ts_utc` 是 `T 14:30:00
Asia/Shanghai` 对应的 UTC epoch microseconds。Feature 与 Label 都按完整 key 升序；Label
必须逐行继承已提交 Feature 的 key、行数和顺序。

V1 的输入版本绑定固定为：

```text
sh_stock_trade_1m/v1
sz_stock_trade_1m/v1
adj_factor/v1
```

上述绑定是 V1 计算语义的一部分，不在运行时保存或验证 lineage。任何会改变输入版本、时间
边界、universe、schema、公式、排序、null 或 rank 语义的变化必须使用新的 H03 version；不得
在 `v1` 下根据当前上游状态动态判定旧输出是否仍可复用。

固定版本只绑定计算语义，不证明同版本输入内容从未修订。V1 不检测上游数据修订，已有输出
不会因此自动失效或重建；同尺寸 payload 内容替换也不属于当前 Meta 的检测保证。研究验证
必须另外保存实际输入内容摘要及可恢复内容、代码版本和环境，不能用版本名称替代输入证据。

## 时间与可见性

全部时间边界均为 `Asia/Shanghai` wall-clock，使用半开区间：

```text
Feature visibility = T CONTINUOUS minute_start_ts_utc < 14:30
5m window          = T [14:25, 14:30)
15m window         = T [14:15, 14:30)
30m window         = T [14:00, 14:30)
60m window         = T [13:30, 14:30)
entry window       = T   [14:31, 14:36)
exit window        = T+1 [14:31, 14:36)
Label maturity     = T+1 14:36:00
```

只消费 `phase=CONTINUOUS` 的分钟行。Access 按完整日对象读取并校验持久化契约；builder
只把可见分钟用于计算。对满足输入契约的整日表，改变 `T 14:30` 及以后分钟不得改变 Feature，
改变 entry/exit 窗口外分钟不得改变 Label。这是计算可见性，不是日文件的物理裁剪 API。
计划分钟缺失时不生成 dense grid、不补零、不前向填充，
也不以更早观察替代窗口边缘。

## Feature universe

`T` 日 Feature universe 是两市分钟事实中满足以下条件的 symbol 升序集合：

```text
phase = CONTINUOUS
minute_start_ts_utc < T 14:30
```

一个 symbol 只要至少有一行满足条件就进入 universe；不要求四个 Feature 窗口内有观察。
只在 auction 出现、第一行不早于 14:30 或完全没有当日分钟事实的 symbol 不进入。V1 不读取
`stock_basic`、`stock_st`、`suspend_d`、daily bar 或 T+1 数据来决定 T 日行集合，不应用
ST、停牌、上市天数、日频或 T+1 可成交性过滤。

上海与深圳两个分钟对象都是每个所需日期的完整输入。Meta 或 payload 缺少或无效时整体失败，
不得用单一市场形成部分 universe。两个有效分钟对象合并后 symbol 或分钟 key 冲突时失败。
有效输入形成空 universe 时 builder 返回固定 schema 空表，发布边界拒绝该分区。

## Feature schema 与计算

Feature key schema 精确为：

```text
symbol: string not null
trade_date: string not null
decision_ts_utc: int64 not null
```

V1 按 `5m, 15m, 30m, 60m` 的 window-major 顺序，每个窗口固定输出以下八列：

```text
f_l2_edge_vwap_return_rank_{w}m: float64 nullable
f_l2_high_low_range_rank_{w}m: float64 nullable
f_l2_notional_rank_{w}m: float64 nullable
f_l2_trade_count_rank_{w}m: float64 nullable
f_l2_average_trade_notional_rank_{w}m: float64 nullable
f_l2_tick_signed_volume_ratio_rank_{w}m: float64 nullable
f_l2_tick_signed_notional_ratio_rank_{w}m: float64 nullable
f_l2_observed_minute_ratio_{w}m: float64 not null
```

因此输出是三列 key 后跟固定 32 个 Feature。每个窗口内，`observed` 表示该 symbol 实际存在的
计划 `minute_start_ts_utc`；H02 key 已保证同一 symbol、minute、phase 唯一。原始量定义为：

```text
minute_vwap                  = notional_sum / volume_sum
edge_vwap_return             = last_edge_minute_vwap / first_edge_minute_vwap - 1
high_low_range               = max(observed high) / min(observed low) - 1
notional                     = sum(observed notional_sum)
trade_count                  = sum(observed trade_count)
average_trade_notional       = notional / trade_count
tick_signed_volume_ratio     = sum(tick_signed_volume_sum) / sum(volume_sum)
tick_signed_notional_ratio   = sum(tick_signed_notional_sum) / sum(notional_sum)
observed_minute_ratio        = count(distinct observed minute starts) / w
```

`first_edge_minute` 是窗口起点，`last_edge_minute` 是 `14:29`。任一精确边缘分钟缺少，或其
VWAP 不是有限正数时，`edge_vwap_return` 为 null。窗口没有任何 observed 行时，除
`observed_minute_ratio=0.0` 外的七项原始量均为 null。其他除法只有分子、分母及结果满足公式
所需的有限正分母时才有效，否则为 null；不会把无效值转换为零。

除 observed minute ratio 外，七项原始量分别在完整 `T` 日 Feature universe 内独立执行：

```text
ascending=True, method="average", pct=True, valid values only
```

null 不参与分母且保持 null；非 null rank 必须有限并落在 `(0, 1]`。四个 observed minute
ratio 必须有限并落在 `[0, 1]`。

分钟级 `int64` 范围不限制跨分钟的总量。Volume、signed volume 和 trade count 在窗口内
精确求整数和，不允许静默溢出；trade count 排名在转换为浮点数前完成，不能把不同的整数
总量舍入成 tie。除法进入 `float64` 计算，浮点总量或公式结果非有限时按上述 null 规则处理。

## Label schema 与计算

Label schema 精确为：

```text
symbol: string not null
trade_date: string not null
decision_ts_utc: int64 not null
y_rank_return: float64 nullable
```

`T+1` 只由正式 trade calendar 的下一正式 session 决定。Entry 和 exit 分别在对应五个计划
分钟中聚合实际观察到的 CONTINUOUS 分钟行：

```text
raw_vwap = sum(notional_sum) / sum(volume_sum)
```

这里的 volume 同样先精确求整数和，再进入浮点除法。

至少一个观察分钟即可产生候选 VWAP，不要求五分钟全部存在。然后使用各自日期的正式
`adj_factor/v1`：

```text
gross_return = exit_raw_vwap(T+1) * adj_factor(T+1)
             / (entry_raw_vwap(T) * adj_factor(T)) - 1
```

VWAP、factor 和复权后价格都必须是有限正数；某个 Feature symbol 的 entry、exit 或任一 factor
缺失或无效时，其 `gross_return` 与 Label 为 null，但该行保留。必要的整日分钟或 factor 对象
缺少或无效时整体失败，不能把对象缺失解释为逐 symbol null。

`y_rank_return` 在 Feature universe 内对有效 `gross_return` 执行 ascending、average-tie、
valid-only percentile rank。null 不参与分母并保持 null，非 null 值必须有限且落在 `(0, 1]`。
全部 Label 都为 null 的非空分区仍是结构有效的可发布结果；真实数据验收必须另外证明至少存在
一个有效 Label。

`T+1 14:36` 是数据 maturity 语义，不是 workflow 读取当前系统时间的运行门槛。调用方负责只在
应当构建时运行；builder、Step 和 Access 不读取当前时间。

## 计算 API 与边界

两个纯函数直接拥有领域计算，不引入 builder 实例、通用 context 配置或 engine：

```python
build_stock_1430_features(minutes: pa.Table, *, trade_date: date) -> pa.Table
build_stock_1430_labels(
    *, feature_keys: pa.Table, entry_minutes: pa.Table, exit_minutes: pa.Table,
    entry_factors: pd.DataFrame, exit_factors: pd.DataFrame,
    trade_date: date, next_trade_date: date,
) -> pa.Table
```

Step 把 Access 已解析的规范 session 字符串转换为 `datetime.date`。Builder 不再接受字符串
日期，不重复解析日历或检查 `T+1 > T`；紧邻下一 session 由 `Access.next_trade_date` 唯一建立。
分钟表和 factor 表必须是各自日期的 Access 结果，factor 使用完整返回表，使缺失 symbol 保留
为 null；不把 Feature symbols 作为要求全部存在的 Access 参数。

Label 只消费已提交 Feature 的三字段 key。Step 在 Label miss 时通过 `meta.require` 取得
可消费 payload 路径并用 context manager 读取 key 列；builder 校验类型、非空 key 值、唯一性、
目标日期、14:30 时间和顺序。`STOCK_1430_KEY_SCHEMA` 在 builder 中声明，供两种输出 schema
和 Step 投影共同使用；Label 输出直接继承输入 key 数组，不重新构造或排序行集合。
Label 直接由 Arrow 数组构造，不再携带中间 Pandas DataFrame 的 schema metadata；规范 Arrow
字段、值和顺序保持上述定义，不承诺与旧候选 payload 字节一致。

输出 schema 由 Arrow 构造固定；计算直接建立有限值/null、rank 和 observed ratio 的规则，
不对刚构造的输出重复扫描并校验。类型不符合上述 API 的调用不定义兼容、字符串数字转换或
旧异常文本；分钟、factor 和持久化 key 的责任边界仍按各自 owner 失败。

## 发布与复用

Feature 和 Label 分区各自使用 `steps` 包内部共享的 `_publish_derived_partition` 边界：有效 Meta
返回复用结果；只有 miss 才同步调用构建能力，要求非空，先原子写入单一 Parquet payload，
再提交同目录 Meta，返回发布行数。具体 Step 记录结果日志。H03 Meta 精确使用通用无 upstream、无 `symbol_slices`
形式：

```json
{
  "payload": "data.parquet",
  "size_bytes": 123
}
```

只有 Meta 不存在表示 miss。对于固定数据集 identity、version 和日期，已有 Meta 的自身 schema、
payload identity 与 payload size 有效时必须立即复用；不得读取分钟、factor、Feature payload 或
当前上游状态来重新证明可复用。Meta 已存在但自身无效，或包含 H03 禁止的 `upstream` /
`symbol_slices` 时失败，不得覆盖或降级为 miss。

一个目标日期固定先处理 Feature，再处理 Label。Feature 成功提交后 Label 失败时 Feature 保留；
较早日期成功后较晚日期失败时较早分区保留。重跑时每个有效 Meta 独立命中并从首个 miss 续建，
不定义跨对象事务。

## 错误归属与非目标

- 正式 session 解析、下一正式 session、两市分钟对象和 factor 对象的 Meta/payload/schema/date/key
  读取边界由 Access 拥有。
- 纯 builder 消费对应日期的 Access 返回表，信任 H02 已保证的分钟数值，不重复检查这些
  已建立的对象契约。Label builder 仍校验从已提交 Feature payload 取得的三字段 key；该读取
  没有经过 Access 的分钟或 factor 边界。
- Feature/Label 时间过滤、universe、公式、rank、输出 schema、key、排序和 null 结果由
  builder 拥有。
- 固定 identity/version、Feature-before-Label、部分成功和日志上下文由 H03 Step 与 workflow
  拥有；Meta reuse、非空输出和原子发布使用编排 owner 定义的包内部共享发布边界，分区结果
  日志由具体 Step 记录。
- 错误原样传播；不得跳过日期、缩小市场或行集合、使用 fallback 数据、覆盖已有对象或转换为
  success。

V1 不定义日频融合、order book、模型、组合、交易、成本、滑点、多决策时点、实时 source、
HTTP Job、cron、MQTT、正式历史回填状态、旧版本兼容、动态 lineage 校验或未来版本。
