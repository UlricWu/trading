# 市场 Phase 契约

- **状态**：正式 owner
- **适用范围**：market phase 词汇、Level-2 正成交事实的 phase 归属与当前数据源边界。

## 身份与归属

`phase` 表示一条日内市场事实所属的成交机制，稳定编码为：

```text
AUCTION     = 0
BREAK       = 1
CONTINUOUS  = 2
FIXED_PRICE = 3
```

`AUCTION`、`CONTINUOUS`、`FIXED_PRICE` 分别表示集合竞价、连续竞价和盘后固定价格
成交机制。`BREAK` 只表示没有活跃成交机制，且只有 source fact 契约明确需要时才能产生。

`phase` 属于日内事实，不是日线 feature、训练样本资格、仿真执行许可或通用
`MarketDataView` 属性。日线数据不得构造 `phase`；训练与日线回放不得借
`phase` 过滤或授权。

当前正式 Level-2 producer 只接收 `SH_Stock_OrderTrade -> sh_trade` 和
`SZ_Trade -> sz_trade` 的正成交事实。resolver 必须为每一行产生
`AUCTION` 或 `CONTINUOUS`；证券类型无规则或成交时间不在已定义窗口时必须失败，
不得以 `BREAK` 接受。当前 producer 不产生 `FIXED_PRICE`。

## Level-2 正成交窗口

下列时间均为 `Asia/Shanghai` 本地 wall-clock。`[a, b)` 为左闭右开；
`[a, b]` 为两端闭合。

| exchange / security_type | 生效日期 | `AUCTION` | `CONTINUOUS` |
| --- | --- | --- | --- |
| SH stock / cdr | 2018-08-20 起 | `[09:25:00, 09:25:02]`；`[15:00:00, 15:00:03)` | `[09:30:00, 11:30:00]`；`[13:00:00, 14:57:00)` |
| SH b_share | 全部已支持日期 | `[09:25:00, 09:25:01]`；`[15:00:00, 15:00:02)` | `[09:30:00, 11:30:00]`；`[13:00:00, 14:57:00)` |
| SH fund / etf | 至 2026-07-05 | `[09:25:00, 09:25:02]` | `[09:30:00, 11:30:00]`；`[13:00:00, 15:00:00)` |
| SH fund / etf | 2026-07-06 起 | `[09:25:00, 09:25:02]`；`[15:00:00, 15:00:03)` | `[09:30:00, 11:30:00]`；`[13:00:00, 14:57:00)` |
| SH bond / convertible_bond / bond_repo | 全部已支持日期 | `[09:25:00, 09:25:01)` | `[09:30:00, 11:30:00]`；`[13:00:00, 15:30:00)` |
| SZ stock / fund / etf / bond / b_share | 全部已支持日期 | `[09:25:00, 09:25:01)`；`[15:00:00, 15:00:01)` | `[09:30:00, 11:30:00]`；`[13:00:00, 14:57:00)` |
| SZ convertible_bond | 至 2020-06-07 | `[09:25:00, 09:25:01)`；`[15:00:00, 15:00:01)` | `[09:30:00, 11:30:00]`；`[13:00:00, 14:57:00)` |
| SZ convertible_bond | 2020-06-08 起 | `[09:25:00, 09:25:01)`；`[14:57:00, 14:57:01)`；`[15:00:00, 15:00:01)` | `[09:30:00, 11:30:00]`；`[13:00:00, 14:57:00)` |

SZ `cdr` 与 `bond_repo` 没有已定义的 trade phase，不得继承其他证券类型规则。

SH stock、cdr、fund 与 etf 的开盘右端点只接纳 source-native
`TickTime=09:25:02.00`。SH `TickTime` 精度为百分之一秒，因此
`09:25:02.01` 及更晚时间不属于该开盘集合竞价窗口。

该右端点的全量证据来自 2026-08-23 对
`raw/level2_ftp/sh_stock_ordertrade/trade_date=2025-11-18/SH_Stock_OrderTrade.csv.7z`
的复核；输入文件大小为 1,959,275,828 bytes，SHA-256 为
`2ce4e66da582dd4f9b66216726e694a4d8230b22f908d5584d72b38b7836e951`。按正式
trade 解析与过滤规则扫描 170,657,244 条 raw，保留 59,423,448 条正成交。旧半开
窗口未匹配的 22,702 条全部且仅为 `TickTime=09:25:02.00`，其中 stock 20,000 条、
etf 2,668 条、fund 34 条；闭合该精确端点后，59,423,448 条正成交全部获得 phase，
上述 22,702 条均归入 `AUCTION`，没有观察到其他越界时间。该证据不授权接纳
`09:25:02.01` 至 `09:25:02.99`。

SH b_share 的开盘右端点只接纳 source-native `TickTime=09:25:01.00`；
`09:25:01.01` 及更晚时间不属于该开盘集合竞价窗口。该右端点的全量证据来自
2026-08-23 对
`raw/level2_ftp/sh_stock_ordertrade/trade_date=2025-12-03/SH_Stock_OrderTrade.csv.7z`
的复核；输入文件大小为 1,703,443,547 bytes，SHA-256 为
`94ea22e645dd0b7016b6e0d0d529461b271e99c71790e3892b890c9e76e3bee2`。按正式
trade 解析与过滤规则扫描 148,039,119 条 raw，保留 49,182,347 条正成交。旧半开
窗口未匹配的 19 条全部且仅为 b_share `TickTime=09:25:01.00`，覆盖 10 个 symbol；
闭合该精确端点后，49,182,347 条正成交全部获得 phase，上述 19 条均归入
`AUCTION`，没有观察到其他越界时间。

上述 15:00 后的 `AUCTION` 尾端只容纳 source-native 收盘成交时间标记，不表示法定
收盘集合竞价延长至 15:00 之后。`SH_Stock_OrderTrade` 的上海 B 股收盘成交可能统一
标记为 15:00:01.x，因此其右开边界为 15:00:02。

深市可转债盘中临时停牌时间跨越 14:57 时，于 14:57 复牌并对已接受申报进行
复牌集合匹配，再进入收盘集合匹配。因此 `SZ_Trade` 中自 2020-06-08 起落在
`[14:57:00, 14:57:01)` 的可转债正成交属于 `AUCTION`；该 source-native 窗口只
容纳实际出现的复牌集合匹配成交，不表示每只可转债每日都在 14:57 成交。规则来源：
[深交所 2020 年通知](https://www.szse.cn/disclosure/notice/t20200522_577540.html)、
[现行可转债交易实施细则](https://docs.static.szse.cn/www/lawrules/rule/bond/W020250327514604502531.pdf)。

## 盘后固定价格交易

自 2026-07-06 起，上交所和深交所盘后固定价格交易覆盖 A 股和 ETF，制度时间为
15:05–15:30；上交所基金收盘阶段同时改为收盘集合竞价。规则来源：

- [上交所规则通知](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml)
- [深交所现行交易规则](https://www.szse.cn/lawrules/rule/trade/current/t20260424_620190.html)

当前 `SH_Stock_OrderTrade` 与 `SZ_Trade` raw object 都不包含可识别的盘后固定价格
正成交渠道。2026-07-27 全量样本中，前者最后一笔正成交为 15:00:01.71，后者为
15:00:00；15:05–15:30 均无正成交。因此不得从状态行、接收时间或普通成交时间推导
`FIXED_PRICE`。

只有在新的 raw object、字段 schema、合并与 lineage 归属、以及 source-native
15:30 微秒边界全部定义后，producer 才能产生 `FIXED_PRICE`。
