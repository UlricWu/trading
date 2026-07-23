# Level-2 归一化契约

- **状态**：强制执行
- **适用范围**：Level-2 trade source 路由、`TradeTime` 解析、`ts_utc` 时间字段和
  processed symbol slice index。

## Source 路由

| raw object | processed dataset | exchange |
|---|---|---|
| `SH_Stock_OrderTrade` | `sh_trade` | 上海 |
| `SZ_Trade` | `sz_trade` | 深圳 |

`sh_trade` 只包含上海数据，`sz_trade` 只包含深圳数据。交易所由 processed dataset
身份携带，不写入每一行，也不编码进 `TradeTime`。其他 raw object 与 processed dataset
路由组合必须在解析 batch 前以 `ValueError` 拒绝；输入为空不改变该规则。

## TradeTime

`TradeTime` 是 `Asia/Shanghai` 本地 wall-clock，必须严格使用
`YYYY-MM-DD HH:MM:SS.f` 到 `YYYY-MM-DD HH:MM:SS.ffffff`。小数是秒的小数，解析时
右补零到六位；不得包含 null、首尾空白、缺失的小数部分或超过六位的小数。任一值
不合法时，整个输入 batch 必须失败，不得截断、填充缺失值或跳过该行。
非字符串列必须抛出 `TypeError`；缺少字段、null、格式或日历时间非法必须抛出
`ValueError`。

`TickTime` 不参与 `ts_utc` 计算。

## ts_utc

`ts_utc` 是 `int64` UTC epoch microseconds。Normalize 必须把 `TradeTime` 按
`Asia/Shanghai` 解释后直接转换为 `ts_utc`，不得先伪装成 UTC 再手动加减固定偏移。

`DateTimeUtils` 拥有对应的标量日期与时区转换语义；`parser_engine` 只拥有上述
source-native 字符串的 Arrow 向量化解析。交易时段、交易日历和其他 normalized 字段
不属于本文件。

## Symbol slice index

Level-2 Normalize 必须把输出按 `(symbol, ts_utc)` 升序排序。同一 `(symbol, ts_utc)`
下的多行不定义额外排序键、稳定性或 source order。

每个 symbol 在输出中必须只占一个非空半开区间 `[start, end)`。边界必须是整数且不得
是布尔值，并满足 `0 <= start < end`。按 `start` 排序后，第一个区间必须从 `0` 开始，
相邻区间必须首尾相接，最后一个 `end` 必须等于 Parquet 总行数。由此每行恰好属于一个
symbol，且同一 symbol 不得出现在区间之外。Normalize producer 负责保证区间中的行确实
具有该 symbol；consumer 不重复扫描 symbol 列验证。

Meta 中的 `symbol_slices` 是以裸 `symbol` 为 key 的 object；每个 value 精确包含
`start` 和 `end`。`symbol` 是跨 `sh_trade` 与 `sz_trade` 的全局身份；两个数据集出现
相同 symbol 时 Access 必须失败，不得覆盖或拼接。Meta 不保存 output path、row-group
位置或 index header。

provider enrichment 可以按 symbol slice 修改列，但不得改变行数或行顺序。index 建立
后的其他 Normalize 变换也必须保持行数和行顺序。Access 在加载 Meta 后以 Parquet 总
行数校验完整覆盖；row-group overlap 只属于 Access 的运行时读取优化，不持久化。
