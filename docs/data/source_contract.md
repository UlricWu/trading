# 数据源与核心日线数据契约

- **状态**：正式 owner
- **适用范围**：`data.brokers`、`data.sources`、source identity、Tushare active
  manifest、通用 source-native 查询、Tushare 通用单位标准化、`trade_calendar`、`daily_bar` 与公告核验证券状态的正式语义。
  分红及公告的专项语义由下列独立 owner 定义。
- **工作流 owner**：[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md)
- **存储 owner**：[`docs/data/storage_layout.md`](storage_layout.md)

## Source identity

`source_name` 是本地 raw 分区身份；`raw_object` 是 broker 识别的源端对象；`outputs`
是该 raw source 直接产生的 processed dataset 名；`outputs=[]` 表示 raw-only。

Tushare 结构化 source 与 Level-2 文件 source 使用不同的选择权威：

- `TushareBroker._TUSHARE_SOURCES` 是 Standard 当前正式启用 source 的唯一 manifest。
  mapping key 同时固定为 `source_name`、`raw_object` 和单一同名 processed output，value
  是 Tushare API 名；存在于 mapping 表示启用，不存在表示当前 workflow 不执行。
- `data.sources` 只声明 Level-2 文件 source。每项必须显式提供 `enabled`、
  `broker=level2_ftp`、`group=offline_level2`、非空 `raw_object` 和完整 `outputs`；
  `enabled=false` 的文件 source 不参与执行。

配置不得声明 Tushare source，也不提供 `use_broker_sources`、group 总开关或其他隐式展开
机制。增加 Tushare capability 不得自动改变正式执行集合；改变 manifest 本身才改变 Standard
source 集。所有能够通过校验的 `data.sources` 都属于 Level-2，不存在被 workflow 静默忽略
的其他 group 或 broker 条目。

Feature 与 label 配置不使用 source group；其固定身份与字段语义由
[`docs/data/daily_feature_label_contract.md`](daily_feature_label_contract.md) 所有，执行编排
由 [`docs/offline_workflow_contract.md`](../offline_workflow_contract.md) 所有。

Broker implementation 的选择与绑定由
[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md) 所有。Broker name
保留配置引用和 source identity 语义。运行时不得建立可变 register/freeze registry。
仅在需要来源 I/O 时构造 broker adapter，并在同一 workflow 内按 broker 复用。

Tushare 单日下载能力的调用形式为：

```python
payload_path: Path | None = broker.fetch(
    source_name=source_name,
    raw_object=raw_object,
    trade_date=trade_date,
    pm=path_manager,
)
```

`source_name`、`raw_object` 和交易日由调用方提供；broker identity 由具体 Broker 的
`name` 提供，source-native payload basename 由 Broker 确定。成功时直接返回已经写完的
正式 raw payload 路径；返回路径不表示 Meta 已提交，raw Meta 仍由 Step 提交。调用方不再
传递或接收 `DownloadPlan`，也不根据下载结果重新拼装 raw 路径。`None` 只表达本 owner
定义的源端无数据，不表达请求、文件写入或其他执行失败。

Broker 到 normalize callable 的关系固定为：

```text
tushare    -> normalize_tushare
level2_ftp -> normalize_level2
```

该关系不是 broker 配置中的 profile/version 选择器。所有 processed 输出固定写入 `v1`；
broker config 不声明 `normalize_profile`。

## Tushare active manifest

本地 broker `tushare` 的正式 active manifest 固定为：

| source_name / raw_object / output | Tushare API |
|---|---|
| `trade_calendar` | `trade_cal` |
| `daily_bar` | `daily` |
| `adj_factor` | `adj_factor` |
| `daily_basic` | `daily_basic` |
| `stock_basic` | `bak_basic` |
| `stock_st` | `stock_st` |
| `stk_limit` | `stk_limit` |
| `suspend_d` | `suspend_d` |
| `dividend` | `dividend` |
| `cyq_perf` | `cyq_perf` |
| `margin` | `margin` |
| `margin_detail` | `margin_detail` |
| `moneyflow` | `moneyflow` |
| `top_list` | `top_list` |

本地 `stock_basic` 是历史日股票列表，源端固定查询支持 `trade_date` 的 `bak_basic`；其
response 记录集合定义该交易日的历史股票成员集合，不得使用当前股票基础信息快照接口
`stock_basic` 代替。Standard 与 Level-2 universe 如何把该集合与各自行情可用集合组合，
由 Access owner 定义。

`processed/stock_basic/v1.list_date` 把能够按 Tushare 紧凑日期格式解析的值转换为
`YYYY-MM-DD`，其他值转换为 null。Tushare response 的记录集合是该对象的权威；本系统不
解释无法解析值的业务含义，也不要求它与 `daily_bar` 或其他 source 具有相同记录覆盖。
当 response 为零行时，即使 Tushare DataFrame 没有携带列，normalize 也必须构造包含空
`symbol` 与 `list_date` 列的零行 processed 对象。该转换只建立可消费 schema，不增加任何
股票记录；`stock_basic@2019-04-01` 是该规则的正式案例，不得以前后日期填充。

2026-09-23按用户授权完成历史对象维护：研究涉及的213个日期中，164份旧 `stock_basic`
使用了缺少交易日期的基础信息快照，现已重取对应日期的 `bak_basic` 并替换raw及processed。
另49日及全部 `stock_st`、`suspend_d` 未变。此维护不改变上述源记录集合契约；新源仍有
预上市成员的空 `list_date` 和退市整理期覆盖差异，不能解释为交易所完整上市状态。
详见[修复、失败检查与备份证据](../../research/subscription-training/README.md#stock-basic-repair-2026-09-23)。

2026-09-24按用户授权重取 `suspend_d@2026-06-11`，正式 raw 与 processed 均由7行更新为
19行：新增12条S停牌记录（含 `300411.SZ`），原有4条S及3条R逐字段保留；Meta与直接raw
lineage已更新并读回验证。实际采集时间为2026-09-24 11:39:13 +08:00，不表示历史盘前可用。
旧快照、精确采集时间、内容摘要和校验结果见
[维护记录](/home/wsw/app/maintenance-evidence/suspend-d-2026-06-11-repair-2026-09-24-hry43yg_/README.md)。

### 公告核验的证券状态

本节唯一拥有 `security_status/v1` 的数据语义；它补充历史名单、上市日期及停复牌来源
不能确定的证券状态，不覆盖或改写原始 `bak_basic`、`suspend_d`。上市状态与交易状态
独立：上市不等于当日可成交，停牌也不表示退市。回放消费及沿价估值由
[离线回放 owner](../offline_workflow_contract.md#真实行情到回放输入)定义。

状态核验必须指定证券、适用交易日、已确认的上市/交易状态、证据公开时间、原文 URL
和判断依据，并保留所补充的同日名单及停复牌来源位置。当前对象表达完整账户日状态，证据必须
在该日零时以前可见；盘中披露只能支持后续完整账户日，不能反向改写当天时段。
状态按证据能够证明的适用日期生成；
未来才披露的上市日、复牌日或更正不得提前影响历史成员及成交。只有日期的公告沿用
下一自然日零时才可见的保守约定；采集时间独立保存，不能充当历史公开时间。
确认待上市不要求知道未来上市日；发行阶段证据必须来自当时，空 list_date、缺行情或
后来上市本身都不能替代该证据。确认全天停牌需要对应 A 股证券的公告及期间覆盖，
不能将同一发行人的 H 股复牌套用到 A 股。证据不能消除冲突时继续保留未知。

原始核验输入 `SecurityStatusReviews` 固定包含 `version=v1`、`documents`、`reviews`。
其补充对象固定为同日 `stock_basic/v1`、`suspend_d/v1`，不另设内容指纹字段。
documents 保存公告身份、证券代码、标题、公开日期、URL 和采集时间。
每条 review 包含 `ts_code`、`trade_date`、可空的 `listing_status`
（pre_listing/listed/delisted）、`trading_status`（suspended/trading）、`available_ts_us`、
`evidence_refs` 和 `reason`。两种状态至少确认一种；交易状态必须同时确认 listed。
每个引用必须对应同一证券的原文 URL，原生公告身份与 URL 各自唯一，同一证券日唯一。

`ResolveSecurityStatusStep` 确认原文存在及原始输入 Meta 后发布三列 string：`symbol`、
`trade_date`、`resolution`；resolution 是上述 review 的 JSON。Parquet schema metadata
`security_status_inputs` 保存 stock_basic、suspend_d、reviews 三个对象相对于存储根的路径，
直接 upstream 指向核验输入的 Meta。生产者重新构造结果，表内容和来源路径相同时复用
payload，必要时更新直接 upstream 的 Meta 绑定；没有显式核验输入时不创建“已核实”对象。

核验结果由 data-system 发布为正式 V1 对象，Access 负责读取、schema、身份、来源路径
及所引用对象的 Meta 校验；回放仍只使用真实行情生成执行观察。同一明确引用按可信输入
使用，不将文件内容变化作为需要额外检测的失效模式。摘要不能证明证券身份、停复牌状态
或历史可见性；这些保证仍由原文关联、适用日期和公开时间规则建立。历史摘要与冻结材料
保留审计，新生产和消费不依赖摘要。原始来源冲突已经被充分权威证据解释时，仍保留冲突与处置依据，不把已确认
状态继续冒充未解决，也不删除来源行制造一致性。

除 `trade_calendar` 与 `dividend` 外，当前 Tushare source 按单日参数
`trade_date=YYYYMMDD` 查询。`trade_calendar` 固定查询 SSE，并以自然年作为唯一请求与对象
分区：

```text
trade_cal(exchange="SSE", start_date=YYYY0101, end_date=YYYY1231)
```

每个缺失自然年最多请求一次。全年 response 原样保存为该年的 raw Parquet；其他 source
response 仍按单日保存。Broker 不改变正式 processed 字段。

## 分红送转与公告专项 owner

- [分红送转与公司行动数据契约](dividend_contract.md)：拥有 `dividend/v1` 的查询、字段与
  标准化，以及 `corporate_actions/v1` 的候选关联、已确认修正和处置语义。
- [公告采集、披露事实与核验契约](announcement_contract.md)：拥有公开索引、原文证据、
  `announcement_facts/v1`、公开时点事件视图及登记日公告调查；普通分红与公告事实分别建模。

## Level-2 source identity

| source_name | raw_object | outputs |
|---|---|---|
| `sh_stock_ordertrade` | `SH_Stock_OrderTrade` | `["sh_trade"]` |
| `sz_order` | `SZ_Order` | `[]` |
| `sz_trade` | `SZ_Trade` | `["sz_trade"]` |

`sh_trade` 与 `sz_trade` 的字段和 index 由
[`docs/data/level2_normalization.md`](level2_normalization.md) 所有。

<a id="level2-delivery-contents"></a>

### 五类交付文件的内容与当前消费范围

本节说明交付文件的内容和当前正式消费范围；原始字段观测不新增 source、processed
dataset 或标准化映射。`Index`、`Level2Market` 尚未列入上表的正式 source。
建模用途及待验证方向由
[subscription-training](../../research/subscription-training/README.md#level2-delivery-modeling) 记录。

| 交付压缩包 | 内容与记录粒度 | 主要字段 | 当前正式消费范围 |
|---|---|---|---|
| `Index.csv.7z` | 指数行情快照；本批包含沪、深、北市场的指数代码记录 | `SecurityID/ExchangeID`、`TradeDate/TradeTime`；`PreClosePrice/OpenPrice/HighestPrice/LowestPrice/LastPrice/ClosePrice`；`TotalVolumeTrade/TotalValueTrade/NumTrades`、`LocalTimeStamp` | 没有现行正式 source、指数快照 parser 或 processed 输出 |
| `Level2Market.csv.7z` | 证券盘口快照；本批包含股票、ETF、债券等品类及沪、深、北市场记录 | `BidPrice1..10/BidVolume1..10`、`AskPrice1..10/AskVolume1..10`；成交价量、委买/委卖总量及加权均价、涨跌停价、委托笔数、`IOPV`、`Withdraw*`、时间字段 | 没有现行正式 source、盘口快照 parser 或 processed 输出 |
| `SH_Stock_OrderTrade.csv.7z` | 上海合并逐笔事件流，含状态、委托、撤单和成交；文件名中的 Stock 不保证只含股票 | `TickTime/TickType`、`BuyNo/SellNo`、`Price/Volume/TradeMoney`、`Side/TradeBSFlag/MDSecurityStat`、`MainSeq/SubSeq`、`TradeTime/LocalTimeStamp` | `sh_stock_ordertrade -> sh_trade/v1`，只消费现行正成交；其他事件没有正式 processed 对象 |
| `SZ_Trade.csv.7z` | 深圳成交及撤单逐笔事件流，不能将每一行都当作成交 | `TickTime`、`TradePrice/TradeVolume`、`ExecType`、`BuyNo/SellNo`、`MainSeq/SubSeq`、`TradeTime/LocalTimeStamp` | `sz_trade -> sz_trade/v1`，按现行事件映射及正成交条件消费，撤单不进入 processed trade |
| `SZ_Order.csv.7z` | 深圳逐笔委托事件，带订单编号、方向及委托类型 | `OrderTime`、`Price/Volume`、`Side/OrderType`、`OrderNO/OrderStatus`、`MainSeq/SubSeq`、`TradeTime/LocalTimeStamp` | `sz_order` 的 `outputs=[]`；按现行契约在网盘保留，日常 workflow 不下载、不解压，没有 processed order 对象 |

`Level2Market` 描述快照时刻的价格与挂单量；逐笔文件描述单次事件的价格与数量。
快照中的 `TotalVolumeTrade/TotalValueTrade/NumTrades` 是累计统计，不能跨快照直接相加；
`TotalBidVolume/TotalAskVolume` 是快照委托总量，不能解释为累计成交量或资金流。
完整订单队列、逐单撤补及事件顺序不能仅由间隔快照恢复。

成交编码、CSV 十进制价格与数量尺度、`TradeMoney` 与正式 `notional` 的关系、时间转换、
源端方向与 tick-rule 的区别、序号及订单字段的限制均沿用
[归一化 owner](level2_normalization.md)，不在本节另定义。该 owner 当前只定义 trade：
不能直接把 snapshot 的 `TradeTime` 接入现行 `TickTime` parser，也不能凭实时接口的
整数价格字典给这些十进制 CSV 再除以 10000。

#### 2026-09-22 单日文件核查（2026-10-03）

输入目录为 `/home/wsw/Downloads/4564934805_铁皮卡lh/2026-09-22-New/`；核对时仓库
HEAD 为 `0ab4b31d75741a91410e842cb7f7c89258ef24ca`。每包只有一个同名 CSV 成员。
`Index` 和 `Level2Market` 已用 `7zz x -so`（7zz 23.01）与 Python 3.13.10、PyArrow 23.0.0
分批完整读取，解码退出码为 0、没有 stderr，CRC 校验通过；其全部 `TradeDate` 均为
`2026-09-22`。另外三个文件用同一解码器及 Python `csv` 读取表头和开头连续 100,000 行，
所读 `TradeTime` 日期前缀均为 `2026-09-22`；样本偏向盘前与早盘，不代表全日事件比例，
未做这三包的全量 CSV 或 CRC 校验。

所有 SHA-256 均对整个压缩包计算；文件在各自哈希读取期间的尺寸和修改时间保持不变，
两个快照包的哈希与完整读取时一致。哈希标识输入内容，不替代全量质量或交易所覆盖验证，
也不表示已有不可变归档或该日数据已正式入库。

| 压缩包 | CSV 列数 | 压缩字节数 | CSV 成员字节数（7z 目录） | 本次读取范围 |
|---|---:|---:|---:|---|
| `Index.csv.7z` | 14 | 44,121,201 | 450,938,318 | 全量 2,972,913 行 |
| `Level2Market.csv.7z` | 68 | 1,380,330,138 | 18,986,217,006 | 全量 45,502,080 行 |
| `SH_Stock_OrderTrade.csv.7z` | 16 | 2,906,484,946 | 28,532,221,446 | 前 100,000 行 |
| `SZ_Trade.csv.7z` | 12 | 1,530,469,342 | 14,690,893,265 | 前 100,000 行 |
| `SZ_Order.csv.7z` | 13 | 1,536,355,281 | 15,748,264,978 | 前 100,000 行 |

| 压缩包 | SHA-256 |
|---|---|
| `Index.csv.7z` | `e1549b4db5e0c07cf5d6de903089a40e8c3f96f84d921e25d5ffc15f1f7c421a` |
| `Level2Market.csv.7z` | `06aa2453fbd6a2411fe7ac338b66f722f648e8fde5e8a179c29cb50171f7e88f` |
| `SH_Stock_OrderTrade.csv.7z` | `fa59087bdc038a84d490e4b8304e6df11489bf93a1b019200d48ffd27138014a` |
| `SZ_Trade.csv.7z` | `c182f438444d1575d12f897ef9609af0d3dcd062ec892181443895b890f3e778` |
| `SZ_Order.csv.7z` | `918a1c591206e5d898f692563ec1a1b3cda2574e9394bd351561a3e9d2fe2727` |

本次观测的关键限制如下，均仅绑定上述输入：

- `Index` 在 SH/SZ/BJ 分别有 1,201,941/1,762,418/8,554 行，对应 200/375/12 个
  `SecurityID`；这些是交易所内代码数，不能相加后宣称同样数量的不同经济指数。
  `Level2Market` 对应 27,443,278/17,422,783/636,019 行与 25,863/23,819/345 个代码，
  代码数不等于股票资格集合。两包整体记录时间范围都是 09:14 至 15:30，不证明全部证券
  在此区间连续可观测。
- `Index` 的 `000001.SH/000300.SH/399001.SZ/399006.SZ` 及 `Level2Market` 的
  `600000.SH/000001.SZ/510300.SH/159915.SZ` 均观察到相邻 3 秒更新及其他时间间隔，
  不能按固定 3 秒网格补造数据。后两只深圳证券还出现同一 `TradeTime` 多帧；这里只确认
  时间重复，不证明整行重复，也没有执行去重。这里的交易所后缀只用于区分展示代码，
  不改变正式 processed `symbol` 的源值语义。
- `Level2Market` 的 `WithdrawBuyNumber/WithdrawBuyAmount/WithdrawBuyMoney` 及
  `WithdrawSellNumber/WithdrawSellAmount/WithdrawSellMoney` 六列在全量中全部为空，
  不能直接提供撤单统计；这不表示其他逐笔文件没有撤单事件。
  SH 的 `IOPV` 全部为 0，SZ 有 2,651,430 行大于 0，BJ 全部为 0；不能把列存在视为
  对所有证券、市场都有效。盘前和非活跃证券等记录中还存在零价格及空盘口。
- 上海前缀样本的 `TickType` 出现 S/A/D/T，`Side` 还出现 O/T/N，不能跨事件类型统一
  解释成买卖方向；深圳成交前缀的 `ExecType` 为 `4/F`，`4` 即使价格和数量为正也不属于
  正式成交。深圳委托前缀的 `Side=1/2`、`OrderType=2/3`、`OrderStatus` 全空；这些只是
  原始编码观测，不建立正式 order 映射，也不排除文件后续出现其他编码。
- 三个逐笔前缀的 `SubSeq` 全为 0；仍按现行 owner 保留，不能以它作为订单身份或去重键。
  `OrderNO`、`BuyNo/SellNo` 与 `MainSeq` 的存在不证明可唯一关联和完整恢复订单簿。
  `LocalTimeStamp` 不表示策略实际收到消息的时刻，也不因本次核查获得 `ServerTime` 映射。

### 百度网盘交付与按需下载

历史及未来交付全部使用百度网盘，保留既有 `level2_ftp` 本地 broker identity 与路径；
名称不再表示 FTP transport。`outputs=[]` 的 `SZ_Order` 仅在网盘长期保存，不作为日常
入库前置条件，不下载、不解压。两个 trade 输出仍固定为 `v1`，沿用已合并的旧格式和
百度新格式标准化映射、symbol slices 及直接 raw lineage。

`data.brokers.level2_ftp` 显式配置 `baidupcs_go` 可执行文件、`remote_path_templates`
有序候选列表和 `raw_cache_days`（正整数，默认配置 5）。模板只接受 `{month}`
（YYYY-MM）、`{date}`、`{file}`，必须含日期及文件名，展开为绝对路径。当前配置依次为
`/wsw/level2_data/{month}/{date}-New/{file}`、普通日期目录、根下补录日期目录。
首次入库选择第一个存在的文件；迁移既有 raw 则只选择与原记录同尺寸的候选。

`Level2Broker.locate()` 返回网盘来源记录或明确缺失，`describe()` 查询一个确切路径，
`download()` 只准备本地压缩 cache。Meta 发布、processed 复用和缓存生命周期由工作流
及存储 owner 拥有。传输采用[技术栈决策](../engineering/technology_stack_decisions.md)
指定的客户端，认证与传输失败不得转为缺失；不连接 NATS/ClickHouse。
broker 字段编码及单位由[标准化 owner](level2_normalization.md)定义。

<a id="level2-data-status"></a>

### Level-2 缺失情况与异常处理（2026-10-02 更新）

本节记录已发现问题在正式 `/home/wsw/app/data` 中的处理状态，不改变 source、缺失失败、
标准化或数据复用契约，也不表示已重新扫描所有历史日期。

| 日期 | 历史缺失或异常 | 当前正式库状态 | 处理记录 |
|---|---|---|---|
| `2025-11-25` | `level2_ftp/sz_trade` 的 `SZ_Trade` 源端不可用；沪深逐笔与分钟缺失 | **已补齐**：本地新交付文件已入库，raw、沪深标准化逐笔与分钟可用，正式路径读回校验通过 | [补录记录](../../research/subscription-training/README.md#level2-gap-2025-11-25) |
| `2025-11-24/25` | 受 `11-25` 分钟缺失影响，H03 Feature/Label 无分区 | **已补齐**：两日 Feature/Label 已生成；各8个逐证券 null Label 符合现行规则，不属于整日分区缺失 | [补录及校验](../../research/subscription-training/README.md#level2-gap-2025-11-25) |
| `2026-06-08/09` | 旧 `06-09` 三个原始 CSV 除日期外与 `06-08` 全文相同，造成跨日重复 | **已修复**：按新交付数据重建逐笔、分钟及受影响 Feature/Label；原重复窗口已不同，`06-08` 零收益标签由5,106降为0 | [修复与原始文件核验](../../research/subscription-training/README.md#level2-duplicate-2026-06) |
| `2026-09-21` | broker 新文件的时间、成交编码、序号及部分字段与历史格式不同，旧规则曾无法完成标准化 | **逐笔已补齐**：2026-10-01 按用户确认的映射和债券时段保存三个 raw，并发布沪深 `v1` 正成交；分钟及派生分区未在本次重建 | [字段对比、历史清理与本次入库](#level2-format-2026-09-21) |
| `2026-09-18` | H03 Label 依赖下一交易日 `09-21` 的分钟与复权因子，受上述缺口影响未生成 | **Label 缺失**；当日 Feature 分区仍存在 | [依赖与当前状态](#level2-format-2026-09-21) |

`2025-11-25` 相关缺失与 `2026-06-08/09` 重复已处理；`2026-09-21` 的 raw 与正成交
逐笔已补齐，分钟及其影响的 `09-18` Label 缺口仍存在。旧重复文件最初由谁、在哪个
生成或转存环节引入仍未确定。
旧实验结论不能自动用于修订后的数据；修订数据上的重验以对应研究卷宗的单独记录为准。

`2025-11-25` 的源端失败作为历史观测保留，本次本地补录没有重新检查 FTP 可用性。
其完整错误身份为：

```text
source unavailable; source=sz_trade broker=level2_ftp trade_date=2025-11-25
```

该历史结果表示上述精确 source identity 当时在源端不可用，不表示有效空记录集合；不推断缺失原因，
也不授权跳过该日或使用其他日期、source 的数据补齐。Workflow 的缺失失败语义仍由
[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md) 定义。

<a id="level2-format-2026-09-21"></a>

#### 2026-09-21 字段差异、历史清理与本次入库

本节仅记录数据观测和维护结果，不定义新的标准化映射。用户确认本批仍由同一 broker 提供，
三个原始逐笔数据先合入数据库，再按日导出。文件位于
`/home/wsw/Downloads/4564934805_铁皮卡lh/2026-09-21/`。
以下字段对比及清理核查保留 2026-09-22 当时的结果；当前映射由
[Level-2 归一化 owner](level2_normalization.md) 定义，最新入库结果见本节末尾。

以 `2026-09-18` 为基准，两日三个正式 source 的原始文件均完整读取，分别为
**471,322,951 / 527,687,863 行**；六个压缩包完整性校验通过，全部 `TradeTime` 日期前缀
与各自分区一致。另对 `2025-11-25`、`2026-06-09`、`2026-09-17` 每个 source 的前
100,000 行抽查，主要编码均与 `09-18` 一致；该抽样不代表这些历史日期的全量质量审计。
`Index`、`Level2Market` 不在本次比较范围。

| 字段或结构 | `2026-09-18` | `2026-09-21` | 对当时旧实现的影响 |
|---|---|---|---|
| 表头 | 上海16列、深圳订单13列、深圳成交12列 | 列名及顺序完全相同 | 单靠表头检查无法发现以下变化 |
| `ExchangeID` | 上海 `1`、深圳 `2` | `SH/SZ` | raw 编码变化；当前 processed 交易所由 dataset 路由决定 |
| 上海 `TickTime` | 7/8位 `HHMMSScc` | 全部9位，呈 `HHMMSSsss` 毫秒形式 | 当前解析器因超过8位失败 |
| 深圳 `TickTime/OrderTime` | 8/9位毫秒形式 | 全部补齐9位；两日末位均为0 | 长度仍在现有解析范围内 |
| 深圳 `ExecType` | `1` 81,337,432行，`2` 47,015,948行 | `F` 84,932,821行，`4` 47,332,953行，无 `1/2` | 现有成交映射会过滤掉全部记录 |
| `MainSeq/SubSeq` | 上海7个、深圳18个通道号；`SubSeq` 全部为正整数 | `MainSeq` 取值扩展到数千万；三个文件的 `SubSeq` 全部为0 | 原通道号及通道内序号的对应关系需核实 |
| 上海方向字段 | 订单 `Side=1/2`；成交 `Side` 为空，方向在 `TradeBSFlag=B/S/N` | 订单 `Side=B/S`，成交 `Side=B/S/N`；`TradeBSFlag` 全空 | 字段承载内容改变，不能直接认定旧映射仍适用 |
| 上海 `MDSecurityStat` | 状态事件有值 | 全空；状态事件的 `Side` 出现 `S/O/T/C/E` | 状态字段与编码变化 |
| 深圳 `OrderStatus/OrderType` | 状态全部 `A`；类型 `1/2/3` | 状态全空；类型新增 `U` 31,517行 | 原字段信息与枚举集合变化 |
| `LocalTimeStamp`（事件样本） | 纯数字时间，例如 `92500205` | 完整时间戳，例如 `2026-09-21 09:25:00.148` | 保持 raw-only，不作为标准化时间补值 |

上海新文件有 **47,307,078行（19.01%）** 的 `TickTime` 末位不为0，其中成交事件
**6,192,082行（成交事件的8.40%）**。直接截去末位会损失时间精度。新 `MainSeq`
也不是全文件连续递增序号：上海范围为 `1..52,619,410`，深圳订单为 `1..39,154,983`，
深圳成交为 `149..39,257,907`，三者都观察到回落；不能据此视为全局唯一事件身份。

| 原始文件 | `09-18` 行数 | `09-21` 行数 | 行数变化 | 不同 `SecurityID` 数 |
|---|---:|---:|---:|---:|
| 上海逐笔 | 200,487,385 | 248,871,439 | +24.13% | 3,937 → 25,841 |
| 深圳订单 | 142,482,186 | 146,550,650 | +2.86% | 4,144 → 5,002 |
| 深圳成交 | 128,353,380 | 132,265,774 | +3.05% | 4,144 → 5,118 |

这些是原始记录；上海包含状态、订单、撤单和成交，代码数不等于股票数或有效成交证券数。
跨交易日的数量差异不能直接证明漏数、补数或重复。本次未做6月问题所用的跨日去日期全文
哈希审计，也未验证价格、数量、金额在新旧导出中的单位等价性。差异直接存在于原始 CSV，
但现有证据不能定位至采集、合库或导出环节，也不能据此认定数据损坏或重复造假。

**正式库处理（2026-09-22 15:59:57 +08:00）**：用户明确要求删除 `2026-09-21` 正式数据。
按[正式存储布局](storage_layout.md)检查 `/home/wsw/app/data` 全部六个顶层命名空间，
共扫描66,205个目录、132,191个文件；未发现任何 `trade_date=2026-09-21` 分区或符号链接，
实际删除 **0个对象**。此前构建在隔离目录失败，未发布至正式库；该次入库到此停止。
下载文件与隔离检查证据保留，年度日历、其他日期和实验制品未改动。

`2026-09-18` 的 H03 Feature 分区存在，Label 分区仍缺失；其成熟所需的下一交易日输入是
`09-21`。依赖关系沿用[日常 Level-2 workflow](../offline_workflow_contract.md)，未用其他日期
替代，也未将缺失标记为有效空数据。当时重新接入前需要核实 broker 的导出字段映射，
尤其是原通道号是否保留；该次检查未采用新映射。

完整证据目录为
`/home/wsw/app/maintenance-evidence/level2-2026-09-21-ingest-2026-09-22-lrmjeohq/`，包括
`comparison.md`、`compare_delivery.py`、`compare-inputs.json`、`compare-日期-source.json`、
原失败日志 `build.log` 和本次清理核查 `formal-cleanup.json`。新文件指纹与下载时保存的
SHA-256 一致，统计结果与首次全量检查相符。分析使用保留的
`46ecdc1dba1a390cf7cfe8560f296cb7fcd0cbbf` 代码快照；环境见 `environment.json`。
上述 2026-09-22 检查只更新维护文档与外置核查记录，未修改标准化实现，
未执行 commit、merge、release 或 deploy。

**本次入库（2026-10-01）**：核实 broker 文档和字段字典后，按用户决定将新 CSV 的
`MainSeq/SubSeq` 原样保存，仅用于确定性排序，不推断通道或事件唯一性；按
[归一化 owner](level2_normalization.md) 识别新导出编码并保留沪市毫秒末位，按
[phase owner](market_phase.md) 补齐本批涉及的深市固收时段。
三个原压缩文件保持原始字节，正式发布两个 `v1` 正成交对象：沪市 **73,688,684 行、
3,808 个证券**，深市 **84,932,821 行、4,606 个证券**；`SZ_Order` 仍为 raw-only。
全量逐行核验、SHA-256、symbol slices、直接 raw lineage 和正式回读均通过，
311 项相关测试通过。旧 phase 拒绝的 918,965 条正成交全部保留。

本日存在 `501009`、`501028`、`501031` 三个跨市场同码；指定交易所可分别读取，
未指定交易所的两市合并读取继续按现有 Access 契约失败，不覆盖或拼接同码证券。
本次未生产分钟、Feature 或 Label。2026-10-02 合并前核查确认该日两市分钟、
`l2_stock_1430`/`stock_1430_daily_l2` Feature，以及 `2026-09-18` 的
`l2_stock_1430_t1_vwap_rank` Label 分区仍缺失。
入库精确输入、代码快照、失败检查与正式发布证据见
[完成记录](/home/wsw/app/maintenance-evidence/level2-baidu-2026-09-21-2026-10-01-hk6hwrmv/completion.json)。

## 正式交易日历

`processed/trade_calendar/v1` 是唯一正式交易日历，以 `year=YYYY` 保存每个自然年的一个
正式对象。payload 包含该年 Tushare response 转换后的全部行，schema 精确为：

```text
trade_date: string  # Tushare cal_date 归一化为 YYYY-MM-DD
is_open: bool
```

Tushare `trade_cal` 是日历日期完整性、唯一性、请求范围、交易所和 `is_open` 取值的正式
数据源权威。本系统不重复检查这些 source 业务不变量；normalize 只执行字段选择、日期格式
转换和 `0`/`1` 到 boolean 的类型映射。外部请求失败、空 response、缺少转换所需字段或
`cal_date` 无法完成日期格式转换仍必须失败。

有效 Meta 证明该自然年对象已经正式提交，不表示其中每一行都开市。正式交易日定义为：

```text
row in valid yearly trade_calendar object AND is_open == true
```

开市日和休市日都保存在同一个年度 payload 中。查询范围涉及的任一年度对象缺失时，整个
范围不可用。Producer 发现有效年度 Processed Meta 时直接复用，不隐式刷新或覆盖；日历
刷新不是当前 offline data workflow 的行为。旧 `trade_date=YYYY-MM-DD` 日历对象不构成
年度正式对象，Access 不读取该旧布局。

## Tushare processed 单位

Raw 保存来源 response 的字段、数值和单位。Normalize 是来源单位进入系统的唯一换算边界；
Access、Feature、Label 与回放消费 processed 单位，不再解释手、千元、万元或百分数。
本节拥有下表的来源映射；分红字段由[分红 owner](dividend_contract.md#dividend)拥有，
Level-2 的证券类型、价格编码与股票股数由[Level-2 owner](level2_normalization.md#raw-到-processed-字段关系)拥有。

当前股票事实采用股、人民币元、元/股和小数比例（1 表示 100%）。单位按具体 API 与字段绑定，
不按列名后缀或数值大小推断。字段名保留，例如 pct_chg 的 processed 数值仍按小数比例解释。

| 本地数据集（源 API） | 字段 | 来源单位 → processed 单位 | 转换及类型 |
| --- | --- | --- | --- |
| daily_bar（daily） | vol、ah_vol | 手 → 股 | ×100，nullable int64 |
| daily_bar（daily） | amount、ah_amount | 千元 → 元 | ×1000，float64 |
| daily_bar（daily） | pct_chg | 百分数 → 小数收益率 | ×0.01，float64 |
| daily_basic | total_share、float_share、free_share | 万股 → 股 | ×10000，float64 |
| daily_basic | total_mv、circ_mv | 万元 → 元 | ×10000，float64 |
| daily_basic | turnover_rate、turnover_rate_f、dv_ratio、dv_ttm | 百分数 → 小数比例 | ×0.01，float64 |
| stock_basic（bak_basic） | total_share、float_share | 亿股 → 股 | ×100000000，float64 |
| stock_basic（bak_basic） | rev_yoy、profit_yoy、gpr、npr | 百分数 → 小数比例 | ×0.01，float64 |
| moneyflow | buy/sell 的 sm、md、lg、elg 档 vol 及 net_mf_vol | 手 → 股 | ×100，nullable int64；净量允许负数 |
| moneyflow | 上述各档 amount 及 net_mf_amount | 万元 → 元 | ×10000，float64；净额允许负数 |

来源依据：[daily](https://tushare.pro/document/2?doc_id=27)、
[daily_basic](https://tushare.pro/document/2?doc_id=32)、
[bak_basic](https://tushare.pro/document/2?doc_id=262)、
[moneyflow](https://tushare.pro/document/2?doc_id=170)。同名 total_share 在 daily_basic 与
bak_basic 的源尺度相差一万倍，因此不能共享仅按字段名选择的转换规则。

采用股使价格乘数量直接得到元，避免成交、持仓和股本计算重复乘100；采用元使成交额、
市值和资金流金额能按同一尺度计算。例：13.01 手转换为1301股，不先取整手数，也不要求
成交股数为100的整数倍。手数使用精确十进制转换，非空值必须能无损落入 int64 股数；
不足一股的尾数、非有限值或溢出必须失败，daily_bar 的量还必须非负。来源 null 保留，
不能作为零成交或补齐盘后覆盖的依据。其他参与换算的非空数值必须有限，转换失败或溢出
失败整个对象；不截断、不补零。换算只处理已提供的通用源字段，不凭缺列合成事实。

股本是供应商发布的观察值，采用 float64 保留来源所提供的精度，不因以股表示而宣称精确到
一股，也不擅自取整。行情价格、金额与比例沿用 float64，不提前舍入到两位或三位小数；
金额单位为元不限制小数位。账户精确金额及公司行动条款继续沿用各自 Decimal 契约。
百分比转比例不要求数值在0到1内；收益和增长可为负，换手率可超过1。

以下字段数值不缩放：股票价格和绝对涨跌额；daily_basic 的 volume_ratio、pe、pe_ttm、pb、
ps、ps_ttm 等倍数；adj_factor；已以元提供的 margin/margin_detail 金额。cyq_perf 的
cost_5pct 等列表示成本价格，字段名中的分位不触发百分比换算。

尚不能确定映射的字段继续保留 source-native 语义，不宣称已统一：top_list 的金额与比例、
cyq_perf.winner_rate、bak_basic 的资产金额及 reserved/undp 等财务字段。前两者的官方字段
表缺少明确单位，后者仍需明确币种及未注明字段的尺度。margin 汇总的 rqyl/rqmcl 混合
股、份、手，无法用一个系数转换成股；margin_detail 的数量及非股票 Level-2 数量继续按
证券类型和原 owner 解释。源币种不同不能仅改单位名称转换成人民币。

单位一致只保证量纲可运算，不证明统计范围一致。单笔、累计快照、盘口存量、不同股本分母、
复权基准、主动买卖及 tick-rule 方向必须分别保留。日线与 Level-2 对账前须先确认证券、日期、
事件、时段和来源精度；日线成交额不要求等于收盘价乘成交量。

## Daily bar

`processed/daily_bar/v1` 是行情事实，不是交易日历。它只能证明某个正式交易日的日线行情
已经落地；其存在或缺失不得改变 `trade_calendar` 给出的交易日序列。

Workflow 不为 `is_open=false` 的日期请求 `daily_bar`。`is_open=true` 时缺少
`daily_bar` 是数据缺失并必须失败；feature、label、training 或 backtest 不得跳过该日并
把后续日期当作替代交易日。

该对象保留 [Tushare A 股日线](https://tushare.pro/document/2?doc_id=27) 的未复权价格，
OHLC、pre_close、change 为元/股，vol/ah_vol 为股，amount/ah_amount 为元，pct_chg 为
小数收益率；转换由上节唯一规定。pre_close 保留来源除权参考价含义，不能等同于上一日
原始收盘价。回放价格/时间映射由
[offline 工作流契约](../offline_workflow_contract.md#真实行情到回放输入)拥有。
Broker 显式请求 ts_code、trade_date、open、high、low、close、pre_close、change、pct_chg、
vol、amount、ah_vol、ah_amount。Processed 按该顺序保存，最后追加 symbol。
ah_vol/ah_amount 是可空的盘后独立观察，来源未提供字段或值时保留 null，不补0；
历史 raw 缺少这两个可选字段时仍只表达未知。该规则不证明日线主量额与盘后量额的包含关系，
也不允许相加生成总量；当前 Level-2 不声明盘后固定价格渠道覆盖。
Tushare 停牌期间不提供该证券日线，但不能反向推断每次日线缺失都属于停牌；缺行原因
仍需核实。缺行不表示零成交的完整日线，也不得使用后续日线补齐。
源端日线在盘后入库，其日标签不是精确的历史发布时刻证明。

单位调整继续使用 v1。已经提交的旧单位 processed 必须从明确 raw 重建，不能在读取时自动
猜测或兼容，也不能将旧 processed 再作为 Normalize 输入。有效 Meta 的命中不表示历史
对象已经完成单位调整。正式切换须同时处理受影响的 Feature、模型输入和回放消费，并验证
上下游尺度一致；单次普通 workflow 不隐式重写历史对象。缺少可选源字段的历史事实只有
重新取得来源观察才能补齐，不能通过单位转换生成。

## Source no-data 边界

单日 Tushare source 成功返回 `DataFrame` 即表示已取得 payload；零行 `DataFrame` 是有效
空记录集合，Broker 必须照常写入 raw payload 并返回该文件的 `Path`，不得把它转换为下载
失败。只有源端返回 `None` 时，单日 Tushare Broker 才返回 `None`。

`stock_st` 的 `2019-04-01` 是该边界的正式案例：源端成功响应且记录集合为零行，返回的
DataFrame 不携带列。Normalize 必须构造空 `symbol` 列，Producer 必须提交
`raw/tushare/stock_st/trade_date=2019-04-01/data.parquet` 及同目录 `meta.json`；该对象表示
Tushare 对当日查询返回的 ST 记录集合为空。本系统以该 source response 的记录集合为权威，
不在运行时使用外部来源复核其事实完整性；因此该对象不表示下载失败或数据缺失，也不得使用
`2019-03-29`、`2019-04-02` 或其他日期的记录填充。

`suspend_d` 的零行 response 使用相同的空 `symbol` 列构造规则。该规则只使空集合能够由
Access 直接消费，不增加事件记录。

Transport、认证、response 类型、非空 response 的必要字段缺失或没有上述可空映射的字段
转换失败必须传播为错误。`trade_calendar` 必须包含记录；对请求自然年返回 `None` 或零行
response 都必须使日历 producer 失败。其他 source 的 range 聚合和缺失失败语义由 workflow
owner 定义。
