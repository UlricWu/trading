# Access 契约

- **状态**：正式 owner
- **适用范围**：`src/access/access.py::Slice` 的正式 processed 市场数据读取、
  可用交易日、日频窗口、股票 universe、收盘涨停和 Level-2 symbol slice。

## 身份与职责

`Slice` 表示一个确定的 `(storage_root, processed version, trade_date)` 研究读取视图。
`trade_date` 必须是规范的 `YYYY-MM-DD`；processed version 必须由调用方显式提供。
同一个 `Slice` 的所有读取使用这一个版本，并以绑定日期作为单日查询日或窗口
`end_date`。

Access 负责把正式 processed 对象直接提供成研究可用的数据，并集中拥有跨日期选择。
Meta 负责证明一个存储对象已经提交且 payload 可用。Access 的每次对象读取都必须先通过
同目录 Meta；仅有 `data.parquet` 不构成可用对象。

## Public API

```python
class Slice:
    def __init__(
        self,
        pm: PathManager,
        trade_date: str,
        *,
        version: str,
    ) -> None: ...

    def trade_dates(self, *, start_date: str) -> list[str]: ...

    def recent_trade_dates(self, *, sessions: int) -> list[str]: ...

    def daily(
        self,
        dataset_name: str,
        *,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame: ...

    def daily_window(
        self,
        dataset_name: str,
        *,
        sessions: int,
    ) -> dict[str, pd.DataFrame]: ...

    def stock_universe(
        self,
        *,
        min_list_calendar_days: int,
        exclude_st_sessions: int,
        exclude_suspended: bool,
    ) -> list[str]: ...

    def closed_limit_up_symbols(self) -> list[str]: ...

    def level2_symbols(self) -> list[str]: ...

    def level2(self, symbols: Sequence[str]) -> dict[str, pa.Table]: ...
```

`dataset_name` 是正式存储中的 processed dataset identity；Access 不维护第二份 dataset
allowlist。非法路径段由 `PathManager` 拒绝。

`daily()` 返回一个 `pandas.DataFrame`。`symbols=None` 返回当日完整对象；显式 symbol
序列返回按请求顺序排列的行；空序列返回保留列的空表。请求 symbol 必须是唯一的六位数字
字符串。

## 可用交易日与窗口

一个实际可用交易日定义为：同一 processed version 下存在且能够通过 Meta 校验的正式
`daily_bar` 对象。目录或 payload 单独存在不算可用。

`trade_dates(start_date=S)` 返回闭区间 `[S, trade_date]` 内的可用交易日，按日期升序。
它不推断休市日，也不维护另一份交易日历。

`recent_trade_dates(sessions=N)` 返回截至并包含当前 `trade_date` 的最近 `N` 个实际可用
交易日，按日期升序。`N` 必须为正整数；当前 `trade_date` 不可用或历史不足 `N` 日时
整体失败。

`daily_window(dataset_name, sessions=N)` 先按上述规则确定日期，再读取每个日期的目标
对象。返回值按升序插入日期键。任何一个已选交易日的目标对象缺失或无效时，整个查询
失败，不返回部分结果。

## 股票 universe

基础 universe 是当日 `daily_bar` 中唯一 `symbol` 的原始顺序。`daily_bar` 属于股票日线
source，不再使用 Level-2 的 SecurityID 代码段规则重复判断证券类型。

三个过滤参数都是调用策略，必须由调用方显式提供；Access 不提供隐藏默认值。

### 上市天数

`min_list_calendar_days=0` 表示不执行上市天数过滤。正整数 `N` 保留满足下式的股票：

```text
trade_date - stock_basic.list_date >= N 个自然日
```

上市当日为第 `0` 天。`stock_basic.list_date` 必须是规范 `YYYY-MM-DD`。过滤启用时，
任一基础 universe symbol 缺少 `stock_basic` 记录或 `list_date` 非法都必须失败。

### 近期 ST

`exclude_st_sessions=0` 表示不执行 ST 过滤。正整数 `N` 使用截至并包含当前
`trade_date` 的最近 `N` 个实际可用交易日：

```text
recent_st(T, N) = union(stock_st(D) for D in recent_trade_dates(T, N))
```

历史不足 `N` 日或任一必要 `stock_st` 对象缺失时必须整体失败。`stock_st` 的所有行都
表示风险警示股票；Access 不解析名称，也不区分 `ST` 与 `*ST`。

### 停牌

`exclude_suspended=true` 时移除当日 `suspend_d` 中的 symbol；为 `false` 时不读取该
对象。必要对象缺失必须失败，不得用成交量或 `daily_bar` 缺失推断停牌。

所有过滤只删除基础 universe 中的 symbol，不改变剩余 symbol 的顺序。

## 收盘涨停

`closed_limit_up_symbols()` 返回当日 `daily_basic.limit_status` 为 `2` 或 `3` 的
symbol，保持 `daily_basic` 原始顺序：

```text
2 = 收盘涨停，不含一字涨停
3 = 一字涨停
```

`daily_basic.limit_status` 必须是 `0..6` 的非空整数。缺列、重复 symbol 或非法状态必须
失败。当前不定义 `closed_limit_down`、盘中触板、炸板、开盘涨停或涨跌停价计算。

## Level-2

当前 Level-2 对象固定为 `sh_trade` 和 `sz_trade`。各对象的 symbol slice identity 和
覆盖不变量由 `docs/data/level2_normalization.md` 拥有。

Access 加载两张对象的 Meta，验证 slice 覆盖 Parquet 总行数，并拒绝跨对象重复
symbol。`level2()` 只读取与请求 slice 相交的 row groups，再按 Meta 的全局行区间裁出
每个 symbol 的表。空请求返回空字典且不读取对象。

## 错误归属

- 非法日期、版本、路径段、窗口大小、请求 symbol 和 universe 参数在 public 边界以
  `TypeError` 或 `ValueError` 失败。
- 必要 Meta、payload 或直接 upstream 文件缺失时，在首次消费该对象的边界以
  `FileNotFoundError` 失败。
- 已存在但无效的 Meta、payload identity、直接 upstream identity、symbol slice、数据
  identity 或字段值以 `RuntimeError` 或该方法已定义的字段 `ValueError` 失败。
- Access 和 Meta 不记录日志；调用它们的 workflow/step 拥有运行日志。
- raw/source-native 日期、字段和 API 响应转换属于 broker/normalize，不属于 Access。

## 非目标

Access 不拥有 source 获取、normalize、特征计算、信号、交易规则模型、交易日预测、
DataFrame 通用变换、旧 API 兼容或跨日期隐式状态。Meta 不建立领域模型、递归 lineage、
hash identity 或并发事务。
