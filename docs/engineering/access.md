# Access 契约

- **状态**：正式 owner
- **适用范围**：`src/access/access.py::Access` 的正式 processed 市场数据读取、
  可用交易日、日频行情/复权因子/换手率、日频 universe、Level-2 universe 和 Level-2
  symbol 读取。

## 身份与职责

`Access` 表示一个确定的 `(storage_root, processed version)` 市场数据访问能力。
processed version 必须由 composition root 显式提供；offline data、training 和 backtest
composition root 固定使用 `v1`，配置和 submission 不选择版本。每次 workflow 从自己的
唯一 `PathManager` 创建一个 Access，不接受调用方另传可能绑定其他 storage root 的
Access。所有查询日期由 public 方法显式接收，并且必须是规范的 `YYYY-MM-DD`。

Access 负责把正式 processed 市场对象提供成不暴露 dataset、broker、路径和 Meta 的具名
研究能力。Meta 负责证明一个存储对象已经提交且 payload 可用。Access 的每次对象读取都
必须先通过同目录 Meta；仅有 `data.parquet` 不构成可用对象。

## Public API

```python
class Access:
    def __init__(
        self,
        pm: PathManager,
        *,
        processed_version: str,
    ) -> None: ...

    def trade_dates(
        self,
        *,
        start_date: str,
        end_date: str,
    ) -> list[str]: ...

    def recent_trade_dates(
        self,
        *,
        end_date: str,
        sessions: int,
    ) -> list[str]: ...

    def universe(
        self,
        *,
        trade_date: str,
        min_listing_calendar_days: int,
    ) -> tuple[str, ...]: ...

    def level2_universe(
        self,
        *,
        trade_date: str,
        min_listing_calendar_days: int,
    ) -> tuple[str, ...]: ...

    def daily_bars(
        self,
        *,
        trade_date: str,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame: ...

    def adjustment_factors(
        self,
        *,
        trade_date: str,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame: ...

    def turnover_rates(
        self,
        *,
        trade_date: str,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame: ...

    def trades(
        self,
        *,
        trade_date: str,
        symbols: Sequence[str],
    ) -> dict[str, pa.Table]: ...
```

Access 不提供接收 `dataset_name` 的 public 方法，也不提供任意路径或任意表读取。

## 正式交易日与日频对象

`daily_bars()` 返回正式 `daily_bar`。`symbols=None` 返回当日完整对象；显式 symbol
序列返回按请求顺序排列的行；空序列返回保留列的空表。请求 symbol 必须是唯一的六位
数字字符串。`adjustment_factors()` 和 `turnover_rates()` 使用相同选择规则，并分别返回
精确列：

```text
symbol, trade_date, adj_factor
symbol, trade_date, turnover_rate
```

三个方法都要求底层对象包含所需列、`symbol` 是唯一六位数字字符串，并且每行
`trade_date` 精确等于请求日期。Access 保证对象 identity 与请求选择已经可直接消费，
不解释价格、因子或换手率数值的 feature/label 有效性。该数值语义由对应 producer owner
拥有。

正式交易日只由指定 processed version 下按自然年分区的 `trade_calendar` 对象定义；其中
`is_open=true` 表示正式交易日，`is_open=false` 表示休市。日历对象 schema、Tushare
权威边界与 `daily_bar` 的职责边界由
[`docs/data/source_contract.md`](../data/source_contract.md) 所有。

`trade_dates(start_date=S, end_date=E)` 要求闭区间 `[S, E]` 涉及的每个年度日历对象都
存在，返回范围内 `is_open=true` 的日期并按日期升序。缺少任一年度对象必须失败；Access
不重复检查 source 已保证的日期完整性、唯一性、范围和字段值域。

`recent_trade_dates(end_date=E, sessions=N)` 要求 `E` 是正式交易日，并返回截至且包含
`E` 的最近 `N` 个正式交易日，按日期升序。该方法从 `E` 所在年度开始按需向前读取连续
年度对象；`N` 必须为正整数，`E` 休市、年度对象断档或历史不足时整体失败。

## Universe

`universe(T, N)` 的基础集合是当日正式 daily bars 中的唯一 symbol：

```text
daily_base(T) = symbols(valid daily bars at T)
```

`level2_universe(T, N)` 的基础集合是当日全部必要正式 Level-2 对象 Meta 中
`symbol_slices` keys 的并集：

```text
level2_base(T)
  = union(symbol_slices of every required valid Level-2 object at T)
```

`level2_base` 不与 `daily_base` 求交。当前必要 Level-2 对象固定为上海 trades 与深圳
trades；任一对象缺失或无效时整体失败，不能返回部分市场。

两种 universe 对各自基础集合应用相同过滤：

```text
universe(T, N)
  = listing_eligible(daily_base(T), T, N)
  - symbols(valid stock_st(T))
  - symbols(valid suspend_d(T))

level2_universe(T, N)
  = listing_eligible(level2_base(T), T, N)
  - symbols(valid stock_st(T))
  - symbols(valid suspend_d(T))
```

`stock_st` 和 `suspend_d` 是事件集合。有效空对象表示当日没有对应排除项；Meta 或
payload 缺失表示 universe 不能产生，必须失败。过滤只删除基础集合中的 symbol。

两种 universe 都是当日相关正式对象提交后的观察集合，不表示盘前可知集合。返回值按
symbol 升序排列，不继承底层 payload 的物理行顺序。

### 上市自然日

`min_listing_calendar_days=N` 必须是非负整数，并由调用方显式提供。

`N=0` 表示不执行上市天数过滤，也不读取 `stock_basic`。正整数 `N` 保留在
`stock_basic` 中存在非 null `list_date` 且满足下式的 symbol：

```text
T - stock_basic.list_date(symbol) >= N 个自然日
```

上市当日为第 `0` 天，恰好满 `N` 个自然日时保留。基础集合 symbol 缺少 `stock_basic`
记录或 `list_date=null` 时不满足上市天数条件。Access 信任 Tushare 对象的记录集合，不
跨 source 检查覆盖关系，也不解释 null 的源端业务含义。

当前 universe 不包含历史 ST 冷却、涨跌停、成交额、流动性、市值、行业或其他策略筛选。

## Level-2 trades

各正式 Level-2 对象的 symbol slice identity 和覆盖不变量由
`docs/data/level2_normalization.md` 拥有。

Access 加载全部必要对象的 Meta，验证 slice 覆盖 Parquet 总行数，并拒绝跨对象重复
symbol。`trades()` 只读取与显式请求 slice 相交的 row groups，再按 Meta 的全局行区间
裁出每个 symbol 的表。返回字典保持请求顺序；空请求返回空字典且不读取对象。

调用方通过 `level2_universe()` 取得研究集合后，可以把其中需要研究的有限 symbols
传给 `trades()`。`trades()` 不重复执行 universe 的上市、ST 或停牌过滤；请求 symbol
没有正式 Level-2 slice 时，整个请求失败，不返回部分结果。

## 错误归属

- 非法日期、版本、窗口大小、请求 symbol 和上市自然日参数在 public 边界以
  `TypeError` 或 `ValueError` 失败。
- 必要 Meta、payload 或直接 upstream 文件缺失时，在首次消费该对象的边界以
  `FileNotFoundError` 失败。
- 请求 symbol 没有对应正式行或 Level-2 slice 时以 `KeyError` 失败。
- 已存在但无效的 Meta、payload identity、直接 upstream identity、symbol slice、数据
  identity 或字段值以 `RuntimeError` 或该方法已定义的字段 `ValueError` 失败。
- Access 和 Meta 不记录日志；调用它们的 workflow/step 拥有运行日志。
- raw/source-native 日期、字段和 API 响应转换属于 broker/normalize，不属于 Access。

## 非目标

Access 不拥有 source 获取、normalize、feature、label、信号、交易规则模型、交易日
预测、涨跌停查询、任意 DataFrame 变换、旧 API 兼容或跨日期隐式状态。Meta 不建立
领域模型、递归 lineage、hash identity 或并发事务。
