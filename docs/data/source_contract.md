# 数据源与核心日线数据契约

- **状态**：正式 owner
- **适用范围**：`data.brokers`、`data.sources`、source identity、Tushare active
  manifest、source-native 查询、`trade_calendar`、`daily_bar`、`dividend` 及分配事实解析的正式语义。
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
及 SHA-256、原始名单/停复牌输入摘要和判断依据。当前对象表达完整账户日状态，证据必须
在该日零时以前可见；盘中披露只能支持后续完整账户日，不能反向改写当天时段。
状态按证据能够证明的适用日期生成；
未来才披露的上市日、复牌日或更正不得提前影响历史成员及成交。只有日期的公告沿用
下一自然日零时才可见的保守约定；采集时间独立保存，不能充当历史公开时间。
确认待上市不要求知道未来上市日；发行阶段证据必须来自当时，空 list_date、缺行情或
后来上市本身都不能替代该证据。确认全天停牌需要对应 A 股证券的公告及期间覆盖，
不能将同一发行人的 H 股复牌套用到 A 股。证据不能消除冲突时继续保留未知。

原始核验输入 `SecurityStatusReviews` 固定包含 `version=v1`、`source_digests`、
`documents`、`reviews`。前者分别绑定同日 `stock_basic`、`suspend_d` 的 SHA-256；
documents 保存公告身份、证券代码、标题、公开日期、URL、PDF 摘要和采集时间。
每条 review 包含 `ts_code`、`trade_date`、可空的 `listing_status`
（pre_listing/listed/delisted）、`trading_status`（suspended/trading）、`available_ts_us`、
`evidence_refs` 和 `reason`。两种状态至少确认一种；交易状态必须同时确认 listed。
每个引用必须对应同一证券的原文及摘要，同一证券日唯一。

`ResolveSecurityStatusStep` 校验原文及原始输入后发布三列 string：`symbol`、
`trade_date`、`resolution`；resolution 是上述 review 的 JSON。Parquet schema metadata
`security_status_inputs` 保存两项来源及 reviews 文件摘要，直接 upstream 指向核验输入的
Meta。输入摘要不变时复用；没有显式核验输入时不创建“已核实”对象。

核验结果由 data-system 发布为正式 V1 对象，Access 负责读取、schema、身份及输入失效校验；
回放仍只使用真实行情生成执行观察。源摘要变化使派生核验失效，原记录、原文及旧结果
保留审计。原始来源冲突已经被充分权威证据解释时，仍保留冲突与处置依据，不把已确认
状态继续冒充未解决，也不删除来源行制造一致性。

除 `trade_calendar` 与 `dividend` 外，当前 Tushare source 按单日参数
`trade_date=YYYYMMDD` 查询。`trade_calendar` 固定查询 SSE，并以自然年作为唯一请求与对象
分区：

```text
trade_cal(exchange="SSE", start_date=YYYY0101, end_date=YYYY1231)
```

每个缺失自然年最多请求一次。全年 response 原样保存为该年的 raw Parquet；其他 source
response 仍按单日保存。Broker 不改变正式 processed 字段。

<a id="dividend"></a>

### 分红送转数据

`dividend` 按 `record_date=YYYYMMDD` 查询。raw/processed 路径仍使用统一的
`trade_date=YYYY-MM-DD` 分区，该日期在此数据集表示股权登记日，不是公告日、除权日或
支付日。按登记日查询能够确定账户权益的归属；按除权日只查询回测区间会遗漏先前登记、
本期到账的权利，这些跨区间权利由初始账户显式携带，不能从当前持仓反推。

raw 保存原始 response；`processed/dividend/v1` 保留以下列。新增来源字段仍使用当前版本，
已入库分区从原始 response 同步重建，版本规则由[存储 owner](storage_layout.md#processedfeatures-与-labels)定义：

| 字段 | 正式含义 |
| --- | --- |
| ts_code、symbol | 保留含交易所的来源证券身份，并另存六位展示代码 |
| ann_date | 源端预案、决案公告日；不等于最终条款的历史可见时间 |
| end_date | 利润分配所属报告期结束日 |
| div_proc | 源端分配进度；预案、预披露与实施不能混为同一事件 |
| imp_ann_date、record_date、ex_date、pay_date、div_listdate | 实施公告日、登记日、除权除息日、派息日、红股上市日 |
| cash_div_tax | 每股税前现金红利，元/原股份；不是每十股金额 |
| stk_div | 每股送转合计比例；不再次叠加 stk_bo_rate 或 stk_co_rate |
| source_row_id | 完整原始行经空值规范化及排序 JSON 编码后的 SHA-256；重复行仍逐行保留 |

日期转换为 `YYYY-MM-DD`，金额和比例转换为十进制文本，源端 null 保留；分配进度仅去除首尾空白。
状态修正不会补出缺失的实施公告日，`实施 ` 且公告为空仍属于待核实事实。股票分配记录
可以没有现金侧，现金分红记录可以没有送转侧；两侧都未知不能当成确定的零分配。零行
response 必须生成上述列齐全的空对象，明确区别于尚未采集。单次 response 达到接口
2,000 行上限时无法证明完整，下载失败，不提交可能截断的对象。

2026-10-16 的历史覆盖验收对齐已提交 `daily_bar/v1` 的 2018-01-02 至 2026-09-30，
共 2,123 个交易日，也覆盖现有分钟数据的全部 222 个日期。通过现有 data-system Steps
补齐 2,102 个登记日并复用原有 21 日；raw、processed 与 Meta 均齐全，共保存 34,165 条
源记录，其中 34,031 条状态严格等于“实施”，525 个分区为明确空集合。单次最多 179 行；
逐分区核对了字段转换、源值、登记日期和直接上游关系，再执行同一范围全部命中 Meta，
文件摘要不变。精确代码状态、日期集合、源对象摘要和校验脚本见
[历史入库证据](/tmp/trading-dividend-history-8g4non80/evidence.json)，临时证据目录需保留归档。
该验收证明当次返回值已按契约入库，不证明返回的空集合可靠，也不证明实施事件没有遗漏；
后续独立目录反查已发现反例，见下文历史覆盖中的源响应对账。

这批源记录不等于同样数量的独立分配事件。按证券、报告期、登记日检查，严格等于实施的
源记录有 915 组两行身份，其中沪深 906 组、北交所 9 组。866 组当时的标准化字段相同；49 组
字段不一致分为：21 组仅实施公告日不同、25 组仅现金为 0/null、1 组同时存在上述两类差异、
2 组非零现金金额不同。2026-10-16 对全部 472 个相关登记日重新查询，915 组均复现，返回
行多重集合与原入库对象一致。该统计不证明 915 组对应同一种业务关系，也不证明单行事件可靠。
原始查询及文件保留证据仍见上面的历史入库记录。

按首尾空白规范化重建后，实施记录为 34,032 条、候选关联组为 33,116 组，重复组为
916 组。新增的一组是 `600803.SH/2024-12-31/2025-07-21`，此前其中一行的“实施 ”未被
严格相等筛选纳入；这修正统计口径，不表示新增一次分配。当时错误地为新增字段另开版本目录，
该次验证保留了原 raw、原派生对象及其 Meta 共 8,492 个文件，二次执行未改写新制品；
实际目录及精确源码以该次证据为准，不能将其视为当前版本契约。
当前规范化与解析输入统一使用 `dividend/v1`。2026-10-17 从原始对象重建全部 2,123 日，
并同步重建 `corporate_actions/v1` 的直接上游引用；规范化 payload 与修正前逐字节相同，
解析事实不变，原始输入及公告核验输入未改写，重复执行复用全部分区。木林森与中岩大地的
真实历史回放也验证了登记、派现与送转结果。已替换的派生对象移出正式存储并归档，
精确源码、输入摘要、回归测试及回放结果见[V1 修正证据](/tmp/trading-dividend-v1-20261017-s57bz_hg/evidence.json)。
该次 V1 修正验收时，五组公告核验形成 4 个 resolved 和 1 个 not_entitled，其他 33,111 组为
unresolved，其中包含 911 个重复组。这是该次精确输入的统计；后续发布情况见下方公告流程验收。核验状态
不能由“已入库”代替。精确代码、全部输入摘要、
逐重复组处置及重跑核对见[解析与回放证据](/tmp/trading-corporate-action-resolution-_6lgzo9e/evidence.json)。

#### 从来源记录到业务事件

本节唯一拥有 `corporate_actions/v1` 的数据语义。账户生命周期仍由
[公司行动与非交易账户事件](../offline_workflow_contract.md#公司行动与非交易账户事件)拥有。
`ResolveCorporateActionsStep` 在 dividend/v1 之后运行，按 `ts_code/end_date/record_date` 聚集
待解释记录。该键只是候选关联键，不是来源承诺的事件主键；不通过给公告行加序号或
ann_date 把重复记录变成多次到账，也不以数值一致证明分配对象相同。

可回放的条款必须有公告核验事实。核验输入是
`raw/announcements/corporate_action_reviews/trade_date=<登记日>/reviews.json` 及其 Meta；
JSON 为 `CorporateActionReview` 数组。每项包含 candidate_id、source_digest、related_sources、独立 action_id、
shareholder_scope、record_date、ex_date、pay_date、div_listdate、terms、evidence_refs、reason。
related_sources 是其他候选关联键到其来源摘要的映射；空映射表示该事件只绑定主候选。
主候选不得再次出现在映射中，同一候选不能绑定到两个核验事件；关联候选必须属于同一证券和登记日。
必须由公告证明它们属于同一计划，不能仅凭相同证券、登记日、金额或不同报告期自动归并。
每个候选仍发布自己的 resolution，保存同一份完整 review；账户按 action_id 只登记、派发一次。
dividend 候选的 source_digest 是组内按字典序排序的全部 source_row_id（保留重复项）用换行连接后的 SHA-256。
核验事实必须绑定这个摘要；来源变化、核验身份重复、没有有效来源的核验或非法条款直接失败，不能继续
使用过期核验。公告链接及解释属于输入事实，不允许在回测代码里写股票代码特例。
`CollectAnnouncementsStep → ExtractDistributionTermsStep → ResolveCorporateActionsStep` 生产这些核验与解析事实；
采集边界、原文证据、人工复核输入及未覆盖能力见[公告流程](#announcement-verification-todo)。

经完整原文核验的实施公告也可独立产生 V1 事件，不要求 Tushare 已返回对应候选。
复核输入的 announcement_source 保存原生渠道、公告 ID、原证券代码、标题、公开日期、
已核实的精确公开时刻及原文 URL；candidate_id 为 `<渠道>:<公告ID>`，source_digest 为
该原文 SHA-256，action_id 为 `<渠道>:<公告ID>:ordinary_shareholder`。其 resolution 的
source_row_ids 为空，不能伪造 Tushare 行、报告期或源摘要。独立来源仍须完整采集该证券的
声明公告窗口、校验原文和索引、显式提供全部条款，并保留集合失效后重新核验的要求。
现有入口要求同日 dividend/v1 覆盖对象已准备，真实空集合可以满足这一前提，它不成为
这次公告事件的分红证据。精确 lineage 同时保存覆盖对象、公告集合、原文与复核输入。
以后 dividend 补到同一事件时，显式将其候选及摘要加入同一 review 的 related_sources；
保留公告主身份和原 action_id，每个来源各有处置、账户只执行一次。未完成关联时不能
同时批准两次分配，也不能仅按同日同证券自动合并。首次实现以原代码直接对应的公告为
边界；公告代码与账户证券不一致时须先补齐明确身份关联，不能推断代码相同或股份连续。

当前账户口径明确为 `ordinary_shareholder`：二级市场普通流通股持有人，不模拟公告单列的
大股东、回购专户、重整投资人或债权人身份。税前/税后与股东资格是不同维度；税前记账
不能使原本不合资格的持仓取得权益。terms 逐版本保存 published_date、可选 published_ts_us、cash_per_share、
shares_per_share、reference_cash_per_share、reference_shares_per_share，金额与比例为 Decimal。
后两项仅用于普通除权参考公式，必须由公告确认，不能默认复制账户分配金额。

首次有权益的计划必须在登记日前公开；条款修订可以在登记后、除权前公开。版本按证据支持的
可见时刻严格递增：只有日期时沿用下一自然日 00:00；有经原始索引核对的精确公开时刻时，
保存 UTC 微秒 published_ts_us，并核对其上海本地日期等于 published_date。来源只提供日期或
以午夜占位时，不据此声明精确发布时间。金额的零值必须明确提供，可执行版本的登记、除权、支付和上市日固定；
阶段日冲突不能通过自动选择最新公告消除。若原公告的支付日早于除权、尚不能形成合法的
可执行计划，且更正在任何账户阶段之前公开，复核输入可以从更正公告公开日开始提供首个
完整计划，并保留原文及冲突说明；不伪造更正前的可执行版本。本次 `002986`、`002727`
即属于这一范围，原公告、09-19 更正和更正后实施公告均保留。该处理不模拟更正前对错误
公告的交易反应。除权起点之后，仅有可靠精确公开时刻、且现金与送转权益均未改变的参考参数
修订可以进入账户；它只更新尚未被真实行情替代的临时参考估值，不重新确认或支付权益。
只有日期的除权后更正、晚到的现金或股份变更、已执行阶段的日期变更、没有可靠先后的同日
冲突及特殊公式仍保留未解决。已确认普通股东没有权益的事件可在登记日公开，但必须在除权日前公开。
北陆药业的[2018 年度实施公告](https://static.cninfo.com.cn/finalpage/2019-03-26/1205941522.PDF)
就是另一类日期冲突：登记和除息分别写为 2019-04-02、04-03，但现金支付写成 2018-04-03。
其[2019 年三季报](https://static.cninfo.com.cn/finalpage/2019-10-22/1206997459.PDF)确认分配已于
2019-04-03 实施，能够支持事后事实核查，不能证明当时已经取得合法的支付日期更正。
本次仍保留 unresolved，不能自动把年份改成 2019 或把十月报告当成四月可见输入。
原文摘要、报告对应页及该限制见[日期冲突核查](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/new-source-exceptions/300016-later-confirmation.json)。

每个候选组发布一个 resolution，保留 candidate_id、symbol、record_date、source_digest、
全部 source_row_ids、status、reason、review。状态为 resolved、not_entitled 或 unresolved；
后者 review 为 null，前两者必须保存核验内容。Parquet 的物理列为 symbol、record_date、
resolution（上述命名模型的 JSON），空集合仍发布列完整对象。Meta 的直接上游是同日
`dividend/v1`，Parquet metadata 的 corporate_action_inputs 另保存来源及核验 Meta 相对路径
对应的 payload SHA-256。自动生成核验的 `extraction.json` 还绑定 dividend payload、公告集合、显式复核输入、
生成代码和 PDF 解析依赖的摘要，保存逐候选失败原因及 reviews 内容摘要；其摘要进入
corporate_action_inputs。生产者重跑比较这些摘要；新增或修改输入时重建解析对象，不复用
旧的未解决结论。回测制品保存解析输入摘要和完整 disposition，以便复现。

缺少核验时保留待核实状态，不凭“只有一条”“重复字段相同”“取最新”“取最大”“null 补零”
生成权益。reason 区分实施日期缺失、非标准报告期、分配字段不一致、公告日期不同、重复方案
待核实、受益人与参考价未核实。非标准报告期仅是核验提示，不据此断言重整；同样，标准报告期
也不证明没有重整。扫描覆盖全部实施记录，而不仅是 49 组差异。原始行完整保存和解析全部完成
只证明数据流程完成，unresolved 不得计入业务核验通过数量。

核验依据说明了这些边界的必要性：

- [木林森 2017 年度实施公告](https://static.cninfo.com.cn/finalpage/2018-03-30/1204547578.PDF)
  对 2018-04-10 登记权益只规定一次每股 0.247 元和每股转增 1 股，重复披露不产生第二次权益。
- [顺丰 2024 年中期及特别分红实施公告](https://static.cninfo.com.cn/finalpage/2024-10-31/1221573964.PDF)
  的 1.4 元是中期 0.4 元与特别 1 元的合计，两个候选组内的源行 0.4、1.4、1 不可相加；
  除息折算金额为 1.3939620 元。两组通过 related_sources 关联同一 action_id。
- [中国平安 2017 年年度与三十周年特别分红实施公告](https://static.cninfo.com.cn/finalpage/2018-05-31/1205017466.PDF)
  明确年度每股 1 元加特别分红 0.2 元，合计一次 1.2 元；本地两个候选均只保存 0.2 元。
  这说明既不能直接采用源金额，也不能把重复候选相加，更不能将核验后的 1.2 元各派一次。
- [宝丰 2023 年度实施公告](https://static.cninfo.com.cn/finalpage/2024-07-18/1220664181.PDF)
  对中小股东每股派 0.3158 元，对指定大股东派 0.2650 元，除息参考价扣减 0.28 元；三个数值
  不可互换，两个源金额并非简单的错误值和正确值。
- [正邦重整实施公告](https://static.cninfo.com.cn/finalpage/2023-12-02/1218498400.PDF)
  的转增股份不分给原股东。后续[不开盘参考价调整的披露](https://static.cninfo.com.cn/finalpage/2023-12-07/1218534964.PDF)
  确认本次不调整参考价；普通账户不获得源 stk_div=1.623 所描述的重整股份。
- 天津松江和贵人鸟同样不能将公司总转增比例当作普通股东所得。天津松江
  [2021-12-21 提示公告](https://static.cninfo.com.cn/finalpage/2021-12-21/1211971348.PDF)
  与贵人鸟[2021-06-08 提示公告](https://static.cninfo.com.cn/finalpage/2021-06-08/1210183780.PDF)
  最终确认：股份仅用于投资人和债权人，普通股东没有现金或股份，除权参考价等于登记日
  收盘价。两个事件均有明确的次日除权日期。核验从登记日最终公告的可见时点开始，
  不把此前尚有条件的参考公式提前写成零参数。四个源候选关联两个 not_entitled 事件，
  各持有 100 股的受控验证中现金、应收和股数不变，重复执行没有新增账户分配。
  完整原文及输入见[核验依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/final-zero-right-review-basis.json)、
  [分区与账户验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/final-zero-right-pipeline-result.json)。
  同分区另外两项 JSON 变化仅为 Decimal 的末尾零，类型化业务事实相同，见
  [分区变化核对](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/final-zero-right-other-changes.json)。
- [中岩大地 2025-07-05 调整公告](https://static.cninfo.com.cn/finalpage/2025-07-05/1224085489.PDF)
  将每股现金 0.2816137 改为 0.2814904、转增 0.3994521 改为 0.3992772，登记日为 07-04。
  两条快照都保存最终比例并不证明 06-30 已经公开最终条款；需保留原公告和修订两个版本。
  [06-30 原实施公告](https://static.cninfo.com.cn/finalpage/2025-06-30/1224017153.PDF)的参考送转比例
  为 0.3921344，旧人工记录截断为 0.392134，本次依据原文修正；07-05 最终参考比例仍为
  0.3919658，不把原版精度修正当成一次新的股东分配。四组旧人工记录重新关联已采集原文，
  保留源摘要、原文摘要及更新理由，见[重新核验结果](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/prior-manual-rebinding-result.json)。

上述关联规则在 19 次逐文核验的分配中，以 43 条原始行、38 个候选组验证。每组仍保存
自己的处置及来源摘要，账户准备只生成 19 个事件；每个事件登记持有 100 股的受控场景
合计取得税前现金 1,348.949 元、柳工转增 30 股，重复登记、支付及股份解锁不重复入账。
它验证候选关联及记账边界，不代表历史策略收益。小天鹅四个不同源报告期均关联同一次
A 股每股 4 元现金，B 股支付日期不适用；
同日换股价格调整公告确认该现金，不授予提前换股或行权。博迈科的差异化参考现金按公告
公布的 0.05 元精度保存，不用自行计算的更多小数替换。原文、集合、判断和执行输入见
[关联核验依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/nonstandard-cash-review-basis.json)及
[数据流程与账户验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/nonstandard-cash-pipeline-result.json)。
进一步核对发现，中国太保的 1.30 元已含 0.10 元特别分红，另一个源候选不能再次加一角；
药明康德则将年度 0.98169 元与特别 0.35 元合为每股 1.33169 元，参考扣减为 1.33017 元。
太保 A 股 6 月 30 日支付不能混用 GDR 的 7 月 7 日。柳工现金零值来自完整原方案仅转增
的显式核验，不能从源空值推断；10 月 26 日可交易另有明确起始交易日依据。东方能源
原文落款年份错误，公开日期按目录记录保存为 2021-11-09，不将落款当作 2020 年已公开。
这些差异、跨报告期关联、普通 A 股范围及验证限制见
[进一步核验依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/related-plan-review-basis.json)、
[分阶段记账验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/related-plan-pipeline-result.json)及
[合计与去重检查](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/related-source-combined-verification.json)。

字段和查询依据：[Tushare 分红送股接口](https://tushare.pro/document/2?doc_id=103)。接口只有
日期，不能提供券商到账微秒、零碎股最终分配、持仓批次红利税、受益人类别或完整修订链。
当前下载结果也不证明历史时点版本未被修订；采集时间不得充当历史公开时间。

<a id="announcement-verification-todo"></a>

#### 公开公告采集、核验与历史覆盖

公告数据生产归 `src/data_system`，继续使用 `dividend/v1`、`corporate_actions/v1`；
账户回放复用既有 Steps，不联网查公告。`anns_d` 权限接入保留为可选 TODO，公开巨潮索引
及发行人 PDF 是当前可执行来源。采集、条款核验、候选归并是三个独立责任：取得公告接口
只解决访问，不能将原文自动等同于有效账户事件，也不将“未核验”误称为“源值错误”。

[Tushare 公告接口](https://tushare.pro/document/2?doc_id=176)需要单独开通，
[权限说明](https://tushare.pro/document/1?doc_id=290)将公告权限与积分权限分开。
2026-10-17 对 `003001.SZ`、2025-06-27 至 07-06 的只读查询被当前配置端点拒绝；
这不能进一步区分官方账户与中转服务的权限。精确参数、脱敏错误、被审计源码摘要、
来源原行与统计范围见[核查证据](/tmp/trading-announcement-gap-20261017-_1hmzbtg/evidence.json)
及[核查笔记](/tmp/trading-announcement-gap-20261017-_1hmzbtg/announcement-review.ipynb)，
临时证据需随既有历史证据保留归档。

初次独立核查使用巨潮公开公告索引及发行人 PDF：固定原回放的 454 个沪深候选，按证券查询
2026-08-20 至 2026-10-17 的公告，取得 8,699 条公司公告索引，保存 1,019 份相关 PDF。
454 个候选均匹配实施公告；443 个完成确定性字段比对，11 个文本提取歧义经全文或渲染
页面复核。普通 A 股股东的税前金额、送转比例、登记、除权、支付和股份上市日期与保存
源值均一致；该次核查没有覆盖完整参考参数，后续流程验收补齐了本批参数，仍不证明全部历史源值正确。

核查包含更正公告：`002986` 的支付日由 09-22 更正为 09-23，`002727` 由 09-23 更正为
09-24，两份更正均在 09-19 披露，保存的源值已经采用更正后的日期。
[宇新更正公告](https://static.cninfo.com.cn/finalpage/2026-09-19/1225572338.PDF)与
[一心堂更正公告](https://static.cninfo.com.cn/finalpage/2026-09-19/1225572157.PDF)说明即使
期末源值正确，生产流程仍需保留更正证据及历史可见时间，不能将最终日期倒写进原公告。
完整的[字段核查](/tmp/trading-dividend-public-audit-20261017-pos0_7fq/verified-entitlements.json)、
[公告索引](/tmp/trading-dividend-public-audit-20261017-pos0_7fq/company-index.json)和
[原文清单](/tmp/trading-dividend-public-audit-20261017-pos0_7fq/pdf-manifest.json)保留来源及摘要；
账户金额及方法限制由[权益对账证据](../offline_workflow_contract.md#corporate-action-impact-evidence)拥有。

这个结果证明公开来源能够覆盖本次候选所需的权益公告，不证明 2018—2026 年全部公司
行动已被收集。查询按自然日覆盖周末，范围早于回测起点并追溯原计划；本次索引的起止日
是具体输入，不能写成所有历史任务的固定回看窗口。公开索引的当前证券映射仅用于定位
公告，不能替换回测的历史股票成员集合。HTTP 成功、空结果或零散搜索命中都不足以证明
覆盖完整；本次还处理了分页数量与实际返回量不一致、跨页重复的问题，以证券、日期切分
及公告 ID 去重核对范围内的唯一记录数。

生产入口为 `src.workflows.corporate_action_data.run_corporate_action_preparation`，要求已提交
日历和 dividend/v1，显式传入登记日范围、公告自然日范围和可选的复核文件。公告范围
包括周末，不能隐式固定为回测区间或某个统一回看天数。查询范围覆盖所有已实施候选的
证券，包括北交所来源；当前证券组织映射仅用于定位来源，不定义历史股票池。
历史代码不能与公告身份对应时保留失败，不能推测代码映射或静默跳过该候选。
沪深历史代码通过可选 `identity_refs` 传入已复核的对应关系 JSON；每项包含现代码、
公告原代码、官方原文 URL 与 SHA-256，JSON 同目录须有 `<SHA-256>.pdf` 原件。
采集在校验原件摘要后保存对应关系和原件，允许索引返回已证明的旧代码；组织 ID 本身
不作为股份连续性的证明。`000043 → 001914`、`000022 → 001872` 均有发行人代码变更、
原投资者持股数量不变的公告依据。`300114 → 302132` 则由
[2025-02-15 代码变更公告](https://static.cninfo.com.cn/finalpage/2025-02-15/1222544408.PDF)
确认；2018-05-15 登记的原实施公告仍使用 `300114`，实际税前每股派 0.05 元。
较晚的代码对应证据只用于识别同一证券，不将变更日期、未来交易状态或分红条款提前
用于账户决策。没有被证据覆盖的其他代码仍拒绝，PDF 原代码不改写。

```python
run_corporate_action_preparation(
    path_manager=pm,
    registration_interval=TrainingDateRange(start="2026-09-01", end="2026-09-30"),
    announcement_interval=TrainingDateRange(start="2026-08-20", end="2026-10-17"),
    review_refs=(Path("/inputs/reviewed-distributions.json"),),
)
```

流程按以下责任组装：

```text
DataPipeline
├─ CollectAnnouncementsStep       沪深公司索引、原始 PDF、查询及采集证据
├─ CollectBseAnnouncementsStep    北交所及历史新三板索引、原文、官方代码对应证据
├─ ExtractDistributionTermsStep   明确条款提取、显式复核输入、具体缺口
└─ ResolveCorporateActionsStep    候选处置、关联事件，发布 corporate_actions/v1

Standard DataPipeline
├─ FactMaterializeStep            dividend/v1 等来源事实
├─ ExtractDistributionTermsStep   消费已提交的本地公告，不隐式发起网络采集
├─ ResolveCorporateActionsStep
└─ Feature / Label Steps

BacktestPipeline
└─ 消费本地 corporate_actions/v1，复用条款更新、登记、除权、派现和红股上市 Steps
```

**采集与完整性。** `CninfoBroker` 使用公开公司索引和 `static.cninfo.com.cn` 原文路径；
每页按 30 条请求，以 totalAnnouncement 和唯一公告 ID 数量验证。跨页重叠时按自然日
二分；单日仍重叠时补查反向排序。索引查询遇到 500/502/503/504 时最多请求三次，
等待分别为 1 秒、2 秒，每次响应均保留；403 等其他状态立即失败。数量变化、同 ID
字段冲突、范围外记录、无法补齐的截断、最终 HTTP/PDF 错误均失败，不提交成功集合。
已实施候选查到空索引不能证明公告不存在，
同样失败；某渠道没有候选时不查询该渠道。保存原始请求参数、响应、数量、来源 URL、
公告 ID、证券身份、源公开日期、独立采集时间、内容 SHA-256。公开日期取索引时间按
Asia/Shanghai 转换；没有经过验证的盘中发布时间不进入回放。PDF 缓存保留首次采集时间，
复用前校验字节摘要。已提交集合的复用同时检查 PDF 与查询证据，不依赖临时核查路径。
条款提取分别核对两个渠道覆盖其负责的全部已实施候选，不能把部分证券采集
冒充整个来源分区的完整集合。渠道尚未采集时，其候选保留 `announcements_not_collected`
及渠道名，其他已有完整证据的候选仍可核验；这些处置不表示整体覆盖已通过。
旧集合缺少某些证券时，仅在来源与公告范围摘要不变、
旧原文及查询证据仍有效的条件下补采缺少证券，再发布完整集合；不把旧的沪深覆盖当成
已经覆盖北交所。
全市场目录单日双向排序仍无法补齐时，按官网公布的深市、沪市、北交所板块拆分；必要时
再拆深主板/创业板、沪主板/科创板。最小板块仍有重叠时，按本次公开索引已返回的
`secCode,orgId` 查询发行人，不能以本地回测股票名单替代。子查询保留源总数和实际观察
记录，来自父范围和子范围的记录可以互补；只把最终合并的唯一 ID 数量等于所请求范围
总数的结果作为完整索引交付。中间板块仍有缺行不能单独当成完整来源；若合并后整家
公司仍遗漏，总数约束仍会阻止成功。例：2018-04-20 的权益分派目录声明 101 条，
单日双向分页只能获得 95 个唯一 ID；板块拆分补齐到 101 条，说明仅按日期拆分仍会漏项。
数量一致也不能抵消已观察到的来源矛盾。三个真实公司窗口分别返回 83/94、38/45、63/64
两种总数；改为覆盖全部自然日、保留每次完整分页后，两轮结果仍分别为 83/83、45/41、
63/64。前一证券即使两轮相同，仍遗漏此前已返回的 11 个公告身份。因此不能取较小集合、
取最大数量，或用“两次相同”消除已经存在的缺口；须取得一致的官方目录，或另以发行人/
交易所完整披露目录逐项交叉核验，保留全部已观察身份和原失败。这里验证的是目录覆盖，
不能据此推断实际分红金额有误，见[原始查询与逐日复核](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/unstable-company-index-comparison.json)。
HTTP 成功和文件类型响应头也不保证原文有效。浦发银行 2023-07-06 的巨潮索引
`1217219068` 指向转股提示公告，但实际返回 HTTP 200、application/pdf，正文却是
“请求资源不存在”的 HTML 错误页。须以实际字节验证并保留失败；发行人官网检索到的
另一原文地址只是补核线索，尚未取得并核对原件时，不能填入该巨潮 ID 的缓存或据此
提交完整集合。原地址响应、摘要及发行人下载的 TLS 失败记录见
[源文件复查](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/1217219068-original-recheck/receipt.json)、
[补核限制](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/1217219068-original-recheck/issuer-failure.json)。

`BseBroker` 使用北交所官网 `disclosureInfoController/infoResult.do` 公司公告索引及
`www.bse.cn/disclosure/` 原文，包含历史新三板披露。按响应实际 `size/totalPages` 翻页，
核对 `totalElements`、唯一原生 `disclosureCode`、证券和公开日期；接口返回 20 条页容量时
不能按调用方假设的 30 条计算页数。零结果不作为已实施候选的成功证据。原始 JSONP
仅拆除明确的 `null(...)` 外壳并解析 JSON，不执行源端脚本。
公司查询不限定 `isNewThree` 和 `xxfcbj`，集合的 `query_scope` 保存实际范围；缺少
全市场范围声明或只查新三板的旧集合必须重新采集。已核实的反例是同惠电子 `833509`
在 2021 年上半年：`isNewThree=1` 返回 0 条，取消该市场限制返回 64 条，其中包含
05-06 实施公告。分页总数一致只能证明所查询范围完整，不能证明查询范围本身正确。
北交所镜像对个别历史 PDF 返回 403/404 时，可读取全国股转系统官方同路径原文；
保存镜像失败回执、实际成功来源 URL 和内容摘要，不改用二手金额推断。其他 HTTP
失败仍中止该分区，不能通过更换来源绕过限流。403 页面明确提示云安全拦截或异常访问
时，保留失败正文并直接中止，不切换镜像；批量运行遇到持续访问拦截应停止该来源，
待服务允许访问后恢复。例：2021-05-19 的
`1621409889_887424.pdf` 在北交所镜像返回 403，而全国股转系统原文正常返回 PDF。

历史证券关联使用[北交所官方代码对照表](https://www.bse.cn/service/code_mapping.html)，
保存 HTML、采集回执和 SHA-256，并把对应关系写入集合的 `security_identities`。
同时查询候选代码和有证据的原代码；PDF 的代码、公告 ID 保持来源原值，条款核验仍
检查原文证券头。新芝生物当前 `920685` 的 2019 年索引为空，按官方对应的 `430685`
可取得[2018 年年度实施公告](https://www.bse.cn/disclosure/2019/2019-05-13/1557737525_765911.pdf)。
芭薇股份则为 `837023 → 920123`，说明按相同尾号推断会关联错误。对照表只用于识别
同一发行人的历史证据，不把其当前上市日期或新代码提前写进历史股票池。
没有官方对应证据的身份仍失败；BSE 原生十六进制及 `manual<数字>` 公告 ID 不伪装为巨潮数字 ID，
事件身份使用 `bse:<disclosureCode>:ordinary_shareholder`。

替换公告集合前，按旧集合摘要归档完整集合、原文及查询证据；归档不参与运行时回退。
多渠道提取同时绑定各集合摘要和代码对应证据。人工复核绑定整组输入，新增渠道或
修订改变该组摘要时须显式重新提交复核，不能凭旧 approved 状态自动沿用。

**条款及异常。** `pypdf` 按锁定依赖解析原始页；规范化排版不改动业务数值，每十股转每股
使用 Decimal。多页文本仅在各页首行或各页末行依次为独立的 1、2、… 或对应的
1/N、2/N、… 且 N 等于总页数、第一页正文以证券/股票/A 股代码开头时删除该组页码；
正文数字、单个未确认的边缘数字均保留。北陆药业
2018 年实施公告的“2018 年 4 月 16 日”跨页，原提取会夹入第二页页码而变成“42 月”；
这属于排版问题，不能修改源日期或凭另一日期猜测。保证声明插入股东范围或日期中时，
仅去除已确认的董事会或监事会固定声明原句；后续页首重复出现与首页完全一致、同时包含证券代码、
简称及公告编号的页眉时，去除重复页眉并保留首页身份，不扩大普通股东范围。盾安化工
2018 年公告的除息日期被保证声明打断，新美星的“全体股东”被声明和重复证券页眉隔开，
均须先恢复原文连贯性，不能用源数据日期替代公告核验。各页首行完全一致的公司名称
或“简称＋年份＋年公告”也可按该排版证据移除，再校验连续独立页码；苏奥传感和红日
药业的 2018 年公告分别因此拆断除息日期和“登记在册”。原文身份检查寻找首页明确的
证券/股票/A 股代码字段，不将任意正文金额当代码；阳谷华泰的 PDF 提取顺序把该字段放在
首页正文后方，不能因超过固定字符位置就断言证券不符。“证券号码”同样须精确匹配证券，
不能退化成正文任意数字匹配。独立页脚完整包含该证券代码、简称及公告编号时，仅从
拼接正文中去除该页脚，原始页仍用于身份核验。小熊电器
[2019 年度实施公告](https://static.cninfo.com.cn/finalpage/2020-06-03/1207888992.PDF)
在“每”和“10 股转增 3 股”之间插入了保证声明和证券页脚；旧提取错误地批准零送转，
实际应为每股派现 1 元、转增 0.3 股，2020-06-09 登记，次日除权、派现及新增股份上市。
东莞控股 2026 年中期公告的 `2 / 3` 页码插在 `1.500000` 和“元”之间；中国中铁同期
公告使用“A 股代码”。九月真实输入曾检出这两种版式回归，补齐后重新逐候选验证。
自动批准限定于证券、报告年度及相容期间、登记日、普通股东范围、税前金额、送转、
结算日期及除权参考参数均能唯一解释的实施公告。对全股本等比例分配，须有完整分配基数
或明确的非差异化声明；差异化方案按公告参考公式取值。实收金额和参考价参数仍分开。
同一实施公告的 A 股每股现金红利摘要与实施条款不一致时，保留金额冲突；未重复标注
“A 股”、但明确列于“每股分配比例”下的普通股东现金摘要也须核对。不能只因其中
一句写了“含税”就忽略另一处金额，也不能把首页的总股本折算参考值当成实收摘要。
例如领益智造、崇德科技的 2026 年中期公告均在首页解释除息折算，实际现金与参考值
不同是已明确的差异化分红，不构成金额冲突。相反，舒华体育
[2023 年度实施公告](https://static.cninfo.com.cn/finalpage/2024-04-26/1219837422.PDF)
首页为每股 0.30 元，第二页差异化方案为每股 0.25 元，参考公式却使用 0.30 元。
原始图像确认并非文字提取误差；现有提取曾错误批准 0.25 元，修正后进入待核验，
不按出现次数选择金额，也不以 Tushare 的 0.30 元覆盖冲突。除权参考值 0.2974 元是
另一个字段，本身不要求与实收金额相等。原文摘要、图像及修正前后结果见
[金额冲突回归证据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/cash-consistency-original-regression.json)。
鸿路钢构[2022 年度实施公告](https://static.cninfo.com.cn/finalpage/2023-06-07/1217001730.PDF)
的实际每股现金公式写为 0.259999 元，实施条款却为每十股 2.599999 元，即每股
0.2599999 元；不能用容差或来源值掩盖这项原文精度冲突。它与明确调整旧方案不同：
格科微 2023 年度、广钢气体 2024 年中期及精智达 2024 年度原文分别确认实际每股
0.00603、0.04201、0.31910 元，参考扣减分别为 0.00597、0.04200、0.31788 元。
自动提取无法解释替代关系时先拒绝，再由显式核验发布当前条款；精智达的中间调整
0.32351 元不另产生权益。四份完整已存分区、调整依据、拒绝错误批准及重复发布验证见
[原文与发布核对](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/cash-summary-originals-result.json)。
同类原文核验另确认 25 项现金条款，逐项区分原预案、实际派发金额和除权参考扣减；
例如上海银行本次末期每股 0.22 元不能替换成同文已派中期的 0.30 元，`688129`
2023 年度按原文最终每股 0.20053 元执行，不能继续采用此前每十股金额换算的
0.200532 元。`002332` 2026 年中期另因“含税”跨页采用显式核验，每股 0.1 元及参考
扣减 0.0981851 元均不改变。26 项的原文、替代关系及阶段日期见
[逐项依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/reviewed-cash-adjustment-basis.json)。
26 个登记分区经数据流程、重复发布和受控账户验证；各持 100 股时税前现金合计
808.928 元，重复登记、支付不会重复到账。这是金额及阶段验证，不是历史交易收益。
同分区其余变化只有 5 项 Decimal 尾零格式及 2 项未解决原因细化，没有其他已批准
权益被静默改变，见
[发布和账户验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/reviewed-cash-adjustment-pipeline-result.json)、
[其他变化审计](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/reviewed-cash-adjustment-other-changes.json)。
同时派现和转增的差异化方案，明确除权公式中的现金扣减与股份变动比例须成对读取，
不能只识别其中现金项后因存在送转而拒绝，也不能用实际到账比例填补参考参数。
三花智控[2018 年度实施公告](https://static.cninfo.com.cn/finalpage/2019-04-30/1206162504.PDF)
将参考式写为 `(股权登记日收盘价-0.2483639)÷(1+0.2980367)`。解析限定于明确标注的
除权公式；同文公式或现金参考值冲突仍待核验，邻近其他交易价格公式不作为除权依据。
“股份总额”“公司现有股本总数／总额”和以总股本“为基准”也是完整股本的来源表述；
总股本使用“万股”或省略紧随数字的“股”，不改变该明确基数的含义，也不改变每十股
分配的单位。已确认普通股东范围后，完整基数与“每十股”的语序不改变含义。基数后注明
已扣减、剔除或不含回购股份，或另有不参与分配的限制性股份等例外时，不能假定参考参数
等于实际到账比例，须继续核实差异化口径。中兴商业、广聚能源、现代投资、华润三九、
德威新材及华闻传媒六个真实登记分区的完整集合验证见
[明确总股本回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/full-capital-wording-originals-result.json)。
自动规则因完整实施段而收紧后，不能把先前的批准直接视为恢复依据。中海达 2018 年度
实施段漏写“总股本”的“本”，雷电微力 2021 年度的完整基数被声明与页码打断，均由
完整原文的总股本、股本未变及方案一致声明显式确认。设研院 2021 年度明确回购股份已
注销、回购账户无库存，可转债转股只改变登记日总股本，现金和转增的每股比例不变；
不能把已注销股份当作仍在股本内但不参与分配的股份。日照港 2025 年度实施金额为
每股 0.0395 元，同文 0.033 元属于已经支付的中期红利。这四项恢复均保持先前经原文
支持的账户条款，各持有 100 股时合计税前现金 50.55 元、增加 150 股；全部分区重复
执行及其他业务事实检查通过。原文、公开日期与具体限制见
[完整原文恢复依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/bounded-original-recovery-review-basis.json)、
[分区及账户验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/bounded-original-recovery-pipeline-result.json)。
623 份参考参数缺口原文的单篇比较中，48 份可完整提取，另 1 份转为明确的支付日期
时序缺口；这不是 48 项完整修订链核验通过。48 份与来源现金、股份的比较仅有中兴商业
2019 年半年度现金为空与核验为零的差异，原文明确“不进行现金分红”，并非自动补零。
逐项输入、结果及限制见
[单篇比较](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/full-capital-original-comparison.json)、
[源值核对](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/full-capital-source-comparison.json)。
送红股和资本公积转增分别核验，最终到账股份为两项之和；同一项重复表述不重复记账，
同一项存在不同金额仍须复核。例如陕西金叶
[2017 年度实施公告](https://static.cninfo.com.cn/finalpage/2018-05-03/1204892261.PDF)
规定每十股送 1 股、转增 4 股，合计每股新增 0.5 股，不能把两个分配组成当成冲突，
也不能在两项比例相同时只保留一次。
[精测电子 2017 年度实施公告](https://static.cninfo.com.cn/finalpage/2018-05-03/1204906041.PDF)
的每十股送 5 股、转增 5 股，合计每股新增 1 股；旧提取只计 0.5 股。现金条款在前、
“送红股”沿用同一句每十股单位时，也不能因后半句没有重复单位而漏掉送股。
智飞生物[2022 年度实施公告](https://static.cninfo.com.cn/finalpage/2023-06-09/1217028943.PDF)
明确每十股派税前现金 5 元、送红股 4 股、另转增 1 股，合计每股现金 0.5 元及新增股份
0.5 股；“元人民币现金（含税）”和“派发现金红利…元（含税）”都不能切断后续送股
沿用的单位。完整历史重算中的阶段交叉检查发现旧提取只计转增 0.1 股，因此该旧快照
不能作为当前已核验结论；失败输入及原值见
[有范围声明的阶段审计](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/capital-component-bounded-audit-result.json)。
修正后由自动解析重新处理完整原登记分区，不向智飞生物提供人工核验替代解析；100 股
受控账户一次取得税前现金 50 元和新增股份 50 股，重复执行不重复登记或支付，同分区
其他业务事实不变，见
[真实原文及账户回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/shared-unit-original-pipeline-result.json)、
[分区变化核对](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/shared-unit-original-other-changes.json)。
实施段遗漏送股或转增比例，而同文其他段仍有该项正分配时，不得将缺项解析为零；
必须确认实施条款或明确取消该项的声明。“不送红股”只否定送股，不能据此取消转增。
同文列举往年送转以计算最低减持价格，也不能当成本期分配。自动提取无法可靠区分时，
保留缺项并显式核验当前方案：新宝、众信、宗申及中新赛克的九个案例逐项确认了本期
完整现金方案、登记日、支付日和全股本口径，历史送转属于其他年度，原本期金额与股份
不变。八个登记分区经发布和重复运行验证通过，依据与输入分别见
[报告期复核](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/share-scope-review-basis.json)、
[发布验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/share-scope-pipeline-result.json)。
实施方案明确位于第一节或第二节时，以该节核验执行金额，不把此前股东大会的旧股本
预案金额混入当前方案。“利润分配及资本公积金转增股本方案”同样是明确的实施节标题。
“以资本公积金每十股送”描述资本公积转增，不能再增加一笔送红股。魅视科技
[2025 年度实施公告](https://static.cninfo.com.cn/finalpage/2026-06-02/1225342162.PDF)
前段如此描述每十股送四股，实施段明确每十股转增四股且不送红股；到账比例是 0.4，
不是两段相加后的 0.8。现金金额仍须有明确税前依据；“人民币现金红利”和方括号内
“含税”不改变税务口径。明确的“税前”与“含税”采用同一税前口径；“扣税后”、
“税后”或“税前金额另行确定”不能被规范化成确定的税前金额。银行公告的“A 股每股
现金分红／现金红利人民币”摘要仍须与实施金额一致。中信银行与光大银行 2018 年原文
分别明确每股税前 0.261 元和 0.181 元，完整集合的提取回归见
[税前原文验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/gross-tax-originals-result.json)。登记日的括号
日期、“权益分派登记日”及“A 股股东红利将于”的现金支付表述按相同阶段解释。
金额前带“某年末期／中期”不能导致该金额被漏读，再退回同文其他阶段的已派红利。
上海银行[2025 年度实施公告](https://static.cninfo.com.cn/finalpage/2026-06-02/1225343745.PDF)
本次末期为每股税前 0.22 元，0.30 元是已经派发的中期红利，不能再计入本次事件。
连续页码必须在确定版式后从正文拼接中去除：金智科技
[2021 年度实施公告](https://static.cninfo.com.cn/finalpage/2022-06-22/1213777242.PDF)
第一页以“0.25”结束，第二页相同证券页眉后的页码“2”曾被拼成“0.252”；真实金额
仍为每十股 0.25 元。仅在所有页面的相同证券页眉下确认完整连续页码时删除该页码，
不删除无法证明身份的数字。原文及修正后的逐篇提取见
[来源差异原文](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/current-source-difference-originals.json)，
该逐篇结果仍须经完整分区发布与账户验证，不能直接替代这些验收。
四日期表中的“除权除息日”“新增可流通股份上市日”“红利发放日”分别对应除权、
新增股份上市和现金到账；必须按实际列序读取，不把两类结算日期合并。
明确的“方案调整为”“调整后的方案”“分派变更如下”或因股本变动而声明“因此，公司应以
现有总股本……”为当前实施条款；若其金额仍无法解析，须保留
缺口，不能退回旧预案。道氏技术 2018 年度公告原图的金额和“元”顺序异常，由原文
复核确认每股 0.1499971 元；中国电建 2018 年度实施公告确认实际及参考现金均为
0.09782 元。泰凌微 2023 年度分配则为实际 0.07323 元、参考 0.07246 元，不能沿用
旧预案 0.073 元。各类值仍依据该次公告，不将当前通用公式反推成历史原文。
对阶段结果中已标为 resolved、但与来源金额或股份不一致的 27 个候选逐份核对后，
12 个修正漏读或合并错误的送转组成，4 个修正误用的预案金额，10 个是来源现金为空而
公告已独立确认无现金分配。另 1 个中交地产 2018-06-21 候选虽已确认每股送 0.5 股，
仍缺可确认的新增股份上市日，改为 unresolved；不能继续以漏计送股后的零股份方案通过。
完整范围、原失败、原文、逐项前后条款及剩余缺口见
[差异复核](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/resolved-difference-review-result.json)。
本批 17 份已保存原文中，修复前 1 份、修复后 11 份可完整提取；其余仍保留具体缺口，
不将单份文本提取通过视为完整修订链通过。原文、来源与代码摘要见
[逐文比较](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/historical-wording-actual-result.json)，
随后针对遗漏送转、旧预案、九月版式、日期表、证券页脚、同文金额冲突、成对除权参考
公式、正文期间、税前同义表述、缺失送转、未重复登记日的更正关联、明确总股本写法及
结算日期标签继续补齐可复现失败。独立公告来源和精确公开时刻修订扩充后，
两个真实事件和四个受控账户场景验证通过，见
[事件及账户验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/independent-precise-feature-verification.json)。
随后补齐无分隔符的完整上市日期句子、现金摘要边界、末期及资本公积方案和证券页眉下
连续页码；资本公积“送股”归入转增组成，同一组成的同义表述不重复累加，也不能在
没有另一处“转增”措辞时漏掉该组成。补齐上述现金句后共享单位的送红股回归后，
`frozen-shared-unit-code.json` 冻结源码的数据流程、账户、回放及相关调用边界共
1,330 项测试通过。范围为 tests/data_system、tests/access、tests/trading、tests/workflows
和 tests/utils，不是全仓库测试声明；两种遗漏送股写法修复前均复现失败，见
[源码、测试范围及原失败记录](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/shared-unit-code-verification.json)。
另 16 份日期、身份及金额样本中，5 份由不完整转为完整提取，前述小熊电器修正漏记
送转；其余仍保留具体缺口。原文摘要及逐项结果见
[修复前](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/phase-reference-originals-before.json)、
[修复后](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/phase-reference-originals-after.json)，
这仍是选定原文的解析证据，不替代完整集合修订审计。
此前九月 477 个候选与增量样本 96 个候选的版式回归，与各自阶段输入的业务事实按 Decimal 精确相等，
其中 5 项仅有数字尾零的序列化差异，逐项保留；重复执行的摘要和修改时间不变。
[真实输入回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/verified-layout-regression-result.json)
保留初次回归失败及修复后的输入、代码和结果，不用数值容差掩盖金额差异。阶段处置
一致不能替代与九月已核验账户的比较；扫描件检查引起的核验缺口还须按下述显式复核处理。
补齐扫描件复核后，后续日期表及证券页脚修正对同一 23 个分区重新验证，477 个九月
候选及 96 个增量样本候选的完整处置、摘要和修改时间均保持一致，见
[完整输入的阶段回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/phase-footer-regression-result.json)。
两组含重复候选，不能将 573 次比较当作新增核验事件数；对应账户重跑由
[回放验收](../offline_workflow_contract.md#september-coverage-validation)记录。
同文金额冲突、成对参考公式、正文期间、税前表述、更正关联及总股本写法修正分别对这 23 个分区验证，完整处置仍不变，见
[金额一致性回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/cash-consistent-regression-result.json)、
[成对参考公式回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/paired-reference-regression-result.json)、
[正文期间识别回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/body-period-regression-result.json)、
[税前表述回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/gross-tax-regression-result.json)、
[更正关联回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/revision-association-regression-result.json)、
[总股本写法回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/full-capital-wording-regression-result.json)；
这不批准包含舒华体育冲突的其他历史输入。
重整、缺少明确零值、A/B 表格歧义、阶段日期变更、独立调整/更正/取消通知及非标准身份
交给显式复核，不以置信度、源金额或最近公告兜底。自动核验保留不同公开日期的金额版本；
同日相互冲突不推测先后。仅调整回购或转股价格的公告不会直接变成分红条款修订。
同一证券、同一报告期间的实施公告更正，即使只修改金额或到账日、没有重述登记日，
也须进入修订核验；不能以缺少登记日命中为由忽略。其他报告期的实施更正或仅同期间
的年报更正，不能只凭标题关联。骏成科技 2024-05-18 的
[更正公告](https://static.cninfo.com.cn/finalpage/2024-05-18/1220090152.PDF)
明确补入每十股转增四股，原实施段未重复该比例；新野纺织 2021-08-21 的
[更正公告](https://static.cninfo.com.cn/finalpage/2021-08-21/1210808018.PDF)
明确将现金到账日从 08-27 改为 08-26。两份文件此前均已采集，旧关联逻辑却遗漏了
它们，分别导致零送转和错误到账日。修正后先拒绝自动批准，再按原文及明确替代关系
显式核验：骏成每股现金和转增均为 0.4，新野每股现金 0.023、08-26 到账。新野完整
修正条款的可见日记为 08-21，早于 08-25 登记，不把修正后的到账日倒写到 08-19。
原错误、完整集合、修正条款及重复执行证据见
[遗漏更正回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/omitted-revision-originals-result.json)。
扩大核对后，原已自动批准候选中共有 16 份此类未关联更正：12 份不改变本账户的税前
分配及参考条款，2 份分别涉及前述送转和到账日，另 2 份改变除权参考值。红豆股份
2020-06-08 更正把参考扣减从 0.05 改为 0.0471，实际税前红利仍为 0.05；须保留
06-06 原条款与 06-08 修订条款，不能把修订提前。精华制药 2021-06-30 更正把参考
扣减从 0.399430 改为 0.039943，实际税前红利仍为 0.041。其巨潮原始索引时刻为
北京时间 07:41:10，独立重查完整复现；该事实修正了最初“缺少精确公开时刻”的解释，
因此按已采用的精确公开时刻规则处理。账户仍在除权日 00:00 确认权益，07:41:10 才暴露
更正；仅在原临时参考价尚未被真实行情或其他事件替代时，从原含权基价重新计算。
不能把 07:41 的更正回填到 00:00，也不能重复确认权益。现金、股份或已执行阶段日期
发生变化的晚修订不属于这一授权范围，继续保留未解决。
具体原文分类见
[更正核验依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/explicit-revision-review-basis.json)，
精确公开时刻及修正前的能力限制见
[时序核查](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/002349-reference-revision-limitation.json)。
新增时刻语义已通过真实完整集合重新采集、V1 提取和 Access 读取验证：精华保留两次公开
条款，慕思的独立来源不增加任何 dividend 行；两次重复提取均复用相同输出摘要和修改时间。
证据见[两项真实事件验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/independent-precise-case-result.json)。
另外 13 项显式核验经完整集合提取、V1 解析及重复运行验证通过：12 项税前条款保持原值，
红豆保留两次公开版本；精华在该次旧代码验证中仍为 unresolved，不能将此证据误作新增
精确时刻行为的验证。每个分区的其他变化和
输入摘要一并保存，见[更正发布验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/explicit-revision-pipeline-result.json)。
零值可由同一计划的另一份已公开原文确认。例如瀚叶股份 2017 年转增实施公告明确
每股转增 0.3 股，但没有单列现金金额；此前
[2018-03-01 说明会公告](https://static.cninfo.com.cn/finalpage/2018-03-01/1204441763.PDF)
明确解释该年度未进行现金分配。两份原文共同支持现金为 0 的显式核验，而不是将缺失
字段补零。实施表同时区分 3 月 26 日除权和 3 月 27 日新股上市，不能用同一天替代两者。
对后续完整候选快照中 32 项“来源现金为空、核验现金为零”的差异，逐项回到已保存的
实施原文检查零现金条款，均找到明确“不派现金”或“每十股现金 0 元”的表述。
其中人人乐 2018 年度、因赛集团 2024 年度、奥雅股份 2025 年度及亨迪药业
2025 年半年度的实施节只重述转增；同文前段明确零现金，并明确实施方案与审议方案一致。
这四项须结合完整公告显式核验，不能让自动提取任意回退到旧预案金额。亨迪药业后来
修改注册资本的公告只作实施结果审计，不提前用于历史权益登记。逐项原文摘要、零值
条款和四项完整方案依据见
[零现金差异复核](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/null-cash-review-basis.json)。
该核查仅回答这 32 项零现金的来源依据，不替代所有条款、完整修订链或源事件覆盖验收。
同一实施公告明确写明“按最新股本计算的……方案为”时，取该段实际实施金额，保留前段
旧预案原文；中来股份 2018 年公告同时出现每十股 5 元和调整后的 5.010571 元，实际税前
每股是 0.5010571 元，不能平均或任取其一。没有明确替代关系的多金额仍为歧义。
“每十股送红股 3 股，派 0.8 元”共用每十股单位；“派发人民币现金红利”的措辞差异
不改变金额和税务口径，扣税后数字继续排除。“每股 A 股派发现金红利”和金额后紧接
“。（含税）”仍是明确税前金额；“股息将于”“现金红利于”及“本次红利委托中国结算深圳
分公司将于”明确的划款日也保留实际日期，不默认等于除息日。“现金股利将于”和
“红利发放日”同样提供明确支付日期；“新增可流通股上市日”“新增股份上市日”提供上市
日期。“新增可流通股份上市日（红利发放日）”明确共用一个日期，须与正文分别列出的
支付日期一致，冲突仍待核实。“直接记入证券账户”的日期本身不证明新增股份已可交易。
一般文字标签须有“为”或冒号界定日期，表格则按保留的行列读取；不能把去除换行后紧接
“现金红利发放日”表头的第一列登记日期当成到账日。完整句子明确写出“本次所送（转）的
无限售〔条件〕流通股的起始交易日”并以句号结束时，日期前没有“为”或冒号仍可确定
该句的上市日期；没有句号的表头不能沿用这一规则。捷捷微电 2018、2019 年度及力源信息
2018 年度原公告均使用这种句子。全历史对比发现过度收紧标签后这三项由已核验退回
未解决；修复后分别读取其完整发行人公告集合，恢复的全部条款与原核验完全一致，见
[原文回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/subject-listing-originals-result.json)。
`688578` 2026 年半年度公告的三列依次为 9 月 1 日登记、9 月 2 日除息与支付，
首次扩展文字规则触发过表头误配；新增完整主语句子的规则继续保留这一回归检查。
[失败结果](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/explicit-settlement-regression-result.json)
保留原输入和错误处置；补齐标签边界后，同一九月 477 个候选和增量 96 个候选的完整
处置均未改变，重复运行摘要及修改时间一致，见
[边界修复回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/settlement-label-boundary-regression-result.json)。
303 份日期缺口原文的前后比较中，24 份由这些明确措辞补齐结算日期；其完整已保存公告
集合重新提取后，20 个候选可完整核验，4 个仍缺除权参考依据。20 个候选的金额、股份、
登记、除权、支付和上市字段与全部对应源行一致；来源一致仅是交叉检查，不能代替公告
证据。具体原文、字段、重复执行及未解决原因见
[真实日期原文验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/settlement-label-boundary-originals-result.json)。
A、B 股表格行被文本提取合并时，仅在行首明确为 A 股、随后
明确出现 B 股边界的情况下截取 A 股段；例如市北高新 2018 年 A 股登记为 6 月 1 日、
到账为 6 月 4 日，不能使用同一文本行中 B 股的 6 月 6 日或 6 月 15 日。
早期公告的通用“季度”标题可以结合同年及精确登记日定位，不能改配明确写了另一季度
或另一年度的公告；同一公告对应多个来源报告期时仍进入关联计划复核。例：拉夏贝尔
2017 年季度公告的实际登记日为 2018-01-02，通用标题并不表示没有实施事实。
实施公告标题不带年份时，正文中明确的“年度／半年度等期间＋权益分派、利润分配或
分红派息方案”也可以确定所属报告期；识别到的方案期间必须全部与候选相容。标题已写
其他年份或期间、正文引用多个不同方案期间、只有股东会日期而没有明确方案期间时，
仍不匹配。双鹭药业与景兴纸业的 2018 年实施公告标题均未写年度，正文则分别明确
2017 年度权益分派方案，不能将标题格式缺失解释成公告未采集，也不能从来源报告期
反向补造原文含义。`002038`、`002067`、`300540`、`000690` 四个真实候选分别以完整
已存公告集合重新提取，均从 `missing_implementation` 转为已核验；逐项金额、股份、
原始输入和同分区其他变化见
[正文期间原文回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/body-period-originals-result.json)。
该四例的通过不替代全历史重新提取。
修订文件中的同一日期可能属于另一事项，不能据此认定原分红方案已变更。例如东方市场
2018 年重组报告中的 `2018.04.18` 是贷款及担保到期日；创新股份修订报告则复述已完成
的分红与转增。此类命中经原实施公告和修订原文分别复核后，显式核验沿用原实施条款，
保留两份证据及排除理由；不把报告后来的公开日期用于回填先前账户可见信息。
“权益登记日”与“股权登记日”是同一阶段字段的来源表述；北交所公告仍须独立通过
证券身份、普通股东范围、实际税前金额、结算日和除权参考参数核验，不能仅凭市场代码批准。
历史新三板标准模板有时不直接写“含税”，而在全体股东派现金额后明确个人股东、投资基金
适用差别化个人所得税，再列 QFII 实付及个人补缴金额。核验取该明确税务结构中的原始
派现金额，不取 QFII 扣税后金额；缺少该说明的裸金额仍不批准。新芝生物上述公告的
每十股派 2.80 元、QFII 实收 2.52 元，对应本账户税前每股 0.28 元。

`review_refs` 是按上述模型提供的完整复核结果；原文 URL 与 SHA-256 必须已在公告集合中。
来源候选摘要、受益范围、金额、阶段日和关联关系在发布前整体验证。输入保存在
`raw/announcements/corporate_action_review_inputs`，同时用 `collection.sha256` 绑定本次
核查的整个公告集合；集合变化后不得自动沿用旧复核，须重新提供对应核验。已有显式
reviews 不能被新提取器静默覆盖，必须先作为 review_refs 明确导入。没有登记日持仓是
某次账户的结果，不是 `not_entitled` 的含义；本次 14 个零持仓候选仍保留真实条款。

**发布与重跑。** 原始公告集合、显式复核和生成 reviews 分开保存；具体 raw 路径由
[存储 owner](storage_layout.md)定义。`extraction.json` 的 inputs、producer_sha256、
reviews_sha256、issues 共同绑定精确输入、代码/解析依赖、结果和逐候选未解决原因。
缺少本地集合时 Standard 流程报告未采集并继续解析已有显式核验；损坏、摘要变化或过期
复核不降级为成功。公开 PDF 可能采用 AES 编码且无需打开密码；解析环境使用
`pypdf[crypto]`，按[官方依赖说明](https://pypdf.readthedocs.io/en/stable/user/installation.html)
提供相应组件，提取缓存同时绑定 `pypdf` 和 `cryptography` 版本。缺少运行依赖属于环境
失败，不能当成发行人没有分配。长园集团 `1204975071` 原文曾因此使 2018-07-11 整个
分区中断；补齐依赖后读取了同一摘要的 23 页，并重新产出 45 个 resolved 和 6 个
unresolved，见[真实失败与修复证据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/aes-actual-announcement-result.json)。
已通过原文字节核验但 `pypdf` 无法解码或没有可提取文字时，该发行人
涉及的候选保留 `pdf_text_unreadable`，记录来源、公告 ID 及错误类别；不将错误文本当作
空白无事件，也不阻断其他发行人的核验。已有完整人工核验仍须通过来源与整个公告集合
绑定，才能明确处理该候选。2018-06-21 集合中中天能源的核查文件 `1204921556` 触发
UTF-16 解码错误，即属于原文需另行阅读的情形。九月的威高血净 `1225562518`、确成股份
`1225577538` 为扫描法律意见；启用不可读检查后，两候选从自动通过转为待复核，不能
沿用先前机器批准状态。逐页检查全部 8 页和 7 页后，前者只调整分红后购买资产的发行
价格和数量，后者确认实施公告的差异化派现及参考参数，均未改变原分红条款。新增
显式复核绑定两份原文及原实施公告；威高 9 月 15 日法律意见仅用于后续修订审计，不
回填 9 月 8 日实施条款的可见时间；确成采用 9 月 23 日公开日，不采用律师 9 月 7 日
签署日。两笔对应九月账户税前现金 42 元和 10 元，完整原文渲染、集合摘要、复核输入及
失败重跑见[复核依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/complete-reviewed-september-preparation.json)。
缺口区分原文未匹配、受益范围不明、条款/日期/参考价不完整、独立修订、
跨候选关系待核实及未支持交易所。同输入复用制品；新截止日用于扩展公告范围，修订影响
的登记日分区重新核验和发布。该入口不自动安排定时任务。旧回放仍绑定旧输入摘要；
输入改变后需重跑，原始 Tushare dividend 不被公告修正覆盖。

**月度和关联事件验收。** 2026-10-17 对上述 454 个沪深候选补齐可执行条款，420 项通过
确定性提取，34 项经过原文例外及独立调整通知复核；生成 454 个 resolved，另外 23 个
北交所候选保留 `unsupported_announcement_exchange`。核验输入中，`300980` 的每股
实得转增为 0.4，除权参考比例为 0.3901769；`002605` 的参考现金由 0.1994343 在
09-08 修订为 0.1994344，版本按公开日期保留，税前每股派现始终为 0.2。
金额、日期等已有独立核查结果不是被覆盖的基线；新增原文证据和修订用于解释完整路径。
账户回放及独立对账由[回放验收](../offline_workflow_contract.md#corporate-action-impact-evidence)记录。

跨报告期的历史线索仍有 51 个关联集合、106 个候选、113 条源记录，不能与重复组统计相加。
其中江铃 `000550.SZ` 的 2017-09-30、2018-01-16 两个候选指向同一
[2018-03-20 实施公告](https://static.cninfo.com.cn/finalpage/2018-03-20/1204488117.PDF)：
A 股 03-26 登记、03-27 支付每股税前 2.317 元。现有 related_sources 表达该关联；真实
历史回放的 100 股只登记一次、到账 231.7 元，保留两份来源处置，不混用 B 股日期或港币金额。
数据、源码、原文、失败记录、输入/输出摘要和重复执行结果见
[公开公告流程证据](/tmp/trading-announcement-workflow-20261017-i1ony73e/evidence.json)。
后续又以完整原文确认了玲珑轮胎、祁连山、迈瑞医疗两次合并支付、兔宝宝及江波龙的
跨期关系：年度、季度、特别分红组成只按实施公告合计一次，不按候选分别支付。
泰嘉股份的差额则来自已注销股份；已从总股本注销的股份不等于仍在股本内但不参与分配
的回购股份，不能误判为缺少差异化参考价。上述七个事件的 13 个候选、14 条源行经完整
数据流程与受控账户验证，一次性登记 100 股时合计税前现金为 563.78054 元；该精确乘积
不包含券商到分舍尾，见[发布和记账验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/new-cash-bindings-pipeline-result.json)。
鸿博股份 2017 年度实施公告另明确“目前公司不存在”已回购未注销股份，通用条款提到
回购股不参与不能覆盖这一具体事实。完整原文确认按全部现有股本每股派 0.06 元；之后
的减持、质押和回购数量更正不修改本次分配。以显式复核发布并验证 100 股一次取得 6 元，
见[原文依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/absent-exclusion-review-basis.json)、
[数据及账户验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/absent-exclusion-pipeline-result.json)；
不据这一个案例推广为任意否定句自动排除差异化分配。
另一批九份原实施公告将 19 个候选、20 条源行关联为九次人民币现金支付。兖矿能源年度
源行的每股 2 元已经包含特别股息 0.4 元，不能再加另一候选的 0.4 元；中国海油的
1.18 港元是宣派口径，A 股实施金额为人民币 1.00699 元，不能把港元直接入账或重新
计算已由公告确定的折算值。大名城三个报告期候选均指向同一次临时分配，B 股美元金额
和到账日期不适用于 A 股。完整原文、候选关系及各 100 股合计 493.599 元税前现金的一次
入账验证见[关联方案依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/remaining-cash-plans-review-basis.json)、
[发布及账户验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/remaining-cash-plans-pipeline-result.json)。
后续四份完整实施公告将 8 个候选关联为四次事件，进一步表明合并规则不能统一为求和
或取任一源值：兖矿能源 2023 年实施的是年度每股 3.07 元加特别现金 1.23 元，另送
0.5 股；青岛啤酒年度源值 1.80 元已经包含特别现金 0.50 元，不能再加一次。美凯龙
两条源现金均为 0.046 元，但原实施公告及同日核查意见确认年度 0.034 元加特别现金
0.046 元，实得共 0.08 元，A 股除息参考扣减为 0.07998 元；后续章程修订不改变
该分配。赛维时代两种来源报告期指向同一次每股 0.25 元的特别分红。各持有 100 股
合计税前现金 643 元、增加 50 股，登记、支付及股份释放重复执行不重复入账。完整
输入、公告身份和公开日期见
[四组原文关联依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/completed-middle-cash-plans-review-basis.json)、
[完整分区及记账验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/completed-middle-cash-plans-pipeline-result.json)。
同分区其他 103 项处置变化中，87 项由未解决转为可解析，16 项细化未解决原因；没有
其他此前已批准的业务事实变化，见
[分区变化复核](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/completed-middle-cash-plans-other-changes.json)。
这些受控记账不使用市场价格，不替代新解析条款的全范围交叉检查，也不表示全历史
候选均已核验。

**历史覆盖与增量维护。** 2018-01-02 至 2026-09-30 的源分区和原有候选处置均已生成，
这只说明对象齐全，不能证明源事件完整或全历史公告已核验；本次月度结果与一个跨候选
反例不能自动批准其余候选。
上述 22 个登记日发布后，全历史 33,116 个候选的处置为 460 个 resolved、1 个
not_entitled、32,655 个 unresolved；resolved 中江铃两组来源属于同一个事件，不是两次派息。
剩余 unresolved 包含沪市 14,443、深市 17,038、北交所 1,174 个候选，该统计绑定上述
流程证据的精确历史输入，不用本次月度验收掩盖全历史未完成的工作。
2026-10-18 按代码 `1c0db6f` 读取全部 2,123 个正式登记日分区，以上统计仍成立。其中
32,632 个 unresolved 所在登记日没有已提交的公告采集分区；另外 23 个为已采集九月
分区中的北交所候选。该分类不能表述成全部候选已联网核验失败。源理由中的普通待核验、
重复、差异和非标准方案是处理线索，不能在尚未取得原文时推算自动核验通过率。

历史执行按登记日分区提交、按明确日期范围分批，覆盖全部实施来源；逐候选保留结果，
优先级不得缩小最终范围。公告窗口从原计划追溯至必要的更正和实施阶段，不能统一使用
登记日前固定天数。已成功分区复用，失败分区记录原始错误并补齐；不得提交截断集合。
历史证券查找须覆盖退市、更名、代码变更及北交所来源，当前证券映射缺失不得变成跳过。
普通方案确定性提取，扫描件、表格歧义及特殊方案通过原文复核；每项核验均绑定来源。
同一公告关联多个候选时仍按 action_id 一次执行。阶段日实际变化、除权后现金或股份更正、
缺少可靠公开先后的同日冲突、特殊受益人或非普通参考公式等能力须有明确事实与账户语义后再实现；不能用
最终条款回填以前时点。北交所核验必须单独验证来源与历史证券身份，不能删除未支持项。

普通股东没有现金和股份，不一定意味着参考价不变。以下七笔原文已明确最终参考价，
但使用重整专用公式，仍不属于当前普通分红参考参数可执行范围。表中价格均是公告披露值，
不是从回放收益反推的参数；价格差不能伪装成现金红利，也不能给账户增加公司总转增股份。

| 证券 | 登记日 | 公告采用的前收盘价 | 最终参考价 | 普通股东新增股份 |
| --- | --- | --- | --- | --- |
| 000615 奥园美谷 | 2025-12-26 | 4.18 | 3.53 | 合资格其他股东每股 0.1 股 |
| 000908 景峰医药 | 2026-03-10 | 6.37 | 4.36 | 0 |
| 002713 东易日盛 | 2025-12-29 | 11.00 | 8.14 | 0 |
| 002742 三圣股份 | 2025-12-09 | 4.85 | 4.64 | 0 |
| 300125 聆达股份 | 2025-12-26 | 7.86 | 6.22 | 0 |
| 300506 名家汇 | 2025-12-19 | 4.19 | 3.16 | 0 |
| 600375 汉马科技 | 2024-12-25 | 6.14 | 5.77 | 0 |

其中东易日盛、三圣股份、名家汇的最终公告日期就是生效日，所存原始索引只给出午夜
占位，不能据此假设当天 00:00 已公开；聆达原始索引则明确为 2025-12-26 19:24:28。
奥园美谷还包含特定股份受让方延续排除资格及零碎股分配约束，不能将“其他股东”扩展为
所有来源不明的持仓。原文、源候选、索引公开时间及各项限制见
[特殊参考价核查](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/special-reference-original-findings.json)。
这些是已取得数据但账户语义尚未支持的缺口；只增加公告下载量不能使其通过。

完整性分为现有候选全部核验和来源事件完整两项：前者以本次全部历史候选为分母，
后者还需从公告目录反向核对，发现 dividend 未提供的实施事件并按正式来源流程处理。
无来源记录的空分区、已下载 PDF 或全部 resolution 已生成均不等于业务覆盖通过。
无法获取证据的候选保留明确缺口，阻断对应覆盖验收，不改成零权益或扩大成功定义。

2026-10-18 的目录反查确认，原 33,116 个候选不包含恒瑞医药、新凤鸣在 2019-03-27
登记的两次分配；当日原 raw 和 processed 均为空。
[恒瑞实施公告](https://static.cninfo.com.cn/finalpage/2019-03-21/1205921524.PDF)
规定每股税前现金 0.22 元、派送红股 0.2 股；
[新凤鸣实施公告](https://static.cninfo.com.cn/finalpage/2019-03-21/1205921747.PDF)
规定每股税前现金 0.18 元、转增 0.4 股。两者均为 3 月 28 日除权并派现、3 月 29 日
股份上市，不能把派现日当成新增股份上市日。新凤鸣随后章程修订仅反映股本变化、
经营数据更正仅修改产品售价，已逐文核对，不据“修订”标题自动改变分配。两笔在独立
存储经来源采集、公告核验和 V1 解析通过；精确输入、条款及仅覆盖已查公告窗口的限制
见[核验结果](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/omitted-distributions-verified.json)。

来源完整性还不能只按普通分红公告判断。北汽蓝谷的
[股份对价实施公告](https://static.cninfo.com.cn/finalpage/2018-08-20/1205305795.PDF)
明确按 2018-02-01 登记的普通流通股每 10 股获送 5 股，08-21 支付股份、08-23 上市；
这是既有非流通股划转，总股本不变。另一份
[资本公积转增实施公告](https://static.cninfo.com.cn/finalpage/2018-09-13/1205432898.PDF)
则规定 09-18 登记、09-19 除权、09-20 上市，每股转增 2.5 股，基数已包含此前股份对价。
因此，假设从二月登记日持续持有 100 股且没有其他变动，先变为 150 股，再变为 525 股；
只记九月转增会得到 350 股，少 175 股。这只是明确假设下的股数核对，不是收益或参考价验证。
原始索引已包含股份对价公告，但当前分红标题筛选未纳入它；当前 V1 独立分红来源能力也不
等于已实现股改股份划转。仍须核验登记前方案、八月实施阶段及停牌期间估值，不能将八月
实施公告当成二月已知的完整条款。完整原文、查询摘要及支持边界见
[股改股份对价核查](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/600733-share-reform-audit/findings.json)。

同日通过当前配置的 Tushare 数据入口重新采集全部 2,123 个登记日，71 个分区与旧响应
不同：10 个原空分区补出 22 个候选，另 61 个原非空分区变为空，暂时丢失 745 条源行、
693 个候选。随后针对后 61 日重新查询，完整复现了全部旧行多重集合。因此这些空响应
不能解释成分红撤销，现有证据也不能进一步归责于 Tushare 官方、配置的中转服务或某个
具体故障。不能用较新的响应时间代替完整性判断，更不能依靠重复命中 Meta 验证业务无漏。
[全量重采比较](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/dividend-recollection-result.json)
与[空响应定向复查](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/rechecked-empty-dividend-result.json)
分别保留当次返回、代码、参数、摘要和结果。隔离的
[来源对账对象](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/dividend-source-reconciliation.json)
保留原 33,116 个候选全部原值，并单列 22 个新增候选，共 33,138 个；这个并集仍是待核验
来源范围，不是新的全历史通过结论。原输入和异常空响应均保留，原基线分母不被静默替换。
新增 22 个候选在已采集窗口内已有 21 个通过，北陆药业因前述支付日期矛盾及仅有事后
确认保留 unresolved，完整结果及精确输入见
[新增候选核验](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/new-source-candidates-verification.json)。

目录反查还必须区分延期安排和真正遗漏的实施事件。永泰能源原定 2024-08-23 登记，
随后于 08-21 公告延期，来源另有 11-14 登记记录；TCL 中环原定 2023-05-25 登记，
随后公开调整日期，最终实施公告另定 06-15 登记。不能将原定日期下没有来源记录解释为
少记一次分配。相反，慕思股份的
[2025-06-03 实施公告](https://static.cninfo.com.cn/finalpage/2025-06-03/1223733350.PDF)
明确 06-06 登记、06-09 除息及派现，普通股东每股税前现金 1 元、不送转，参考扣减为
0.9833939 元；[06-10 后续公告](https://static.cninfo.com.cn/finalpage/2025-06-10/1223825640.PDF)
确认该次分配已实施。08-15 另行公告的每股转增 0.1 股于 08-20 登记，是独立分配，
不能代替六月现金事件。当前配置的数据通道按登记日、证券、实施公告日、除息日重查，
均未返回六月事件；该结论绑定实际响应，尚不能定位遗漏发生在 Tushare、配置的中转服务
或其他上游环节。该事件按已采用的公告独立来源进入 V1 核验，不能伪造
Tushare 源行，也不能把六月现金挂到八月候选。后续确认只用于完整性审计，六月历史回放
所需条款必须来自当时已公开的 06-03 原公告。原文、查询响应、延期案例及所选 75 条目录
记录的分类见[反向核对证据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/reverse-inspection-reconciled.json)；
这批分类不代表完整目录已全部核验。

对上述 33,138 个候选的后续冻结快照，全部 2,123 个分区逐一核对了来源候选与处置
身份集合：9,893 个 resolved、1 个 not_entitled、23,244 个 unresolved；其中 21,540 个
尚缺公告采集，另 1,704 个保留明确的条款、日期、原文或修订核验缺口。
[全范围处置证据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/cash-consistent-history-coverage.json)
绑定 `frozen-cash-consistency-code.json` 和该次完整输入，已撤销舒华体育冲突金额的错误批准；
修正前结果另存为回归输入，不作为核验通过证据。该快照不包含此后新增的采集结果，也不表示
来源事件已齐全；后续更正关联审计又发现前述 16 份遗漏更正，因此这里的 resolved 数量只表示
该代码及输入下的历史处置，不作为当前已全部核验的数量。最新关联规则须重新撤销或复核这些
批准，并对完整范围重跑。该冻结快照的核验值与每条源行比较还剩 13 处现金差异，均为源现金为空而
核验值为零：10 处原文有明确不分现金表述，另外 3 处九月事件采用已有显式复核的完整
纯转增实施方案，不能将其描述成原文均写了零现金，更不能形成空值自动补零规则。
具体依据及检查限制见
[现金差异核查](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/phase-null-cash-evidence.json)；
[该冻结快照的源值对比](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/cash-consistent-history-source-differences.json)
仍为同一批 13 个候选，不因新增已核验候选或冲突修正而扩大零值规则。

目录反查通过 `CninfoBroker.distribution_index` 按明确自然日范围读取巨潮公开“权益分派”
类别（`category_qyfpxzcs_szsh`），不以现有 dividend 股票名单限制查询，并复用唯一 ID、
分页数量及日期范围校验。完整返回只证明这个目录类别的覆盖；该类别还包含预案和
非实施文件，须逐项分类。北交所当前公告可能出现在返回中，但不能据此推断早期新三板
公告或未归入该类别的独立更正也已完整；这些来源须另外补齐，不能用空查询证明无事件。
2017-01-01 至 2026-10-17 的 118 个月取得 72,305 条该类别唯一索引。114 个月通过月度
查询核对，4 个月以覆盖全部自然日的查询补齐；个别日再拆公开板块，并核对合并数量。
2022 年 7 月的月查询总数在 562、563 间变化，完整逐日查询得到 563 条；2026 年 5、6、9 月
曾出现同一 ID 的返回时间戳差异，原失败均保留，没有合并冲突字段或放宽校验。
[目录覆盖证据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/verified-reverse-coverage.json)
仅证明该明确类别和日期范围；其中标题匹配实施公告的 35,730 条用于待核查队列，不能
按标题数量推算遗漏分红数量或核验通过率。

增量维护由 `run_corporate_action_maintenance` 按自然披露日组装：

```text
CollectPublicationsStep           沪深、北交所全公司索引，包含周末、未分类更正和失败日期重试
prepare_announcement_updates      用当前本地候选重新关联全部已提交的披露索引
CollectAnnouncementsStep / CollectBseAnnouncementsStep
                                 更新受影响的原登记日证据集合
ExtractDistributionTermsStep      条款提取；证据变化后旧人工核验须显式重新提交
ResolveCorporateActionsStep       重建处置；相同输入复用结果
summarize_announcement_maintenance
                                 返回影响分区、处置统计、未匹配公告及输入输出摘要
```

例如 `run_corporate_action_maintenance(path_manager=pm,
publication_interval=TrainingDateRange(start="2026-09-19", end="2026-09-20"))` 查询两天的
完整公开索引；两来源均不以本地证券集合过滤，巨潮也不限制为“权益分派”类别，以捕捉
未分类的独立更正。覆盖证据和失败重试标记按[存储布局](storage_layout.md)保存。
新披露的实施、更正、补充、修订、取消和终止文件先作为待核查证据；没有本地候选的
公告单列 `unmatched_publications`，不能据标题直接产生分红。每次运行还重新关联已存
披露索引，确保公告先到、dividend 候选后到时仍会处理；历史未完成日期也自动重试，
不会因为调用方的查询窗口前移而消失。

更正无法仅凭索引确定所属事件时，保守刷新发行人已有的登记日证据；新旧证券代码只能
使用已保存的官方对应证据。刷新窗口由实际披露日期、候选阶段日期和原集合范围共同
确定，不因下一次运行日期前移就扩大所有旧窗口。相同窗口中出现新的公告身份，或同一
身份的标题、公开日期、路径发生变化，也必须重新查询公司集合；不能只比较窗口。
输入摘要变化后重建核验和派生分区，原文、原版本及旧回放输入保留；相同输入重复执行
复用结果。返回值包含查询区间、影响登记日、处置统计、未匹配公告和精确输入输出摘要，
不会把完成抓取等同于核验通过。索引未暴露的同 URL 静默替换仍需原文复查，不能声称
索引轮询已验证全部源修订。

2026-10-18 的真实增量验证以 2026-09-19 为披露日，读取巨潮 1,038 条、北交所 0 条
完整索引，并在隔离存储中刷新 9 月 22、23 日两个原登记分区。集合扩展后，旧人工核验
先因绑定变化被拒绝；逐项查看涉及的新增公告、确认原实施及更正文件未变后，显式重交
9 个核验。随后两次工作流运行的输出摘要和文件修改时间完全一致，90 个候选已核验，
6 个北交所候选仍因尚未采集原文保持 unresolved。另有 70 条相关公告未匹配这两个
登记日的本地候选；这不是 70 笔确认漏记的分红。该证据验证真实发现、失效、重新核验
和重复执行路径，不证明全部历史或北交所原文已覆盖。
[运行结果](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/incremental-live-result.json)
绑定执行源码，两个报告保存具体输入输出摘要；
[重新核验依据](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/incremental-review/renewal-basis.json)
保留新旧集合差异、原文及不改变条款的理由。后续排版解析及解码失败处置修正对同一批
输入重新验证，96 个候选的完整事件事实不变，重复执行仍复用文件；
[复核结果](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/incremental-latest-parser-result.json)
绑定修正后源码及输出。日期表和证券页脚修正后，又以 `frozen-phase-footer-code.json`
执行两次完整维护入口：重新查询公开目录，96 个候选事实、输出摘要及文件修改时间
均不变，仍为 90 个 resolved、6 个 unresolved、70 条未匹配公告，见
[完整入口重复验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/phase-footer-live-maintenance-result.json)。
独立公告来源、精确时刻修订及完整上市日期句子的本地回归见
[阶段输入回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/subject-listing-regression-result.json)。
后续现金摘要、资本公积组成及跨页金额修正使用 `frozen-capital-component-code.json`
重建九月 21 日与增量两日的输入，573 个候选的金额、股份、阶段日期及可见时刻按
类型精确相等；仙琚制药仅增加显式核验说明，数字尾零差异单列保存，重复执行复用
输出摘要和修改时间，见
[现金与资本组成输入回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/capital-component-regression-result.json)。
现金句后送红股共享单位修正又用 `frozen-shared-unit-code.json` 处理同一完整范围，
573 次候选比较的全部业务事实精确不变，重复处理仍复用派生摘要和修改时间，见
[最新代码输入回归](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/shared-unit-regression-result.json)。
期间一次完整联网维护因北交所异常访问拦截而中断，保留
[中断及源回执](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/subject-listing-live-maintenance-interruption.json)，
该失败不能计为通过。退避后以相同生产请求方式恢复访问，最新冻结代码完成两次
完整联网入口运行：重新查询目录，96 个候选事实、派生摘要和修改时间不变；结果仍为
90 个 resolved、6 个 unresolved、70 条未匹配公告，见
[最新完整入口验证](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/cash-page-live-maintenance-result.json)。
这验证既定样本的真实查询和重复执行，不消除 6 项北交所原文缺口，也不证明未匹配
公告均为遗漏事件。后续共享单位修正仅改变金额解析及其测试；采集、维护编排等源码
与这两次联网运行逐文件一致。最新代码重建两个增量登记分区，96 个事件事实及重复
执行结果不变；其证据与前述联网查询的关联见
[增量维护证据核对](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/shared-unit-maintenance-verification.json)。
这次解析修正没有新增联网运行，不能把先前网络回执声称为最新解析器的执行回执；
本地回归和联网样本均不扩大全历史覆盖结论。
重复执行复用的是不变的证据和派生制品，目录查询仍须执行以发现变化，不承诺零网络请求。
较早的源码和测试输入另冻结后，相关数据流程的 507 项测试通过，见
[测试记录](/home/wsw/app/maintenance-evidence/backtest-coverage-20261018-xncht91n/frozen-data-system-test-result.log)
及同目录 `frozen-data-system-test-inputs.json`，不将这些测试等同于历史数据覆盖验收。

定期对账完整性与未解决清单；实际定时部署遵守运行入口契约，不以创建回测任务代替
数据维护。`anns_d` 可选接入仍是 TODO，未来按公开渠道的历史覆盖、稳定性和维护成本
决定；其 2,000 条上限仍需按日期、证券拆分核对。

验收至少同时覆盖来源完整性、全部候选处置、来源摘要失效、重复执行、关联事件只到账
一次、修订时点、登记股数、应收/现金/股份/可卖数量与逐笔交易。现有回归包括木林森、
顺丰、宝丰、正邦、中岩大地、江铃、差异化参考、独立更正、A/B 日期、每十股换算及
分页重叠/截断。影响账户的未知公司行动仍须进入诊断；公司行动测试通过不会消除行情
缺口、上市状态或其他账户行为限制，全部分区生成也不能证明全部历史公告已核实。

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

## Daily bar

`processed/daily_bar/v1` 是行情事实，不是交易日历。它只能证明某个正式交易日的日线行情
已经落地；其存在或缺失不得改变 `trade_calendar` 给出的交易日序列。

Workflow 不为 `is_open=false` 的日期请求 `daily_bar`。`is_open=true` 时缺少
`daily_bar` 是数据缺失并必须失败；feature、label、training 或 backtest 不得跳过该日并
把后续日期当作替代交易日。

该对象保留 [Tushare A 股日线](https://tushare.pro/document/2?doc_id=27) 的未复权价格和
源端单位：OHLC 为元，`vol` 为手（1 手为 100 股），`amount` 为千元；不在 processed
层改写为回测单位。消费方要求股数时使用 `Decimal(str(vol)) * 100`，结果必须为有限、
非负整数，不截断或四舍五入。回放价格/时间映射由
[offline 工作流契约](../offline_workflow_contract.md#真实行情到回放输入)拥有。
Tushare 停牌期间不提供该证券日线，但不能反向推断每次日线缺失都属于停牌；缺行原因
仍需核实。缺行不表示零成交的完整日线，也不得使用后续日线补齐。
源端日线在盘后入库，其日标签不是精确的历史发布时刻证明。

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
