# Level-2 股票分钟事实契约

- **状态**：正式 owner
- **适用范围**：`sh_stock_trade_1m/v1` 与 `sz_stock_trade_1m/v1` 的身份、schema、
  分钟聚合、排序、lineage 和 producer 错误边界。
- **输入 owner**：[`docs/data/level2_normalization.md`](level2_normalization.md)
- **Phase owner**：[`docs/data/market_phase.md`](market_phase.md)
- **Access owner**：[`docs/engineering/access.md`](../engineering/access.md)
- **编排 owner**：[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md)
- **存储 owner**：[`docs/data/storage_layout.md`](storage_layout.md)

## 数据集身份与输入

两个 V1 数据集分别表示上海和深圳股票的稀疏一分钟正成交事实：

```text
processed/sh_stock_trade_1m/v1/trade_date=T
processed/sz_stock_trade_1m/v1/trade_date=T
```

每个输出只消费同交易所、同日期的一个已提交逐笔事实对象：

```text
sh_stock_trade_1m(T) <- sh_trade(T)
sz_stock_trade_1m(T) <- sz_trade(T)
```

Producer 通过绑定同一正式存储和 processed version 的 Access，先取得显式交易所当日实际
观察到的全部 symbol，再按有限 symbol batch 读取逐笔事实。`level2_symbols()` 返回所有正式
`security_type`，不建立股票 universe；只有逐笔事实中持久化的 `security_type=stock` 行进入
分钟结果。batch 大小是实现细节，不属于数据集身份或公共 API。

Builder 的公共计算入口为：

```python
build_level2_stock_trade_1m(
    trades: pa.Table,
    *,
    trade_date: str,
) -> pa.Table
```

`trades` 必须提供：

```text
symbol, ts_utc, main_seq, sub_seq, price, volume,
security_type, phase, notional, trade_side
```

这些输入字段的类型、source 映射、正成交过滤、证券分类、phase 和 tick-rule 方向由输入 owner
定义；分钟 producer 不重新解释 raw 字段，不读取 broker，不自行补充证券类型或 phase。

## 分钟身份与稀疏关系

分钟起点由 UTC epoch microseconds 对 `60_000_000` 向下取整：

```text
minute_start_ts_utc = floor(ts_utc / 60_000_000) * 60_000_000
```

完整唯一 key 为：

```text
(symbol, trade_date, minute_start_ts_utc, phase)
```

`phase` 必须进入 key；AUCTION 与 CONTINUOUS 即使落在同一 wall-clock minute 也不得合并。
输出只包含实际观察到至少一笔 stock trade 的 key。没有输出行不表示该分钟成交量为零，不建立
dense session grid，不补零、不前向填充，也不为午休或其他未观察分钟生成行。

## Schema

输出字段、顺序、Arrow 类型和 nullability 精确为：

```text
symbol: string not null
trade_date: string not null
minute_start_ts_utc: int64 not null
phase: int8 not null
open: float64 not null
high: float64 not null
low: float64 not null
close: float64 not null
volume_sum: int64 not null
notional_sum: float64 not null
trade_count: int64 not null
tick_signed_volume_sum: int64 not null
tick_signed_notional_sum: float64 not null
```

输出必须按完整 key 全局升序，且 key 不得重复。有效上游没有 stock 行时，producer 必须发布
上述固定 schema 的零行对象；missing 或 invalid upstream 不能转换为有效空对象。

## 聚合

每个完整 key 内先按下列顺序升序排列逐笔事实：

```text
(ts_utc, main_seq, sub_seq)
```

`ts_utc` 是事件时间主序；`main_seq` 与 `sub_seq` 只提供输入 owner 已定义的确定性排列，不把
不同 `main_seq` 的数值顺序解释为跨通道因果关系。完全相同的
`(symbol, ts_utc, main_seq, sub_seq)` 具有不同 `price` 时，开收盘价无法确定，整个构建必须
失败。身份与价格都相同的重复行保留其多重性，不去重。

每个 key 的输出为：

```text
open                         = 排序后第一笔 price
high                         = max(price)
low                          = min(price)
close                        = 排序后最后一笔 price
volume_sum                   = sum(volume)
notional_sum                 = sum(notional)
trade_count                  = 输入行数
tick_signed_volume_sum       = sum(volume * trade_side)
tick_signed_notional_sum     = sum(notional * trade_side)
```

`trade_side` 及两个 signed 输出只表示 tick-rule direction proxy，不解释为交易所认证的主动
买卖方向。整数聚合结果必须能够安全表示为 `int64`；溢出必须失败。参与计算的 stock 数值和
最终数值必须为有限值，NaN 或 Infinity 不能发布。

一次完整交易所日不得整体转换为 Pandas。不同有限 batch 必须产生逻辑相同的完整日结果；
producer 可以在内存中拼接分钟级 batch，但不得因此改变 schema、key、排序或聚合值。

## 发布与复用

每个 exchange/date 独立先原子发布 `data.parquet`，再提交同目录 `meta.json`。Meta 必须记录
对应 `sh_trade(T)` 或 `sz_trade(T)` 的唯一 direct upstream，不写 `symbol_slices`。

输出 Meta 不存在时构建并发布。输出 Meta 已存在且 payload、direct upstream 和本文禁止的
`symbol_slices` 状态全部有效时直接复用，不读取逐笔输入或重算。已存在输出绑定其他 upstream、
包含 `symbol_slices` 或自身无效时必须失败，不得覆盖或降级为 miss。

上海、深圳与不同日期不是一个事务。按编排 owner 定义的顺序首次失败时终止；此前已经提交的
对象保留，重跑从尚未提交的对象继续。

## 错误归属

- 日期、exchange、symbol 请求以及正式逐笔对象的 Meta、payload、slice 覆盖和市场范围由
  Access 边界失败；Access 不执行股票过滤或分钟聚合。
- 必要输入列、stock 行的非空 symbol、数值有限性、排序身份价格冲突、聚合溢出、输出 schema、
  key、排序和聚合结果由分钟 builder 失败。
- 输出路径、现有 Meta、direct upstream、禁止的 `symbol_slices`、payload 原子发布和 Meta
  提交由分钟 Step 失败。
- Broker 与 Level-2 Normalize 只提供各自 owner 定义的逐笔事实，不判断分钟对象是否可发布，
  也不把分钟错误转换为 source 缺失或空响应。
- 上述错误原样传播；不得跳过交易所、缩小日期范围、发布部分分区内容或转换为 success。

## 非目标

当前不定义 order/order-book、撤单、盘口、非股票分钟事实、dense 分钟、Feature、Label、训练、
回放、HTTP、cron、MQTT、FTP 下载、跨交易所事务、旧 API 兼容或正式历史回填状态。逐笔
`volume/notional` 的 source-native 数值尺度继续由输入 owner 定义；本契约只拥有同字段在
stock 分钟内的守恒聚合。
