# 数据源与核心日线数据契约

- **状态**：正式 owner
- **适用范围**：`data.brokers`、`data.sources`、source identity、Tushare active
  manifest、source-native 查询、`trade_calendar` 与 `daily_bar` 的正式语义。
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

Broker implementation 使用不可变的 `broker name -> broker implementation class`
mapping。该 mapping 只解析执行实现，不选择 source。运行时不得建立可变 register/freeze
registry。Raw Meta hit 前不得构造 broker adapter；首次 miss 时构造并在同一 workflow 内
按 broker 复用。

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

除 `trade_calendar` 外，当前 Tushare source 按单日参数
`trade_date=YYYYMMDD` 查询。`trade_calendar` 固定查询 SSE，并以自然年作为唯一请求与对象
分区：

```text
trade_cal(exchange="SSE", start_date=YYYY0101, end_date=YYYY1231)
```

每个缺失自然年最多请求一次。全年 response 原样保存为该年的 raw Parquet；其他 source
response 仍按单日保存。Broker 不改变正式 processed 字段。

## Level-2 source identity

| source_name | raw_object | outputs |
|---|---|---|
| `sh_stock_ordertrade` | `SH_Stock_OrderTrade` | `["sh_trade"]` |
| `sz_order` | `SZ_Order` | `[]` |
| `sz_trade` | `SZ_Trade` | `["sz_trade"]` |

`sh_trade` 与 `sz_trade` 的字段和 index 由
[`docs/data/level2_normalization.md`](level2_normalization.md) 所有。

### 已知 Level-2 源端缺失

| broker | source_name | raw_object | trade_date | 观测结果 |
|---|---|---|---|---|
| `level2_ftp` | `sz_trade` | `SZ_Trade` | `2025-11-25` | `source unavailable` |

该记录对应的完整错误身份为：

```text
source unavailable; source=sz_trade broker=level2_ftp trade_date=2025-11-25
```

该结果表示上述精确 source identity 在源端不可用，不表示有效空记录集合；不推断缺失原因，
也不授权跳过该日或使用其他日期、source 的数据补齐。Workflow 的缺失失败语义仍由
[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md) 定义。

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

## Daily bar

`processed/daily_bar/v1` 是行情事实，不是交易日历。它只能证明某个正式交易日的日线行情
已经落地；其存在或缺失不得改变 `trade_calendar` 给出的交易日序列。

Workflow 不为 `is_open=false` 的日期请求 `daily_bar`。`is_open=true` 时缺少
`daily_bar` 是数据缺失并必须失败；feature、label、training 或 backtest 不得跳过该日并
把后续日期当作替代交易日。

## Source no-data 边界

单日 Tushare source 成功返回 `DataFrame` 即表示已取得 payload；零行 `DataFrame` 是有效
空记录集合，Broker 必须照常写入 raw payload 并返回 `DownloadPlan`，不得把它转换为下载
失败。只有源端返回 `None` 时，单日 Tushare Broker 才返回 `None`。

`stock_st` 的 `2019-04-01` 是该边界的正式案例：源端成功响应且返回完整字段，但记录集合
为零行。Producer 必须提交
`raw/tushare/stock_st/trade_date=2019-04-01/data.parquet` 及同目录 `meta.json`；该对象表示
当日没有 ST 排除项，不表示下载失败或数据缺失。不得使用 `2019-03-29`、`2019-04-02`
或其他日期的记录填充该对象。

Transport、认证、response 类型、必要字段缺失或没有上述可空映射的字段转换失败必须传播
为错误。`trade_calendar` 必须包含记录；对请求自然年返回 `None` 或零行 response 都必须
使日历 producer 失败。其他 source 的 range 聚合和缺失失败语义由 workflow owner 定义。
