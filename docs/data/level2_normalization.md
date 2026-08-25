# Level-2 归一化契约

- **状态**：正式 owner
- **适用范围**：Level-2 trade source 路由、raw 字段映射、交易所时间转换、broker 通道
  顺序、processed 字段和 symbol slice index。
- **Phase owner**：[`docs/data/market_phase.md`](market_phase.md)

## Source 路由

| raw object | processed dataset | exchange |
|---|---|---|
| `SH_Stock_OrderTrade` | `sh_trade` | 上海 |
| `SZ_Trade` | `sz_trade` | 深圳 |

`sh_trade` 只包含上海数据，`sz_trade` 只包含深圳数据。交易所由 processed dataset
身份携带，不写入每一行。其他 raw object 与 processed dataset 路由组合必须在解析
batch 前以 `ValueError` 拒绝；输入为空不改变该规则。

每个合法 `(raw object, processed dataset)` 由一个 `Level2TradeSpec` 同时绑定 exchange
与 source field mapping，不再经过独立的 `exchange/kind` registry。当前正式范围只有
trade，不定义或预留 order parser。

`SZ_Order` 当前 `outputs=[]`，只保存 raw 对象。它的 `OrderTime` 是深交所
`ExchangeTime`，精度为毫秒，但不因此产生 processed order 对象，也不为 `SZ_Trade`
的 normalize 或发布提供完整性证明。

## ExchangeTime 与 ts_utc

两个正式 trade 路由都使用 raw `TickTime` 作为 `ExchangeTime`。raw CSV reader 提供
string 列，正式规范化关系只有：

```python
if exchange == "sh":
    exchange_time_ms = str(tick_time).zfill(8) + "0"
else:
    exchange_time_ms = str(tick_time).zfill(9)
```

- SH `TickTime` 是 `HHMMSScc`，`cc` 为百分秒；输入必须是 1–8 位十进制数字。
- SZ `TickTime` 是 `HHMMSSsss`，`sss` 为毫秒；输入必须是 1–9 位十进制数字。
- 变换后的 `exchange_time_ms` 必须是合法 `HHMMSSsss`：小时 `00–23`，分钟与秒
  `00–59`。
- string 类型不正确时抛出 `TypeError`；缺失字段、null、非数字、超长或非法钟表时间
  抛出 `ValueError`。整个 batch 失败，不跳过该行。

`ts_utc` 是 `int64` UTC epoch microseconds，只按下式产生：

```text
ts_utc = Asia/Shanghai(partition trade_date + exchange_time_ms) -> UTC epoch us
```

不得从 `TradeTime` 的时间部分、`LocalTimeStamp`、接收时间或其他字段补值，也不保留
旧时间解析 fallback。`DateTimeUtils` 拥有 system date 和标量时区转换；`level2`
normalize 拥有上述 source-native 向量化映射。

## TradeTime 的唯一作用

Normalize public 边界先要求 partition `trade_date` 是严格合法的 `YYYY-MM-DD`。
每个非空 raw batch 还必须有 string `TradeTime` 且不得含 null；每个值的前 10 个字符
必须等于 partition `trade_date`。不一致时在 normalize 侧以 `ValueError` 拒绝整个
batch。

`TradeTime` 第 11 个字符起不参与任何 processed 字段，也不校验其时间语法；这部分
既不是 `ts_utc` 来源，也不是 `TickTime` 失败时的 fallback。

`LocalTimeStamp` 保持 raw-only。当前没有它与统一接口 `ServerTime` 的正式映射。

## Raw 到 processed 字段关系

| processed 字段 | Arrow 类型 | SH source / 规则 | SZ source / 规则 | 精确语义 |
|---|---|---|---|---|
| `symbol` | `string` | `SecurityID` | `SecurityID` | source 值原样保留；不追加交易所后缀，交易所由 dataset 身份提供 |
| `ts_utc` | `int64` | `trade_date + TickTime(HHMMSScc)` | `trade_date + TickTime(HHMMSSsss)` | UTC epoch microseconds；只按上一节产生 |
| `event` | `string` | `TickType: T -> TRADE` | `ExecType: 1 -> TRADE, 2 -> CANCEL` | 正成交过滤后，持久化值恒为 `TRADE` |
| `order_id` | `int64` | `SubSeq` | `SubSeq` | 保留的既有 processed 字段；值与 `sub_seq` 相同，不是订单身份、事件主键或 join key |
| `main_seq` | `int64` | `MainSeq` | `MainSeq` | broker 通道身份；只参与通道内接收顺序表达 |
| `sub_seq` | `int64` | `SubSeq` | `SubSeq` | broker 在同一 `MainSeq` 内的接收序号；只参与排序 |
| `side` | nullable `string` | `Side: 1 -> B, 2 -> S`，其他为 null | 无 source，恒为 null | source 映射值；不是 tick-rule 方向，也不补值 |
| `price` | `float64` | `Price` | `TradePrice` | source 成交价格的数值转换，不缩放 |
| `volume` | `int64` | `Volume` | `TradeVolume` | source 成交数量的数值转换，不做手数或证券类型单位换算 |
| `buy_no` | `int64` | `BuyNo` | `BuyNo` | source 买方委托序号；不声明全局唯一性 |
| `sell_no` | `int64` | `SellNo` | `SellNo` | source 卖方委托序号；不声明全局唯一性 |
| `security_type` | `string` | `SecurityID + SH` 规则 | `SecurityID + SZ` 规则 | 本文件下节定义的证券分类 |
| `phase` | `int8` | market phase resolver | market phase resolver | 成交机制编码，由 phase owner 定义 |
| `notional` | `float64` | `float64(price) * float64(volume)` | 同 SH | source 数值尺度下的算术乘积；不额外声明跨证券类型的统一物理单位 |
| `trade_side` | `int8` | tick rule | tick rule | 每个 symbol 内上一条保留成交的价格方向：首条/不变 `0`、上涨 `1`、下跌 `-1`；不是主动买卖方 |

最终 processed 列顺序固定为：

```text
symbol, ts_utc, event, order_id, main_seq, sub_seq, side, price, volume,
buy_no, sell_no, security_type, phase, notional, trade_side
```

现有 processed 字段全部保留，并增加明确表达 broker 通道顺序的 `main_seq` 与
`sub_seq`。SH 的 `ExchangeID`、`TradeMoney`、`TradeBSFlag`、`MDSecurityStat`、
`LocalTimeStamp`，以及 SZ 的 `ExchangeID`、`LocalTimeStamp` 当前不映射到 processed。
没有消费者需求时不为这些 raw 字段增加别名或占位列。

## Trade batch 与日对象

`parse_level2_trade_batch(...)` 接收显式 `trade_date`，一次完成字段校验、交易所时间转换、
source value mapping、统一 schema 转换和正成交过滤。输出只保留
`event == "TRADE"`、`price > 0` 且 `volume > 0` 的行。

日对象固定按以下关系构造：

```text
raw batch parse/filter
-> security_type
-> concatenate all batches
-> phase
-> sort(symbol, ts_utc, main_seq, sub_seq)
-> symbol slices
-> per-symbol notional/trade_side
```

`ts_utc` 是事件时间主序。相同 `symbol` 与 `ts_utc` 下，`main_seq`、`sub_seq` 只提供
确定性排列：同一 `MainSeq` 内较小 `SubSeq` 表示较早的 broker 接收顺序；不同
`MainSeq` 的数值顺序不表示跨通道因果关系或全局接收顺序。Normalize 不按这些字段或
`order_id`、`buy_no`、`sell_no` 去重；通过正成交过滤的 raw 行保持其多重性。

证券代码段和生效日期/成交时段分别由 `level2_security` 与 `level2_phase` 表达，因为
两者是独立变化的规则集合；它们不构成可配置 profile 或运行时 registry。

## SecurityID 与 security_type

`SecurityID` 必须能够无损转换为 `0..999999` 的整数；null、负数、超出范围或无法转换
时失败。下列闭区间精确决定持久化的 `security_type`；表中使用六位数字显示边界：

| exchange | security_type | SecurityID 闭区间 |
|---|---|---|
| SH | `stock` | `600000-600999`, `601000-601999`, `603000-603999`, `605000-605999`, `688000-688999` |
| SH | `cdr` | `689000-689999` |
| SH | `b_share` | `900000-900999` |
| SH | `fund` | `500000-500999`, `501000-501999`, `502000-502999`, `505800-505899`, `506000-506099`, `508000-508999`, `519000-519999`, `550000-550999` |
| SH | `etf` | `510000-510999`, `511000-511999`, `512000-512999`, `513000-513999`, `515000-515999`, `516000-516999`, `517000-517999`, `518000-518999`, `520000-520999`, `526000-526999`, `530000-530999`, `551000-551999`, `560000-560999`, `561000-561999`, `562000-562999`, `563000-563999`, `588000-588999`, `589000-589999` |
| SH | `convertible_bond` | `100000-100899`, `110000-110999`, `111000-111499`, `113000-113999`, `118000-118499`, `126000-126999`, `132000-132999`, `137000-137499` |
| SH | `bond_repo` | `201000-207999` |
| SH | `bond` | `009000-010999`, `018000-020999`, `101000-101999`, `109000-109999`, `112000-112999`, `114000-115999`, `120000-125999`, `127000-131999`, `135000-136999`, `137500-137999`, `138500-138999`, `139000-140999`, `142000-143999`, `145000-145999`, `149000-152999`, `155000-157999`, `159000-160999`, `162000-169999`, `171000-171999`, `173000-173999`, `175000-180999`, `182300-182999`, `183000-186999`, `188000-189999`, `194000-194999`, `196000-199999`, `230000-238999`, `240000-247999`, `250000-254999`, `258000-259999`, `260000-266999`, `270000-272999`, `280000-283999` |
| SZ | `stock` | `000000-000999`, `001200-001999`, `002000-004999`, `300000-309799` |
| SZ | `cdr` | `001001-001199`, `309800-309999` |
| SZ | `b_share` | `200000-209999` |
| SZ | `fund` | `119400-119499`, `121500-121999`, `150000-151999`, `160000-179999`, `180101-180999`, `184000-184999` |
| SZ | `etf` | `158000-159999` |
| SZ | `convertible_bond` | `115000-115099`, `115600-115999`, `117000-117499`, `120000-120999`, `121000-121499`, `123000-123999`, `124000-124999`, `127000-127999`, `128000-128999` |
| SZ | `bond_repo` | `131800-132099` |
| SZ | `bond` | `100000-114999`, `115100-115599`, `116000-116999`, `117500-117999`, `118000-118999`, `119000-119399`, `119500-119999`, `130000-130999`, `133000-139999`, `143000-149999`, `189500-189999`, `190000-199999`, `500000-529999`, `560000-599999` |

SH 的 `133000-134999`, `141000-141999`, `144000-144999`, `153000-154999`,
`158000-158999`, `161000-161999`, `170000-170999`, `172000-172999`,
`174000-174999`, `181000-181999`, `182000-182299`, `187000-187999`,
`190000-193999`, `195000-195999`, `330000-330999`, `360000-360999`,
`580000-580999`, `582000-582999`, `700000-899999`，以及 SZ 的
`030000-039999`, `070000-084999`, `220000-299999`, `350000-399999`,
`970000-989999` 是明确不支持的代码段；命中时失败。未落入支持或明确不支持区间的值也
必须失败，不得猜测 security type。

## Symbol slice index

Level-2 Normalize 必须把输出按 `(symbol, ts_utc, main_seq, sub_seq)` 升序排序。
`main_seq` 与 `sub_seq` 进入 processed schema，但不进入 Meta identity。

每个 symbol 在输出中必须只占一个非空半开区间 `[start, end)`。边界必须是整数且不得
是布尔值，并满足 `0 <= start < end`。按 `start` 排序后，第一个区间必须从 `0` 开始，
相邻区间必须首尾相接，最后一个 `end` 必须等于 Parquet 总行数。由此每行恰好属于一个
symbol，且同一 symbol 不得出现在区间之外。Normalize producer 负责保证区间中的行确实
具有该 symbol；consumer 不重复扫描 symbol 列验证。

Meta 中的 `symbol_slices` 是以裸 `symbol` 为 key 的 object；每个 value 精确包含
`start` 和 `end`。`symbol` 是跨 `sh_trade` 与 `sz_trade` 的全局身份；两个数据集出现
相同 symbol 时 Access 必须失败，不得覆盖或拼接。Meta 不保存 output path、row-group
位置或 index header。

每个 symbol slice 独立产生 `notional = float64(price) * float64(volume)` 和 tick-rule
`trade_side`：该 slice 首行为 `0`，价格上涨为 `1`、下跌为 `-1`、不变为 `0`。enrichment
不得改变行数或行顺序，index 建立后的其他 Normalize 变换也必须保持行数和行顺序。
Access 在加载 Meta 后以 Parquet 总行数校验完整覆盖；row-group overlap 只属于 Access
的运行时读取优化，不持久化。

## 身份、错误与日志归属

- raw object、processed dataset 和 exchange 的组合身份由 Normalize 路由拥有。
- `symbol` 是 symbol slice 的身份。当前没有 Level-2 成交事件的正式唯一键；
  `main_seq` 与 `sub_seq` 只表达 broker 通道内接收顺序，不能证明一个交易日的 raw
  完整性，也不是数据集级事件身份或 join key。`order_id`、`buy_no`、`sell_no` 同样不是。
- Normalize producer 拥有 raw 必要字段、字段类型、日期一致性、交易所时间、数值转换、
  security type、phase、排序、行数保持和 slice 生成错误。缺少必要字段、非法类型、非法
  时间或无法进行数值转换时失败整个输入，不修补、不去重、不降级；不满足明确正成交
  过滤谓词的行只被排除，不另定义为错误。
- Normalize 不检查每个 `MainSeq` 是否从 `SubSeq=1` 开始、是否连续、是否存在完整文件尾，
  也不联合扫描 `SZ_Order` 与 `SZ_Trade`；这些观察不构成 processed 发布门槛。
- Access 只拥有已提交 processed payload、Meta、symbol slice 完整覆盖和跨 dataset
  symbol 冲突错误。Access 不检查通道序号完整性或成交字段唯一性。
- `FactMaterializeStep` 拥有 raw/processed Meta hit、聚合复用结果与 processed publish
  日志；Level-2 Normalize 以 `▶️ Level-2 normalize` 表示开始，以
  `⏳ Level-2 normalize` 表示仍在流式读取解析，
  以 `✅ Level-2 normalize` 表示成功完成，并以相同词汇记录内部排序和 symbol index。
  流式读取解析阶段每 30 秒至多记录一条 `INFO` 心跳；该心跳只在一个 source batch 完成
  解析后产生，并包含 stage、target、trade date 与累计 elapsed seconds，不记录 source
  batch 数、raw 行数或过滤后保留成交行数。Access 不记录日志。

## 当前不定义的关系

- `LocalTimeStamp -> ServerTime` 没有定义。
- Level-2 成交事件唯一键没有定义。
- 文件末尾之后是否缺失事件、整个 `MainSeq` 是否缺失均无法从现有 raw 文件观察；当前
  不定义 Level-2 日级完整性证明。
- `volume` 和 `notional` 跨 stock、fund、ETF、bond 等证券类型的统一物理单位没有定义；
  Normalize 只保留 source 数值和明确的算术关系。
- Tushare `open/high/low/close/vol/amount` 不从本 processed trade 表推导；两者样本对账结果
  不能自行成为长期聚合契约。
- 当前 raw trade 对象不声明覆盖盘后固定价格交易；该边界由 phase owner 说明。
