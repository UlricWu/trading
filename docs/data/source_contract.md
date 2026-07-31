# 数据源与核心日线数据契约

- **状态**：正式 owner
- **适用范围**：`data.brokers`、`data.sources`、source identity、broker source
  registry、source-native 查询、`trade_calendar` 与 `daily_bar` 的正式语义。
- **工作流 owner**：[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md)
- **存储 owner**：[`docs/data/storage_layout.md`](storage_layout.md)

## Source identity

`source_name` 是本地 raw 分区身份；`raw_object` 是 broker 识别的源端对象；`outputs`
是该 raw source 直接产生的 processed dataset 名。普通 source 必须显式声明一个
`raw_object` 和完整 `outputs`；`outputs=[]` 表示 raw-only。

`source.group` 只在 `offline_standard` 与 `offline_level2` 之间选择 source。
`enabled=false` 的 source 不参与执行。`use_broker_sources=true` 的条目不得再声明
`raw_object` 或 `outputs`；它展开 broker registry 的全部 source name，并对每项固定使用：

```text
source_name = registry name
raw_object = registry name
outputs = [registry name]
```

Feature 与 label 配置不使用 source group，其固定身份与执行归
[`docs/offline_workflow_contract.md`](../offline_workflow_contract.md) 所有。

## Tushare registry

本地 broker `tushare` 的 registry 固定为：

| source_name / raw_object / output | Tushare API |
|---|---|
| `trade_calendar` | `trade_cal` |
| `daily_bar` | `daily` |
| `adj_factor` | `adj_factor` |
| `daily_basic` | `daily_basic` |
| `stock_basic` | `stock_basic` |
| `stock_st` | `stock_st` |
| `stk_limit` | `stk_limit` |
| `suspend_d` | `suspend_d` |
| `cyq_perf` | `cyq_perf` |
| `margin` | `margin` |
| `margin_detail` | `margin_detail` |
| `moneyflow` | `moneyflow` |
| `top_list` | `top_list` |

除 `trade_calendar` 外，当前 Tushare source 按单日参数
`trade_date=YYYYMMDD` 查询。`trade_calendar` 固定查询 SSE：

```text
trade_cal(exchange="SSE", start_date=D, end_date=D)
```

其中 `D` 使用 `YYYYMMDD`。每个 source response 原样保存为自己的 raw Parquet；broker
不改变正式 processed 字段。

## Level-2 source identity

| source_name | raw_object | outputs |
|---|---|---|
| `sh_stock_ordertrade` | `SH_Stock_OrderTrade` | `["sh_trade"]` |
| `sz_order` | `SZ_Order` | `[]` |
| `sz_trade` | `SZ_Trade` | `["sz_trade"]` |

`sh_trade` 与 `sz_trade` 的字段和 index 由
[`docs/data/level2_normalization.md`](level2_normalization.md) 所有。

## 正式交易日历

`processed/trade_calendar/v1` 是唯一正式交易日历。每个请求自然日必须产生一个有效正式
对象，payload 精确包含一行：

```text
trade_date: string  # Tushare cal_date 归一化为 YYYY-MM-DD
is_open: bool
```

该行的 `trade_date` 必须等于分区日期。空 response、非唯一日期、日期不匹配、缺少字段
或 `is_open` 不是 `0`/`1` 都必须失败。`0` 归一化为 `false`，`1` 归一化为 `true`。

有效 Meta 只证明该自然日的日历对象完整，不表示开市。正式交易日定义为：

```text
valid trade_calendar object AND is_open == true
```

因此开市日和休市日都必须保存非空 payload 与有效 Meta。日历范围缺少任一自然日对象时，
整个范围不可用。

## Daily bar

`processed/daily_bar/v1` 是行情事实，不是交易日历。它只能证明某个正式交易日的日线行情
已经落地；其存在或缺失不得改变 `trade_calendar` 给出的交易日序列。

Workflow 不为 `is_open=false` 的日期请求 `daily_bar`。`is_open=true` 时缺少
`daily_bar` 是数据缺失并必须失败；feature、label、training 或 backtest 不得跳过该日并
把后续日期当作替代交易日。

## Source no-data 边界

Broker 只有在源端明确返回无 payload 时才返回 `None`；transport、认证、response 类型或
source response 不合法必须传播为错误。`trade_calendar` 对请求自然日返回 `None` 永远是
错误。其他 source 的 range 聚合和 `SKIPPED` 语义由 workflow owner 定义。
