# Access 契约

- **状态**：正式 owner
- **适用范围**：`src/access/access.py::Access` 的正式 processed 市场数据读取、
  可用交易日及下一正式交易日、历史沪深股票成员、日频行情/复权因子/换手率、停复牌事件、日频 universe、Level-2
  universe、Level-2 symbol/trade 市场范围读取、两市股票分钟事实、分红、公司行动处置、公告事实及事件视图读取；
  以及 `src/access/feature_label.py` 的已提交 Feature/Label 日分区读取。

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

    def next_trade_date(self, *, trade_date: str) -> str: ...

    def historical_stock_listings(self, *, trade_date: str) -> dict[str, str | None]: ...

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

    def level2_symbols(
        self,
        *,
        trade_date: str,
        exchange: Literal["sh", "sz"] | None = None,
    ) -> tuple[str, ...]: ...

    def daily_bars(
        self,
        *,
        trade_date: str,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame: ...

    def suspensions(self, *, trade_date: str) -> pd.DataFrame: ...

    def dividends(self, *, record_date: str) -> pd.DataFrame | None: ...

    def corporate_actions(self, *, record_date: str) -> tuple[CorporateActionResolution, ...] | None: ...

    def announcement_facts(
        self, *, publication_date: str, as_of_ts_us: int,
        observed_by_ts_us: int | None = None,
    ) -> tuple[AnnouncementFacts, ...] | None: ...

    def announcement_history(
        self, *, as_of_ts_us: int, symbols: Sequence[str] | None = None,
        observed_by_ts_us: int | None = None,
    ) -> tuple[AnnouncementFacts, ...]: ...

    def announcement_events(
        self, *, as_of_ts_us: int, symbols: Sequence[str] | None = None,
        observed_by_ts_us: int | None = None,
    ) -> tuple[AnnouncementFacts, ...]: ...

    def corporate_action_notices(self, *, trade_date: str) -> pd.DataFrame | None: ...

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

    def stock_trade_minutes(self, *, trade_date: str) -> pa.Table: ...

    def trades(
        self,
        *,
        trade_date: str,
        symbols: Sequence[str],
        exchange: Literal["sh", "sz"] | None = None,
    ) -> dict[str, pa.Table]: ...
```

Access 不提供接收 `dataset_name` 的 public 方法，也不提供任意路径或任意表读取。

## 正式交易日与日频对象

`daily_bars()` 返回正式 `daily_bar`。`symbols=None` 返回当日完整对象；显式 symbol
序列返回按请求顺序排列的行；空序列返回保留列的空表。请求 symbol 必须是唯一的六位
数字字符串。量、金额与比例直接沿用[processed 单位](../data/source_contract.md#tushare-processed-单位)，
不在 Access 缩放；日线的 ah_vol/ah_amount 保留独立的可空盘后观察。读取 vol/ah_vol 时
保留 nullable int64，不能因同列含 null 而转为浮点并丢失大整数精度。
`adjustment_factors()` 和 `turnover_rates()` 使用相同选择规则，并分别返回
精确列：

```text
symbol, trade_date, adj_factor
symbol, trade_date, turnover_rate
```

三个方法都要求底层对象包含所需列、`symbol` 是唯一六位数字字符串，并且每行
`trade_date` 精确等于请求日期。Access 保证对象 identity 与请求选择已经可直接消费，
不解释价格、因子或换手率数值的 feature/label 有效性。该数值语义由对应 producer owner
拥有。

<a id="suspension-events"></a>
`suspensions(trade_date=T)` 读取正式 `suspend_d`，返回独立的
`symbol, trade_date, suspend_type, suspend_timing` 四列表。非空对象的 symbol 必须是六位
数字字符串，trade_date 必须等于 T。按 [Tushare 字段定义](https://tushare.pro/document/2?doc_id=214)，
suspend_type 只能是 `S`（Suspend，停牌）或 `R`（Resume，复牌）；suspend_timing 表示日内
停牌时间段，日内停牌才有值，否则为 null；非 null 值必须是非空字符串。
正式空对象即使只有空 symbol 列，也返回同一四列空表；Meta/payload 缺失仍失败。

S/R 描述停复牌事件类型。同一证券同一天同时出现 S 和 R，只表示源数据同时提供了停牌和
复牌记录；仅凭类型代码或返回行顺序，无法确定事件先后、是否盘中恢复交易或当天实际
可交易状态。源端字段说明没有定义同日事件的可靠顺序或 S/R 优先级，核实状态还需结合
时段字段和当时可见的有效公告。Access 保留源记录顺序及全部事件，不按 symbol 去重、
选择其中一条或推断事件先后，也不因同日 S/R 并存就判定源记录错误。
Access 不将事件集合变成盘前可知的可交易名单；如何解释日线缺行和停牌估值，由
[回放输入 owner](../offline_workflow_contract.md#真实行情到回放输入)定义。

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

`next_trade_date(trade_date=T)` 要求 `T` 是正式交易日，返回其后第一个正式交易日。该方法
从 `T` 所在年度开始按需向后读取连续年度对象；`T` 休市、所需年度对象断档或没有可表示的
后续 session 时整体失败。它只解析已提交日历，不预测未来开市安排。

<a id="corporate-actions"></a>

## 公司行动对象读取

`dividends(record_date=...)` 读取 `processed/dividend/v1/trade_date=<record_date>`，与 composition root 提供的 processed version 一致。
字段含义归[数据源 owner](../data/dividend_contract.md#dividend)；本边界检查完整列、证券代码和
记录所属登记日，返回独立 DataFrame，不筛选实施状态或改变金额；base_date/base_share
随来源行返回，base_share 已为股，不再乘10000。

`corporate_actions(record_date=...)` 读取 `corporate_actions/v1`，返回不可变的
`CorporateActionResolution` tuple。校验持久化模型、登记分区、内外证券身份及候选唯一性；
可执行处置保存完整普通 source_record 或已确认 review，unresolved 二者均为空。
source_record 不能与同证券、同登记日的其他未关联候选同时作为普通单事件执行。
同 action_id 的多个处置必须保存完全相同的 review、同一证券及完整 source 关联集合，
缺失关联候选或冲突核验直接失败。Access 保留所有候选处置，不去掉重复事件所关联的来源，
也不在读取时核验公告或选择版本；账户消费方按事件身份执行一次。普通来源、已核实事件
与未解决 disposition 一同返回，review 为空不等于不可执行。

公告字段、公开时间与事件累计由[公告 owner](../data/announcement_contract.md#事件进入事实补齐与账户投影)定义。
`announcement_facts(publication_date, as_of_ts_us, observed_by_ts_us=None)` 返回该自然披露日
可见的 `AnnouncementFacts` tuple，缺 Meta 返回 None，已提交但没有可见行返回空 tuple。
读取校验原生公告唯一性、分区日期、外列与 facts 的事件身份、证券及两种时间一致性。
`announcement_history(as_of_ts_us, symbols=None, observed_by_ts_us=None)` 读取截止该时点的
已提交公告事实流；`announcement_events(...)` 调用来源领域的累计函数形成事件视图。
两个接口均先过滤公开时刻，再按可选观察时刻过滤，之后才累计，不能从最终事件向过去
回填字段。系统观察限制省略时表示公共信息历史视图，不声称当年该系统已经取得材料。
累计视图保留缺项和冲突，适用于筛选、特征构建和账户投影；返回已有材料不等于证明
所有历史日期覆盖完整，覆盖核对使用逐日接口。所有读取保持本地，不触发联网或正文解析。

`corporate_action_notices(trade_date=...)` 读取同名 processed 数据集的补充公告，按事件生效
所属账户日分区。每行包含 action_id、symbol、available_ts_us、effective_ts_us、kind、source_ref；
kind 为 cash_option、merger、delisting 或 other。两种时间分别表示本条事实允许被回放消费
的时间和事件开始影响账户的时间，均为 UTC 微秒；source_ref 保存公告来源。这个数据集只
接受有依据的显式补充，不由 suspend_d 或日线缺口推断。它不是覆盖全市场公司行动的
自动采集源；事件模型与是否影响账户由 workflow 校验和判断。

逐分区接口只有 Meta 不存在时返回 None；已提交空表表示该对象没有返回记录。缺失的
corporate_actions 覆盖必须进入回测诊断，补充公告 None 则表示没有提供补充，不证明无其他事件。
Meta、上游、payload 损坏及字段错误继续失败，不能捕获后改成空表。回测不触发网络补取。

## Universe

当日正式 `stock_basic` 是源端 `bak_basic(T)` 产生的历史日股票列表，其唯一 symbol 定义
当日历史股票成员集合：

```text
historical_base(T) = symbols(valid stock_basic at T)
```

有效空 `stock_basic(T)` 定义空 `historical_base(T)`；Access 不使用 daily bars 或其他日期
补充成员。两种 universe 仍要求当日正式 `stock_st` 与 `suspend_d`，这些对象有效时直接返回
空 tuple。

`historical_stock_listings(T)` 仅从当日正式 stock_basic 读取历史成员及 list_date，按现有
Level-2 股票代码分类保留沪深 STOCK，按代码升序返回 symbol → list_date 的调用方独有
dict；有效空对象返回空 dict。非空日期必须为合法 YYYY-MM-DD，null 返回 None，表示
未知，不能单凭它认定未上市；非空未来日期原样保留，由调用方按当时时点解释。历史名单
可能提前包含待上市证券，所以名单成员不等于已上市证券。它不要求 daily_bar、stock_st
或 suspend_d，不过滤 ST、停牌、缺日线或上市天数，不包含北交所、B 股、基金等其他证券。
对象或 Meta 缺失、必要列缺失、symbol 非法或重复、非空 list_date 非法时失败，不改用今天
或其他日期的名单。该日快照同样不证明盘前可见性；全市场行情覆盖的按日确认与保留规则由
[回放输入 owner](../offline_workflow_contract.md#真实行情到回放输入)定义。

`universe(T, N)` 的基础集合是当日正式 daily bars 与历史股票成员集合的交集：

```text
daily_base(T)
  = symbols(valid daily bars at T) ∩ historical_base(T)
```

`level2_universe(T, N)` 的基础集合是当日全部必要正式 Level-2 对象 Meta 中
`symbol_slices` keys 的并集与历史股票成员集合的交集：

```text
level2_base(T)
  = union(symbol_slices of every required valid Level-2 object at T)
    ∩ historical_base(T)
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

两种 universe 始终要求当日正式 `stock_basic`，因为其记录集合定义
`historical_base(T)`。`N=0` 表示保留其中全部成员且不检查 `list_date`。正整数 `N` 只保留
存在非 null `list_date` 且满足下式的成员：

```text
T - stock_basic.list_date(symbol) >= N 个自然日
```

上市当日为第 `0` 天，恰好满 `N` 个自然日时保留。`list_date=null` 时不满足正整数上市
天数条件。Access 信任 Tushare 对象的记录集合，不要求它与 daily bars 或 Level-2 source
具有相同覆盖，也不解释 null 的源端业务含义。

当前 universe 不包含历史 ST 冷却、涨跌停、成交额、流动性、市值、行业或其他策略筛选。

## Level-2 trades

各正式 Level-2 对象的 symbol slice identity 和覆盖不变量由
`docs/data/level2_normalization.md` 拥有。

`level2_symbols(T, exchange)` 返回所选市场范围内正式 Meta 实际观察到的全部 symbol，按
symbol 升序。它不与 `stock_basic`、daily bars、ST 或停牌集合相交，也不按
`security_type` 过滤；调用方需要股票分钟事实时由对应 producer 消费持久化
`security_type`。`level2_universe()` 的既有全市场策略过滤语义不因此改变。

`level2_symbols()` 与 `trades()` 使用相同市场范围：

```text
exchange=None  -> 同时要求 sh_trade(T) 与 sz_trade(T)
exchange="sh"  -> 只要求 sh_trade(T)
exchange="sz"  -> 只要求 sz_trade(T)
```

显式交易所不读取也不要求另一市场，不向另一市场 fallback。`exchange` 不接受其他字符串或
类型。所选对象全部加载 Meta，验证 slice 覆盖 Parquet 总行数，并拒绝所选对象间重复 symbol；
任一必要对象缺失或无效时整个请求失败，不能返回部分市场。

`trades()` 只读取与显式请求 slice 相交的 row groups，再按 Meta 的全局行区间裁出每个 symbol
的表。`symbols` 继续必填，返回字典保持请求顺序；空请求返回空字典且不读取对象。请求 symbol
必须存在于所选市场范围，显式交易所不能从另一市场取得同名或缺失 symbol。

调用方通过 `level2_universe()` 取得研究集合后，可以把其中需要研究的有限 symbols
传给 `trades()`。`trades()` 不重复执行 universe 的上市、ST 或停牌过滤；请求 symbol
没有正式 Level-2 slice 时，整个请求失败，不返回部分结果。

## Level-2 股票分钟事实

`stock_trade_minutes(T)` 固定读取同一 Access processed version 下的
`sh_stock_trade_1m(T)` 与 `sz_stock_trade_1m(T)`，不接受 dataset、version、exchange 或
symbol 参数。两个对象都必须具有有效 Meta 和 payload；任一对象缺失或无效时整体失败，不能
返回单一市场。

每个对象必须精确匹配 [`level2_minute_contract.md`](../data/level2_minute_contract.md) 的 V1
Arrow schema、请求日期和完整唯一 key，并已按
`(symbol, trade_date, minute_start_ts_utc, phase)` 升序。两市对象出现重复 symbol 或合并后
重复 key 时失败。返回值保留同一 schema，并按完整 key 全局升序；有效的两市空对象返回固定
schema 零行表。Access 不执行 14:30 universe、窗口、Feature 或 Label 计算。

## 公告核验证券状态

`security_status(T)` 读取同日 `security_status/v1`，返回不可变的 SecurityStatus 序列。
没有该补充对象时返回空序列，含义仅为没有已提交的补充核验，不证明原始状态正常。
存在对象时验证 Meta、三列 schema、证券日唯一性、字段与分区身份，以及 schema metadata
中的 stock_basic、suspend_d、reviews 路径是否分别指向规范的同日对象，并要求这些对象
的 Meta 有效；身份或对象可用性不符立即失败，不计算内容摘要。
公告证据、状态取值及可见时间由[数据源 owner](../data/source_contract.md#公告核验的证券状态)
拥有，Access 不执行公告下载、条款提取、上市推断或行情填充。

## 错误归属

- 非法日期、版本、窗口大小、请求 symbol 和上市自然日参数在 public 边界以
  `TypeError` 或 `ValueError` 失败；非法 Level-2 exchange 使用相同错误边界。
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

## Feature/Label 分区读取

`read_features(*, pm, feature_set, version, trade_date, feature_names)` 与
`read_labels(*, pm, label_set, version, trade_date, label_column)` 是数据提供侧的具名读取边界，
不扩展 Access 的任意表或路径 API。它们从同一个 PathManager 选择规范对象，先 require
同目录 Meta，再以 context manager 读取 Parquet；不绕过 Meta，不改变数据身份或生成数据。

Feature 返回完整样本键后跟请求顺序的特征列；Label 返回完整键后跟单个标签列。
[日频 schema](../data/daily_feature_label_contract.md) 的键为 symbol、trade_date；
[14:30 schema](../data/stock_1430_feature_label_contract.md) 还包含 decision_ts_utc；H03 的
Feature 与 Label 身份要求读取这一键，缺列时失败，不能退化成日频身份。
读取保留两种现有 schema 中的全部键，校验非空逻辑字符串、完整键唯一性、分区日期一致
和决策时间整数类型，不排序、join、过滤样本或推断“有效日期”。Feature/Label 的逐行对应
由训练或评价边界检查。特征 NaN 和未知标签保留；训练过滤、预处理和评价各自处理其
负责的缺失与数值失败。样本键及 ts_us 不得选择为特征。

返回表归调用方拥有。列的业务含义及计算可见性仍由各 Feature/Label owner 定义；此 API
不提供历史 availability 证明，也不把当前 Meta 或文件时间解释为过去已可见。
