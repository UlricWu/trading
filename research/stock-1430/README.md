# 股票 14:30 离线研究链路

本卷宗记录从已提交市场事实到股票 14:30 离线模型回放的六项候选假设。每项假设可以独立采用
或拒绝；依赖箭头只表示下游结论成立所需的上游语义，不表示排期或统一生命周期状态。

本文件只拥有各假设的研究状态、证据引用、结论和决策。当前正式行为仍由 `docs/` 中的 owner
docs 拥有；本卷宗、Notebook、代码、commit 或实验结果都不能自行改变正式语义。

## 目标

判断以下最小链路能否在不使用未来信息的前提下，产生可回填、可复现、可评价的 14:30 股票
研究输入、模型资产和受限离线回放：

```text
H01 daily_feature ───────────────────────────┐
                                              v
H02 minute_facts → H03 l2_datasets → H04 fusion → H05 training → H06 replay
       └────────────────────────────────────────────────────────────→ H06 replay
```

H01 与 H02 没有相互依赖，可以并行判断。H03–H06 可以在上游仍为 open 时继续研究，但不得把
候选上游当作正式行为；上游被拒绝或语义实质变化时，下游必须重建基线并重验相关证据。

## 当前背景

- H01 建立时，目标分支正式
  [`tushare_daily_basic/v1`](../../docs/data/daily_feature_label_contract.md) 已定义 schema、公式和
  builder，但 [`data-standard`](../../docs/offline_workflow_contract.md) 对 Feature 与 Label 使用
  空 operation 集，也没有独立的历史 Feature 回填入口。
- 正式 Level2 链路已经产生 `sh_trade/v1`、`sz_trade/v1` 逐笔正成交事实，但尚无可复用的
  stock minute fact。
- 当前训练和回测以日频二字段 key 和 daily timing 为正式语义，不能直接证明 14:30 三字段
  key、完整 timestamp maturity 或 post-decision execution 隔离。
- 当前 experiment artifact 没有完整保存 resolved config、代码版本、输入 manifest、环境和
  随机性信息，不能直接作为本研究的完整可复现证据。
- 本卷宗建立时没有绑定当前假设的 Notebook 或运行事实，因此没有因子有效性、模型效果或
  回放收益结论。需要计算或比较时才创建 Notebook，并将确定版本引用写回对应 Change。

## Change 索引

- [H01 日频 Feature 依赖感知回填](#h01)
- [H02 Level2 股票分钟事实](#h02)
- [H03 14:30 Level2 Feature 与 T+1 Label](#h03)
- [H04 日频与 Level2 Feature 融合](#h04)
- [H05 14:30 融合模型离线训练](#h05)
- [H06 14:30 离线回放与受限执行](#h06)

## 研究解释边界

- H01–H04 的数据契约正确，不自动证明任一 Feature 有预测价值。
- 下文记录的因子机制都是待证伪解释，不是已成立的因果结论。
- H05 的 Rank IC 只评价横截面排序相关性，不证明扣除成本后的收益；H06 的简化执行结果也不
  证明真实排队、冲击、容量或未来表现。
- 如果要据此选择或删除某个独立因子，必须在看到最终结果前固定比较候选、选择数据、最终验证
  数据、指标和停止条件；该选择能够独立关闭时，应新增独立 Change，而不是改写既有结论。

## H01

- **Title**：日频 Feature 依赖感知回填
- **Status**：`adopted`
- **Hypothesis**：由同一个依赖感知 `FeatureBuildStep` 同时承担日常 Standard 派生阶段与
  CLI-only 历史回填，并用独立 Standard facts 冷启动入口显式准备 warm-up，可以在不隐式
  扩大任一请求范围的前提下稳定物化 `tushare_daily_basic/v1`。
- **Why**：日常事实到达后需要在同一 Job 内立即物化 enabled Feature 和刚成熟的 Label；历史
  恢复仍需要只消费已提交 facts 的精确 Feature 回填。首次运行所需 warm-up facts 若藏在任一
  入口中，会混淆目标分区与输入范围，因此必须由独立显式入口承担。
- **Scope**：`data-standard` 在事实阶段后顺序执行 enabled Feature 与 Label；CLI-only
  `data-standard-bootstrap` 只物化显式 Standard facts 闭区间；CLI-only
  `data-feature-backfill` 精确选择一个 Feature set/version 和目标日期闭区间；三条路径复用
  现有 Step、builder、原子发布和 Meta，并支持失败后续建。
- **Not included**：Level2 derived、任何新 HTTP Job kind、cron、隐式 warm-up、自动扩大范围、
  多 Feature backfill 批量选择、覆盖或刷新已有对象。
- **Depends on**：无其他 Change；依赖当前正式 calendar、daily facts、Feature builder 和 Meta。

候选的最小语义：

```text
target identity = features/tushare_daily_basic/v1/trade_date=T
lookback_sessions = 61
required sessions = T-61 ... T，共 62 个正式 session
```

- 请求范围精确表示目标 Feature 分区，历史不足或内部必要对象缺失必须失败，不能自动跳过前
  61 日或缩小范围。
- `lookback_sessions=61` 只能由 V1 builder 拥有；workflow 不复制 Feature 公式和窗口常量。
- 单个 symbol 历史不足继续按正式 Feature 契约产生 null；整个输出为空仍失败。
- 有效 Meta hit 不读取上游或重算；较早日期已提交、较晚日期失败时保留已提交分区，重跑从
  miss 处继续。
- `data-standard` 只选择现有配置中 `enabled=true` 的 Feature/Label；任一集合全部 disabled
  时记录 warning 且对应空 Step 成功。到达日 `A` 的 Label 阶段只构建在 `A` 刚成熟的历史
  分区，不构建 `Label(A)`。
- `data-standard-bootstrap` 的闭区间只表示 facts，不知道 `61`；日常 `data-standard` 与
  Feature backfill 都不隐式承担 cold start。

**Acceptance**：

- `data-standard` 在同一 Job 内按 Calendar → Facts → enabled Features → enabled mature Labels
  分阶段执行；全部 disabled 时记录 warning 且空 Feature/Label 集成功；
- `data-level2` 暂时保留两个空 derived Step，现有 Feature/Label 数值行为不变；
- V1 只有一个 `61` 的依赖权威，目标范围、跨周末/长假和边界失败均由正式 session 推出；
- Feature backfill 为 CLI-only，且不调用 broker、不写 raw/processed/label/experiment；Standard
  facts 冷启动也是 CLI-only，且不写 Feature/Label；两者都不是 HTTP Job kind；
- 原子发布、Meta reuse、部分成功后的重跑行为通过回归测试；
- 真实验证从 `2019-01-01` 开始，先由 facts 冷启动入口准备 61 个正式 warm-up session，再对
  随后 62 个正式目标 session 完成首次构建和 Meta-hit 重跑，并记录 resolved dates、输入
  identity、输出行数、null coverage、耗时和峰值内存；
- adoption 同步正式 owner、实现和测试，并由用户明确决定。

**Evidence（2026-08-28..2026-08-29，候选实现）**：

- 隔离存储根为 `/tmp/minquant-h01-2019-DNP1Nf`，没有写入正式数据。代码基线为
  commit `b1bff7445fd9ce180b8fe7e0665829702dd15b9c`。运行发生在提交前，但受测实现与测试的
  内容和该 commit 完全相同；研究 README 不参与运行。因此以下运行事实已绑定到可恢复的
  候选代码版本，但不表示 H01 已采用。
- 候选验证与最终 Adoption diff 均在项目锁定环境全量执行 `515 passed`；最终复跑命令为
  `uv lock --check && uv run --locked --no-python-downloads python -m pytest`，使用 Python
  3.13.13、PyArrow 25.0.0，另有两条仓库既有的 Python 3.13 multiprocessing `fork()`
  deprecation warning。误用当前 Conda Python 3.13.10、PyArrow 23.0.0 直接执行时为
  `510 passed, 5 failed`：五项失败都是 `pq.read_table()` 从测试临时路径额外推断出
  `trade_date` 或 `year` Hive 分区列；同一代码在发布契约要求的锁定环境通过，因此不改变 H01
  判断。当前环境没有 Ruff、Black、Mypy 或 Pyright；`compileall` 成功。
- 正式日历把请求解析为 warm-up `2019-01-02..2019-04-03` 共 61 个 session，以及目标
  `2019-04-04..2019-07-05` 共 62 个 session。两个完整日期数组的规范 JSON SHA-256 为
  `5430a6cdc7602c075752f0a7f5e86b610cc88f57e8935fb9e958bbd5b3b2852a`。
- `data-standard-bootstrap --start 2019-01-01 --end 2019-04-03` 在第 59 个 warm-up session
  `2019-04-01` 失败：源端 `bak_basic(trade_date=20190401)` 在独立复查时仍返回
  `DataFrame(rows=0, columns=[])`；当时候选 normalize 要求 raw 携带 `stock_basic.list_date`，
  因而抛出 `ValueError`。该失败没有以前后日期填充，首次命令耗时 `22:34.12`、峰值
  `614712 KiB`、exit `1`；显式补齐
  `2019-04-02..2019-04-03` 成功，耗时 `47.52s`、峰值 `390820 KiB`。因此 Feature 所需的
  `daily_bar/adj_factor/daily_basic` 在 61 个 warm-up session 上完整，但“全部 Standard facts
  冷启动成功”这一 Acceptance 在该版本上未通过。
- 同日保存的 `stock_st(trade_date=20190401)` raw 实际也是
  `DataFrame(rows=0, columns=[])`，而不是先前假定的“完整字段零行”；联网复核仍不支持
  “当日没有 ST 排除项”。[Tushare `stock_st` 文档](https://tushare.pro/document/2?doc_id=397)
  将该接口定义为按交易日期获取历史每日 ST 列表，并声明数据从 `20000101`
  开始。[上交所 `2019-04-01` 每日交易公开信息](https://www.sse.com.cn/disclosure/diclosure/public/?time=2019-04-01)
  中 ST 栏目为空，只能证明没有 ST、*ST 或 S 证券触发该页定义的三日累计偏离或
  盘中换手率异常披露条件，该页不是当日 ST 全量名单。上交所成交概况页自有查询
  `security/fund/queryNewAllQuatAbel.do`，固定 `searchDate=2019-04-01`、`inMonth=201904`
  和 `inYear=2019` 后，至少返回以下当日 ST 记录：

  | 证券 | 收盘价 | 成交量（万股） | 成交金额（万元） |
  | --- | ---: | ---: | ---: |
  | [600397 `*ST安煤`](https://www.sse.com.cn/assortment/stock/list/info/turnover/index.shtml?COMPANY_CODE=600397) | 2.98 | 1,361.7439 | 4,008.73 |
  | [600423 `*ST柳化`](https://www.sse.com.cn/assortment/stock/list/info/turnover/index.shtml?COMPANY_CODE=600423) | 4.06 | 1,878.5264 | 7,422.39 |
  | [600701 `*ST工新`](https://www.sse.com.cn/assortment/stock/list/info/turnover/index.shtml?COMPANY_CODE=600701) | 2.81 | 2,469.0202 | 6,874.67 |
  | [600749 `*ST藏旅`](https://www.sse.com.cn/assortment/stock/list/info/turnover/index.shtml?COMPANY_CODE=600749) | 12.21 | 478.8736 | 5,803.95 |
  | [600202 `*ST哈空`](https://www.sse.com.cn/assortment/stock/list/info/turnover/index.shtml?COMPANY_CODE=600202) | 5.85 | 0 | 0 |
  | [600871 `*ST油服`](https://www.sse.com.cn/assortment/stock/list/info/turnover/index.shtml?COMPANY_CODE=600871) | 2.49 | 0 | 0 |

  其中前四只有非零成交；`*ST哈空` 和 `*ST油服` 当日停牌，并分别发布
  [撤销退市风险警示公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/c/2019-04-01/600202_20190401_1.pdf)
  和[撤销退市风险警示公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/c/2019-04-01/600871_20190401_1.pdf)。
  该响应与上交所资料存在数据质量差异，但用户确认 Tushare broker response 的记录集合是
  H01 候选要正式化的 source 权威；零行响应仍是有效空 source 对象，normalize 负责构造可
  消费的空 `symbol` 列并正常发布 processed。外部差异只作为待后续统一排查的研究异常，不
  触发运行时复核、失败、填充或备用来源，也不改变 Access 对该空对象不产生 ST 排除项的
  行为。
- 用户确认 cold-start 继续包含全部 Standard facts，并确认零行、零列 `stock_basic` 是可信
  空记录集合。首次修正后重跑暴露同日空 `stock_st` processed 没有 `symbol`、不能由 Access
  消费；该失败被保留为回归场景，最终由同一个 normalize 边界为
  `stock_basic/stock_st/suspend_d` 构造各自必要的空列。最终 warm-up 重跑解析
  `2019-01-02..2019-04-03` 共 61 个 session，12 个 Standard source 的 732 个 raw 与 732 个
  processed 均通过 Meta/payload 校验；`stock_basic` 为零行 `symbol/list_date`，`stock_st` 为
  零行 `symbol`，Access 对 `2019-04-01` 直接返回空 universe。原样再跑为 732 次 raw Meta hit、
  732 次 processed Meta hit、0 次发布，耗时 `1.44s`、峰值 `245868 KiB`、exit `0`。
- 目标 facts 冷启动 `2019-04-04..2019-07-05` 成功：12 个 Standard dataset 的 744 个分区
  全部通过 Meta/payload 校验，耗时 `23:22.34`、峰值 `603248 KiB`、exit `0`。
- Feature 实际输入清单由 1 个 2019 calendar、123 个 `daily_bar`、123 个 `adj_factor` 和
  81 个 `daily_basic` identity 组成，共 328 项。每项记录 role、storage-relative Meta/payload、
  payload size 和直接 upstream Meta/size，按 Meta path 排序后的规范 JSON SHA-256 为
  `9b2f4c8721e4f9d6dd287e944131129bd9901516f6983d900ab4ec3494fe7b56`；所有对象均通过正式
  Meta 读取边界。
- 当前代码下再次从 0 个既有分区开始 Feature backfill，62 次 publish、0 次 Meta hit，耗时
  `1:58.26`，其中 `FeatureBuildStep` 为 `117.209s`，峰值 `724148 KiB`、exit `0`。原样重跑为
  0 次 publish、62 次 Meta hit，命令耗时 `1.06s`，其中 `FeatureBuildStep` 为 `0.014s`，峰值
  `245828 KiB`、exit `0`。
- 62 个 Feature 输出 identity 的规范清单 SHA-256 为
  `125263d2d417f2b9d7141766592affc526d82714d5d13b03108a11fbe238f674`。所有分区具有相同的
  正式 2-key + 16-feature schema，key 唯一，分区日期等于显式目标，所有非 null feature 数值
  有限。当前重建的 62 个 payload 与前次构建逐字节相同；path 与内容组成的 manifest SHA-256
  为 `288a47785913b328c3abb7c8139cbb982a35df1774cd57a5a740e33c2b25e9b0`。总行数为
  224,916；单分区为 3,586..3,657 行，首分区 3,608 行，末分区 3,646 行。
- 以 224,916 行为分母，null coverage 为：

  | Feature 列 | null 行数 | 比例 |
  | --- | ---: | ---: |
  | `f_d_intraday_return`、`f_d_log_volume`、`f_d_log_amount` | 0 | 0% |
  | `f_d_close_return_1d`、`f_d_open_gap_1d`、`f_d_range_vs_prev_close` | 834 | 0.370805% |
  | `f_d_amount_mean_5d_asof_tminus1` | 2,547 | 1.132423% |
  | `f_d_close_return_5d_asof_tminus1` | 2,910 | 1.293816% |
  | `f_d_max_drawdown_20d_asof_tminus1`、`f_d_close_distance_to_high_20d_asof_tminus1`、`f_d_amount_mean_20d_asof_tminus1`、`f_d_close_position_in_range_20d_asof_tminus1` | 7,088 | 3.151399% |
  | `f_d_close_return_20d_asof_tminus1`、`f_d_close_volatility_20d_asof_tminus1` | 7,374 | 3.278557% |
  | `f_d_turnover_rate_mean_20d_asof_tminus1` | 7,537 | 3.351029% |
  | `f_d_close_volatility_60d_asof_tminus1` | 16,612 | 7.385869% |

- 同一隔离根上的 `data-standard --start 2019-07-05 --end 2019-07-05` 依次完成 Calendar、
  Facts、Feature、Label：12 个 raw 和 12 个 processed fact 均 Meta hit，Feature Meta hit；d1、
  d3、d5 Label 分别发布到 `2019-07-04`（3,643 行）、`2019-07-02`（3,648 行）和
  `2019-06-28`（3,652 行），三个 set 均不存在 `Label(2019-07-05)`。当前代码下重建耗时
  `1.22s`、峰值 `323076 KiB`、exit `0`；原样重跑的三个 Label 均 Meta hit，耗时 `1.04s`、
  峰值 `245808 KiB`、exit `0`。

- **Conclusion**：Acceptance 全部通过。隔离实测证明同一组最小 Step 可以同时承担日常
  Standard 派生、显式 Standard facts 冷启动和 Feature-only 历史回填；不需要新 HTTP Job、
  隐式 warm-up、备用来源或兼容入口。Tushare broker response 的记录集合按 source 契约直接
  信任，空响应由 normalize 构造成下游可消费的空对象。
- **Decision（2026-08-29）**：用户明确采用 H01，并授权部署测试环境及回填正式
  Feature/Label 数据。
- **Formalized in**：[`source_contract.md`](../../docs/data/source_contract.md)、
  [`daily_feature_label_contract.md`](../../docs/data/daily_feature_label_contract.md)、
  [`access.md`](../../docs/engineering/access.md)、
  [`cli_contract.md`](../../docs/engineering/cli_contract.md)、
  [`job_api_contract.md`](../../docs/engineering/job_api_contract.md) 和
  [`offline_workflow_contract.md`](../../docs/offline_workflow_contract.md)。
- **Next**：Adoption PR 合入 `dev` 后，由 `release/auto-release` 部署精确测试 SHA；部署成功后
  才在 `/home/wsw/app/data` 回填 Feature/Label，并分别记录 merge、deploy 与数据状态。

## H02

- **Title**：Level2 股票分钟事实
- **Status**：`open`
- **Hypothesis**：将两市已提交的正成交逐笔事实聚合为守恒、稀疏、phase-aware 的一分钟事实，
  能消除下游重复扫描与重复定义，同时在完整交易日保持有界内存。
- **Why**：Feature、Label 和 replay 都需要相同的分钟成交量、成交额、价格范围和方向代理；
  让每个下游直接扫描数千万逐笔行会重复成本并产生不同分钟语义。
- **Scope**：两个 V1 minute dataset、固定 schema/key、单 upstream lineage、按 symbol 有界批处理、
  CLI-only 范围回填、原子发布和 Meta reuse。
- **Not included**：order/order-book、撤单、盘口、非股票品种、dense 分钟、补零或前向填充、
  rolling Feature、Label、训练、回放、FTP 下载、HTTP、cron 或 MQTT。
- **Depends on**：无其他 Change；依赖正式 `sh_trade/v1`、`sz_trade/v1`、stock 和 phase 语义。

候选 identity：

```text
processed/sh_stock_trade_1m/v1/trade_date=T
processed/sz_stock_trade_1m/v1/trade_date=T
key = (symbol, trade_date, minute_start_ts_utc, phase)
```

每个左闭右开一分钟桶保存 `high`、`low`、`volume_sum`、`notional_sum`、`trade_count`、
`tick_signed_volume_sum` 和 `tick_signed_notional_sum`。其中 signed 值只表示 tick-rule direction
proxy，不解释为交易所认证的主动买卖方向。

- 只保留 `security_type=stock`，但同时保留 AUCTION 和 CONTINUOUS，并把 `phase` 放入 key。
- 只保存实际观察到成交的 key；缺行不等于零成交量，不建立 dense session grid。
- 每个输出只绑定同交易所同日逐笔对象；两市和多个日期不是事务。
- 完整交易所日对象不得整体转为 Pandas；批大小属于实现细节，由真实日峰值内存证据决定。

**Acceptance**：

- 每个 exchange/date 的 tick 数、volume、notional 和 signed sums 与 stock 输入守恒；
- minute/phase 边界、午休 sparse 语义、空 stock observation 与 missing upstream 可区分；
- key 唯一且全局排序稳定，NaN、Infinity 和整数溢出确定性失败；
- 完整日通过有限 symbol batch 构建，没有整日 Pandas materialization；
- 多个真实完整交易日记录输入 identity、tick/输出行数、守恒误差、峰值 RSS、耗时和重跑复用；
- CLI-only，不改变现有 Level2 normalize、Access 或事实 workflow；
- adoption 同步正式 owner、实现和测试，并由用户明确决定。

- **Next**：在独立实现分支中先用合成跨分钟/phase 数据固定守恒容差，再用两市完整真实日确定
  有界批处理是否满足资源约束。

## H03

- **Title**：14:30 Level2 Feature 与 T+1 VWAP Rank Label
- **Status**：`open`
- **Hypothesis**：固定 14:30 event-time cutoff、post-decision VWAP 窗口和 Feature 驱动的行集合，
  可以构造无未来泄漏且 key 完全对齐的 Level2 Feature/Label 数据集。
- **Why**：完整日 Level2 universe、T+1 成交是否存在或 14:30 后数据都不能反向决定 T 日 14:30
  样本；整数 session lookahead 也不能表达 Label 到 T+1 14:36 才成熟。
- **Scope**：一对共同采用的 V1 Feature/Label、固定三字段 key、14:30 universe、32 个 Feature、
  T/T+1 VWAP rank Label、完整 timestamp maturity、多直接 upstream lineage 和 CLI-only 回填。
- **Not included**：日频融合、模型、组合、交易、order-book、ST/停牌/上市天数过滤、多决策时点、
  成本、滑点、HTTP、cron、实时源或未来版本。
- **Depends on**：H02。上游未 adopted 时可以研究候选，但 H02 语义变化会使本 Change 证据失效。

固定时间与 identity：

```text
decision grid       = stock_1430_v1
Feature visibility  = T CONTINUOUS minute_start_ts_utc < 14:30
entry window        = T   [14:31, 14:36)
exit window         = T+1 [14:31, 14:36)
Label maturity      = T+1 14:36 Asia/Shanghai
key                 = (symbol, trade_date, decision_ts_utc)
```

Universe 只包含 T 日 14:30 前至少观察到一笔 CONTINUOUS stock 成交的 symbol。Label 必须继承
Feature 的完整 key、行数和顺序；entry、exit 或 adjustment factor 无效只产生 null Label，不能
删除样本。

V1 对 5/15/30/60 个计划连续竞价分钟分别计算八项输入，共 32 列：

| 候选量 | 待证伪机制 |
| --- | --- |
| 窗口首尾 minute VWAP return | 捕捉接近决策点的短期方向、延续或反转 |
| high/low range | 捕捉窗口内价格不稳定性 |
| notional、trade count、average trade notional | 捕捉活跃度、流动性和成交粒度 |
| tick-signed volume/notional ratio | 作为逐笔方向压力代理，不解释为真实主动买卖 |
| observed minute ratio | 区分连续观察与稀疏观察，不能用补零伪造 |

除 observed minute ratio 保留原始比例外，其余量在当日 14:30 universe 内做 ascending、
average-tie percentile rank。缺少计划窗口首尾分钟时 edge return 为 null；缺失分钟不向更早
观察扩张窗口。

Label 使用：

```text
gross_return = exit_raw_vwap(T+1) * adj_factor(T+1)
             / (entry_raw_vwap(T) * adj_factor(T)) - 1
y_rank_return = gross_return 在 Feature universe 内的 ascending percentile rank
```

**Acceptance**：

- decision、visibility、entry、exit、maturity 和三字段 key 无歧义；
- 修改 14:30 及以后数据不改变 Feature，修改 entry/exit 窗口外数据不改变 Label；
- Feature 固定 32 列，计划窗口、null、tie/rank 和 signed proxy 解释通过手算测试；
- Label 与 Feature 的行、key、顺序完全一致，无效监督值保留 null；
- 多 upstream Meta 与现有无/单 upstream 对象兼容，任一直接输入变化禁止 reuse；
- 多个真实 T/T+1 对记录 universe、coverage、key digest、输入 identity、耗时和峰值内存；
- adoption 同步正式 owner、实现和测试，并由用户明确决定。

- **Next**：H02 候选语义稳定后，在独立实现分支中固定真实 T/T+1 验收样本，先完成时间泄漏
  变形测试，再判断 32 个候选量的数据质量；不得据此提前宣称 alpha。

## H04

- **Title**：日频与 Level2 Feature 融合
- **Status**：`open`
- **Hypothesis**：以 T 日 14:30 Level2 universe 为唯一行集合，只连接前一正式 session P 的
  日频 Feature，可以形成固定 39 列、可追溯且在 T 日 14:30 可见的模型输入。
- **Why**：训练和回放临时 join 会分散日期 lag、universe、列顺序和缺失规则；读取 T 日日频
  Feature 则会使用收盘后信息。
- **Scope**：`stock_1430_daily_l2/v1`、P/T 时间关系、L2-left join、七个日频量在 T universe
  内重新排名、固定 39 列、two-upstream lineage 和 CLI-only 回填。
- **Not included**：修改上游、构建 Label、模型训练、因子选择、缺失填充、行业/市值中性、
  fallback、HTTP、cron、实时源或未来版本。
- **Depends on**：H01、H03；H03 同时提供下游训练所需的 Label。

候选 identity 与时间：

```text
output = features/stock_1430_daily_l2/v1/trade_date=T
P      = previous_session(T)
input  = l2_stock_1430/v1(T) + tushare_daily_basic/v1(P)
key    = (symbol, trade_date, decision_ts_utc)
```

输出完整保留 H03 的 32 列，再追加以下七个日频量在 T 日 L2 universe 内的 percentile rank：

```text
close return 1d, open gap 1d, log amount,
max drawdown 20d as-of P-1, close volatility 60d as-of P-1,
close return 5d as-of P-1, turnover-rate mean 20d as-of P-1
```

这些量分别候选表达近期方向/隔夜跳空、流动性与关注度、历史回撤、波动、短期趋势和换手活跃度。
它们是否提供增量预测价值必须由 H05 的预注册比较判断，不能由融合成功推出。

- 输出 key、行数和顺序逐行继承 L2 T；daily 多余 symbol 被忽略，L2 symbol 缺失 daily 时保留
  行并令七列为 null。
- 整个 P partition 缺失必须失败；禁止 P-2、最近日、T daily 或 L2-only fallback。
- 输出只记录 L2 T 与 daily P 两个直接 upstream，不重复展开传递 lineage。

**Acceptance**：

- P 由正式 session 解析，跨周末、长假和年度边界正确；
- 修改或删除 T 日 daily Feature 不影响融合 T，P 缺失时不 fallback；
- 输出 key/rows/order 与 L2 T 完全一致，32 个 L2 值不被重算；
- 七个 source-to-rank 映射、null/tie/valid-count 和最终 39 列顺序通过手算测试；
- 两个直接 upstream 精确记录并参与 Meta reuse validation；
- 多个真实 P/T 对记录七列 coverage、schema/key digest、耗时和峰值内存；
- adoption 同步正式 owner、实现和测试，并由用户明确决定。

- **Next**：H01/H03 的候选 schema 稳定后，在独立实现分支中完成 P/T 无泄漏和 key 对齐验证；
  在 H05 预注册比较前不选择或删除七个日频量。

## H05

- **Title**：14:30 融合模型离线训练
- **Status**：`open`
- **Hypothesis**：按完整 Label maturity timestamp 净化训练样本的固定 walk-forward workflow，
  可以对 39 列融合输入产生无泄漏、确定且可恢复的模型评价与 inference artifact。
- **Why**：当前日频 schedule 只使用整数 lookahead；在 E 日 14:30，E-1 Label 要到 14:36
  才成熟，若仅按日期判断会泄漏。当前 artifact 也不足以绑定全部输入和运行条件。
- **Scope**：三字段 dataset loader、timestamp-aware schedule、固定 rolling SGD baseline、按日
  Rank IC、coverage、完整 input manifest、模型 cutoff、CLI-only experiment 和报告。
- **Not included**：数据构建、随机行拆分、超参数搜索、自动择优、在线学习、模型 registry、
  生产选择、回放或交易。
- **Depends on**：H03 的 Label 与 H04 的融合 Feature。使用尚未 adopted 的候选输入只能形成
  候选证据；任一上游语义变化后必须重验。

固定 baseline：

```text
Feature              = stock_1430_daily_l2/v1，ordered 39 columns
Label                = l2_stock_1430_t1_vwap_rank/v1
evaluation decision  = E 14:30
purged sample        = E-1
latest eligible      = E-2
train window         = 最近 30 个 eligible sessions
model                = SGDRegressor(alpha=0.0005, l1_ratio=0.0, random_state=0)
preprocessing        = missing=drop
```

每个 evaluation E 使用 fresh model；同一日期的全部 symbol 只能整体进入 train 或 eval。最终
artifact 只保存最后一个成功 window 的模型，并记录 `model_fit_cutoff_ts_utc=E 14:30`；它不能
用于回放 cutoff 之前的决策。

任何“融合有效”“日频组有增量”或单因子解释都需要额外证据：至少在相同 folds、数据和指标下
预注册相应 baseline/ablation。没有该比较时，只能报告固定融合 baseline 的 Rank IC 与 coverage，
不能作归因结论。

**Acceptance**：

- E-1 永远不进入 E 14:30 训练，跨周末/长假/年度边界由完整 maturity timestamp 推出；
- 固定 30-session rolling、模型参数、39 列、`missing=drop` 和 `random_state=0` 未在结果后改变；
- Feature/Label 三 key、行数和值顺序精确一致，不 join、不随机拆分日期内 symbol；
- metrics 使用精确 decision timestamp，artifact 绑定 grid、cutoff、版本、列、有效参数、
  preprocessing 和全部实际输入分区；
- 相同代码、输入、参数和环境重跑得到相同 schedule、coverage、prediction 与 metrics；
- 在首次查看最终验证结果前，将 selection/final-validation 日期、最小有效日/coverage、主指标、
  通过或拒绝阈值和停止条件写入本 Change；失败 window 与结果完整保留；
- adoption 同步正式 owner、实现和测试，并由用户明确决定；experiment 成功不自动选择生产模型。

- **Next**：先补齐预注册日期与量化阈值，再在独立实现分支中实现和验证 schedule/artifact；
  需要计算时创建 H05 Notebook，不创建占位文件。

## H06

- **Title**：14:30 离线回放与受限执行
- **Status**：`open`
- **Hypothesis**：单日单时点 snapshot、模型 cutoff、post-decision execution 和显式 T+1 状态机，
  可以在不向 signal 暴露未来窗口的情况下评价指定模型，并诚实报告简化执行的限制。
- **Why**：当前 daily backtest timing 和全量 minute cube 不能保证一次且仅一次的 14:30 决策，
  也不能隔离 14:31–14:36 执行价格、Label maturity 和模型历史 cutoff。
- **Scope**：CLI-only replay、14:30 snapshot、pending target、14:36 window-VWAP execution、固定
  组合 baseline、T+1 sellability、最终退出、公司行动 fail-fast、完整输入 manifest 与报告。
- **Not included**：数据或模型构建、真实 broker、实时源、order-book/排队/冲击/成交概率模型、
  自动模型选择、HTTP、cron、公司行动现金与股数转换或长期真实收益声明。
- **Depends on**：H02 的执行窗口事实、H03 的 Label、H04 的融合 Feature、H05 的完整模型 artifact。

固定 replay baseline：

```text
decision             = T 14:30，一日一次
execution            = T [14:31,14:36) raw VWAP，T 14:36 一次成交
portfolio            = score top-20，95% equity，equal notional，100-share lot
slippage             = adverse 5bp，另计现有 A 股费用
model use            = decision_ts >= model_fit_cutoff_ts
final exit           = end 下一正式 session 的相同执行窗口
```

- Signal/portfolio 无法取得 execution window 或当日未成熟 Label；T 日 prediction 只在 T+1
  14:36 后评价一次。
- 缺执行价格形成明确未成交，不用 14:30、下一分钟、daily close 或前值补齐；报告 requested、
  clipped、filled、market volume、participation、slippage、费用和未成交原因。
- 新买数量只在下一正式交易日 day-start 变为可卖；最终退出后仍有持仓必须失败，不能按最后价
  伪造清仓成功。
- 持仓跨 adjustment factor 缺失、无效或变化时，在当日 signal 前失败且不发布成功收益报告，
  因为当前没有正式公司行动持仓转换。

**Acceptance**：

- 每日恰好一个 snapshot、pending target 和 execution；修改 entry window 不改变 score/target；
- grid、Feature identity/列、preprocessor、模型 cutoff 和完整 artifact 均精确校验；
- top-20、95%、100-share、5bp baseline 未在结果后改变，T+1 和最终退出边界测试通过；
- VWAP、volume、participation、成本、裁剪和缺失原因完整可复现；
- 持仓 factor change/missing 确定性失败且不发布成功 metrics/report；
- 报告明确限定为 window-VWAP simplified execution，不声称真实容量或长期公司行动净收益；
- 在运行前固定模型 artifact、回放范围、评价指标、可接受阈值和停止条件，并保留全部失败；
- adoption 同步正式 owner、实现和测试，并由用户明确决定；不授权 production model、MQTT
  或真实下单。

- **Next**：H05 产生 cutoff 合法且可恢复的候选 artifact 后，先预注册 replay 范围与阈值，再
  在独立实现分支中完成未来隔离、T+1、最终退出和公司行动失败测试；需要运行时创建 H06
  Notebook 或稳定 experiment 引用。

## 未来版本边界（非 Change）

以下内容只保存本次研究形成的条件性版本边界，不属于 H01–H06 的采用范围，不创建实现承诺，
也不改变当前正式语义。未来准备实现时，必须按独立采用边界建立新的 Change，并重新确定
Hypothesis、Acceptance 与 Evidence；届时可以明确接受、修改或推翻这些边界。

### 日频 Feature V2

条件性的 builder 依赖元数据为：

```python
class TushareDailyBasicV2Builder:
    lookback_sessions = 121
```

版本独立存储：

```text
features/tushare_daily_basic/v1/trade_date=T/data.parquet
features/tushare_daily_basic/v2/trade_date=T/data.parquet
```

- V2 是独立新版本，不覆盖、迁移、别名或静默升级 V1。
- 构建 V2 的目标日 T 需要 T 之前 121 个正式交易日，加上 T 共读取 122 个正式 session。
- 如果未来把 V2 接入 backfill，必须从 V2 builder 读取 `lookback_sessions`，不能硬编码或
  根据版本名推导依赖长度。
- 首个目标日前、只用于满足 lookback 的正常 warm-up session 不生成 Feature 分区。该规则不
  授权 planner 静默缩小显式目标范围；显式目标 T 缺少完整历史时仍必须失败。
- 必要历史范围中间存在日期或对象缺口时必须失败，不能跳过、补零、读取更早日期替代或回退
  到其他版本。
- 当前不把配置或单次命令扩展为同一个 feature set 同时声明、自动发现或构建多个版本。

### Level2 Feature、Label 与融合版本

- 如果 Level2 Feature V2 只增加能够从现有 minute facts 推导的 120 分钟窗口，它可以继续
  明确读取 minute fact V1，不因下游 Feature 版本变化而复制 minute fact 版本。
- Level2 的 120 分钟窗口表示 decision 前最后 120 个计划连续竞价分钟，不是 120 个自然分钟，
  也不是最后 120 条实际观察。
- 以 14:30 decision 为例，跨午休的 120 个计划连续竞价分钟窗口是：

  ```text
  [11:00,11:30) + [13:00,14:30)
  ```

  午休不进入窗口分母，稀疏观察也不能把窗口向更早时间扩张。
- Feature V2 可以继续明确绑定 Label V1，前提是 Label 的经济目标、universe、key、entry/exit
  和 maturity 均未改变；Feature 与 Label 的版本号不要求同步。
- minute fact、Level2 Feature、Label、daily Feature 和融合 Feature 分别独立版本化；某一层
  升级不自动要求其他层使用相同版本号。
- 每个融合 Feature 版本必须明确绑定一个具体的 daily Feature set/version 与 Level2 Feature
  set/version 组合，不能从存储状态推断上游。
- 所有 producer、consumer、训练 artifact 和 replay 必须使用精确版本 identity；禁止
  `latest`、自动升级、缺失时降级、跨版本列 union、列交集、重排或补 null 来伪造兼容。
