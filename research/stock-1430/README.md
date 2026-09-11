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

## 当前背景（2026-09-11，本地 `dev@57d94ea`）

- H01 的日常 enabled Feature/成熟 Label、显式 Standard facts 冷启动和 Feature 历史回填
  已随 `2ae615e` 合入 `dev`，正式行为由
  [`daily_feature_label_contract.md`](../../docs/data/daily_feature_label_contract.md) 和
  [`offline_workflow_contract.md`](../../docs/offline_workflow_contract.md) 拥有。
- H02 的两市股票分钟事实及 CLI-only 回填已随 `57d94ea` 合入 `dev`；正式行为由
  [`level2_minute_contract.md`](../../docs/data/level2_minute_contract.md) 拥有。
- H03 当前最小候选已提交为 `feature/intraday_feature@5219ae2`，尚未合入本地 `dev`。
  `feature/1430@cd88f0c` 的候选实现、隔离验收和正式路径回填仍作为历史证据保留；回填事实
  不等于正式采用。
- 当前训练和回测以日频二字段 key 和 daily timing 为正式语义，不能直接证明 14:30 三字段
  key、完整 timestamp maturity 或 post-decision execution 隔离。
- 当前 experiment artifact 没有完整保存 resolved config、代码版本、输入 manifest、环境和
  随机性信息，不能直接作为本研究的完整可复现证据。
- H03 已保存可恢复的 Notebook 和运行证据，其结果只证明相应版本的数据构建行为；目前仍
  没有因子有效性、模型效果或回放收益结论。

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
- 用户明确 Feature/Label 是可以从 processed facts 确定性重建的派生对象，不要求先部署服务
  才能人工回填。2026-08-29 从干净 commit
  `5b0d4ede2146748511351c97ead5e372e0fd32f8`、项目锁定环境直接写入测试数据根
  `/home/wsw/app/data`；写入前 `/home/wsw/app/data/raw` 是 mountpoint。测试服务没有部署本次
  commit，仍运行 `aaf77a70b6c780a98656666fad42cf83b28732ff`；merge、deploy 与派生数据回填按
  独立状态记录。
- 正式 `data-feature-backfill` 对 `2019-04-04..2019-07-05` 发布 62 个 Feature 分区、
  224,916 行，单分区 3,586..3,657 行；所有 payload 与隔离验收输出逐分区、逐字节相同，
  null 计数也逐列相同。首次耗时 `2:00.98`，其中 `FeatureBuildStep=119.922s`，峰值
  `736412 KiB`、exit `0`；原样重跑 62 次 Meta hit、0 次 publish，耗时 `1.04s`，峰值
  `245492 KiB`、exit `0`。
- 随后的正式 `data-standard --start 2019-04-04 --end 2019-07-05` 复用 1 个 calendar 年对象、
  744 个 raw fact、744 个 processed fact 和 62 个 Feature，raw fetch、processed publish 与
  Feature publish 均为 0；只发布三个 enabled Label set 各 62 个分区：

  | Label set | 目标范围 | 行数 | null 行数 | null 比例 |
  | --- | --- | ---: | ---: | ---: |
  | `daily_close_return_rank_d1/v1` | `2019-04-03..2019-07-04` | 224,872 | 790 | 0.351311% |
  | `daily_close_return_rank_d3/v1` | `2019-04-01..2019-07-02` | 224,799 | 1,034 | 0.459966% |
  | `daily_close_return_rank_d5/v1` | `2019-03-28..2019-06-28` | 224,688 | 1,206 | 0.536744% |

  三个末分区都在到达日 `2019-07-05` 成熟，不存在 `Label(2019-07-05)`；与隔离验收重叠的
  三个 payload 逐字节相同。首次耗时 `11.90s`，其中 `LabelBuildStep=10.363s`，峰值
  `398668 KiB`、exit `0`；原样重跑为 744 次 raw fact Meta hit、744 次 processed fact Meta
  hit、62 次 Feature Meta hit、186 次 Label Meta hit、0 次 publish，耗时 `1.78s`、峰值
  `247880 KiB`、exit `0`。
- 对 62 个 Feature 和 186 个 Label 分区逐一执行 Meta/payload、逻辑 schema、非空唯一 key、
  `(symbol, trade_date)` 排序、分区日期、与 `daily_bar(T)` symbol 集合一致、非 null 数值有限、
  Label 有效值位于 `(0, 1]` 的检查，全部通过。首次检查把 Arrow `large_string` 误写成只允许
  物理 `string` 而失败；[`table_ops.md`](../../docs/engineering/table_ops.md) 已明确两者属于同一
  逻辑 string，按该 owner 修正检查后通过。raw 与 processed 的 path/size/mtime 指纹在写前、
  写后及幂等重跑后分别稳定为
  `10729cefebe223def31d089135805e7811ed4e5539142fcebe2a486d6ffeb359` 和
  `92e03a727c18c9d4c1d1ca1ab5d705ecd510dae8d75ada02208bcde152b45f2d`，没有被本次回填改写。

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
- **Next**：本段记录的正式派生回填已完成；owner、实现和测试已随 PR #12 的
  `2ae615ee652f896c58d16ae398d75bcefd542c97` 合入 `dev`。2026-09-08 只按本地 Git
  可恢复状态校正记录，未重新核验 release、deploy 或当前正式数据内容；后续新反例按独立
  Change 处理。

## H02

- **Title**：Level2 股票分钟事实
- **Status**：`adopted`
- **Hypothesis**：将两市已提交的正成交逐笔事实聚合为守恒、稀疏、phase-aware 的一分钟事实，
  能消除下游重复扫描与重复定义，同时在完整交易日保持有界内存。
- **Why**：Feature、Label 和 replay 都需要相同的分钟成交量、成交额、价格范围和方向代理；
  让每个下游直接扫描数千万逐笔行会重复成本并产生不同分钟语义。
- **Scope**：两个 V1 minute dataset、固定 schema/key、单 upstream lineage、按 symbol 有界批处理、
  CLI-only 范围回填、原子发布和 Meta reuse；Access 的 Level2 symbol 与 trade 读取允许显式限定
  单一交易所，缺省仍表示全市场。
- **Not included**：order/order-book、撤单、盘口、非股票品种、dense 分钟、补零或前向填充、
  rolling Feature、Label、训练、回放、FTP 下载、HTTP、cron 或 MQTT。
- **Depends on**：无其他 Change；依赖正式 `sh_trade/v1`、`sz_trade/v1`、stock 和 phase 语义。

候选 identity：

```text
processed/sh_stock_trade_1m/v1/trade_date=T
processed/sz_stock_trade_1m/v1/trade_date=T
key = (symbol, trade_date, minute_start_ts_utc, phase)
```

每个左闭右开一分钟桶保存 `open`、`high`、`low`、`close`、`volume_sum`、`notional_sum`、
`trade_count`、`tick_signed_volume_sum` 和 `tick_signed_notional_sum`。其中 signed 值只表示
tick-rule direction proxy，不解释为交易所认证的主动买卖方向。分钟起点由 UTC epoch
microseconds 对 `60_000_000` 向下取整；OHLC 在每个完整 key 内按
`(ts_utc, main_seq, sub_seq)` 升序选择首尾并计算极值。该顺序只提供确定性结果，不把不同
`main_seq` 的数值顺序解释为跨通道因果关系；完全相同排序身份但 `price` 不同的输入无法决定
开收盘价，必须失败。

- 只保留 `security_type=stock`，但同时保留 AUCTION 和 CONTINUOUS，并把 `phase` 放入 key。
- 只保存实际观察到成交的 key；缺行不等于零成交量，不建立 dense session grid。
- 每个输出只绑定同交易所同日逐笔对象；两市和多个日期不是事务。
- 完整交易所日对象不得整体转为 Pandas；批大小属于实现细节，由真实日峰值内存证据决定。
- `level2_symbols(trade_date, exchange=None)` 与 `trades(trade_date, symbols, exchange=None)` 的
  `exchange=None` 表示全市场，显式 `sh` 或 `sz` 只要求对应单一对象；`trades.symbols` 继续
  必填。`level2_symbols` 返回 Meta 中实际观察到的全部证券类型，股票过滤只由分钟 producer
  根据正式 `security_type` 执行。
- 有效上游没有 stock 行时发布固定 schema 的零行 Parquet 和单 upstream Meta；missing 或
  invalid upstream 失败。分钟输出不建立 `symbol_slices`。
- CLI 固定同时请求 SH、SZ，按日期升序且每个日期固定 SH 后 SZ；首次失败终止。已经提交的
  较早日期或同日较早交易所对象保留，重跑只复用 direct upstream 未变化的有效输出。

**Acceptance**：

- 每个 exchange/date 的 tick 数、volume、notional 和 signed sums 与 stock 输入守恒；整数
  精确相等，浮点以 `math.fsum` 为 reference，预先固定 `rel_tol=1e-12`、
  `abs_tol=1e-6`；
- minute/phase 边界、午休 sparse 语义、空 stock observation 与 missing upstream 可区分；
- OHLC、key 唯一和全局排序稳定；NaN、Infinity、整数溢出及相同排序身份的冲突价格确定性
  失败；
- 完整日通过有限 symbol batch 构建，没有整日 Pandas materialization；
- 以 `16/64/256` 三个 symbol batch 候选验证结果一致并记录已观测最大 SH/SZ 输入日的耗时和
  峰值 RSS；进程 RSS 只作为实现观测，不在执行环境 owner 没有定义内存预算时成为正确性门槛；
- 最终验证固定使用三个预注册候选中内存占用最低且已在两项最大输入成功构建的 batch 16，
  不引入 batch 8、`large_string`、流式 writer 或缓存层；
- 在 `2025-11-18`、`2026-04-30`、`2026-07-27` 记录输入 identity、stock tick/输出行数、
  守恒误差、峰值 RSS、耗时和 Meta-hit 重跑；所有运行只写隔离 storage root；
- CLI-only，不改变现有 Level2 normalize、事实 workflow、HTTP Job、cron 或 MQTT；
- adoption 同步正式 owner、实现和测试，并由用户明确决定。

**Evidence（2026-08-29—2026-08-30，adoption candidate）**：

- 候选代码和本段证据仍位于 base `2ae615ee652f896c58d16ae398d75bcefd542c97` 之上的 dirty
  `feature/level2_feature` 工作树，没有绑定可恢复 commit，因而只能支持当前工作树中的 adoption
  候选，不能证明目标分支已采用或支持跨任务复现。项目锁定环境为 Python 3.13.13、
  PyArrow 25.0.0；2026-08-30
  重跑 `uv lock --check`、`compileall`、`git diff --check` 和全量 `pytest` 全部通过，共
  `543 passed`，另有两条仓库既有 multiprocessing `fork()` deprecation warning。Ruff、Black、
  Mypy 和 Pyright 不在当前项目环境中，未运行。
- 隔离根为 `/home/wsw/app/h02-validation-tAqNHU`，没有写入 `/home/wsw/app/data`。正式 processed
  payload 通过同 filesystem hard link 进入每个独立候选根；最终验证子根为
  `final-b16-zbA2dX`。隔离 Meta 从正式 `symbol_slices` 和相同 payload size 重新提交，不复制 raw
  upstream；trade calendar 也只重建 payload Meta。因此运行精确消费正式 processed payload，
  但隔离 Meta 不是正式 Meta 的逐字节副本，不能用来验证上游 raw lineage。
- 资源选择输入 identity 为：

  | 日期 / dataset | 正式 Meta SHA-256 | payload bytes | rows | row groups |
  | --- | --- | ---: | ---: | ---: |
  | `2026-07-21 sh_trade` | `82bb1b57112428c67a8b0d0d10e8a8e5564758470a69415c80f2dfca0d9db0b5` | 1,281,134,826 | 99,821,632 | 96 |
  | `2026-07-21 sz_trade` | `818771fa99b0dafa8f94eb924a545838964a664310f23b347ebfda645a010b34` | 1,506,676,613 | 119,385,454 | 114 |
  | `2026-01-14 sh_trade` | `03dbd5907a5737e58f170578aa7f0ed29ec6c97c25847adc68b226da7f482810` | 1,202,786,359 | 93,682,575 | 90 |
  | `2026-01-14 sz_trade` | `2959dd473b6ca8577a6172d7ba7fe41f749494714fbdfe20f477eae3638a2b09` | 1,805,634,569 | 143,133,762 | 137 |

- 已观测最大 SH 日 `2026-07-21` 的三个预注册候选均成功构建，且 SH/SZ 输出在
  `pa.Table.equals` 下跨 batch size 完全相等；payload bytes 不同，只反映 batch 产生的 Arrow
  chunk 边界进入同一默认 Parquet writer 后物理编码不同，不能用 payload digest 代替逻辑
  equality：

  | symbol batch | SH elapsed | SZ elapsed | process wall | peak RSS KiB | 原 `<= 2 GiB` 判断 |
  | ---: | ---: | ---: | ---: | ---: | --- |
  | 16 | 35.518s | 44.673s | 80.70s | 1,615,724 | 是 |
  | 64 | 30.408s | 38.865s | 69.82s | 2,922,044 | 否 |
  | 256 | 30.867s | 38.558s | 69.97s | 8,058,424 | 否 |

  batch 16 的 SH 输入含 3,421 symbols，产生 89,366,952 个 stock ticks、544,816 分钟行、
  12,274,002 bytes；SZ 输入含 4,080 symbols，产生 110,237,781 个 stock ticks、685,883
  分钟行、13,821,190 bytes。输出固定 13 字段非 nullable schema，包含 OHLC。
- 按当时错误的 RSS 门槛，64 和 256 在第一个资源选择输入被判定为不可能满足“两项最大输入都
  通过”，因而未在最大 SZ 日重复运行。batch 16 在已观测最大 SZ 日 `2026-01-14` 成功构建：
  SH 3,312 symbols、86,192,303 stock ticks、544,723 分钟行、33.777s；SZ 3,999 symbols、
  133,425,385 stock ticks、683,107 分钟行、52.078s；process wall 86.38s，peak RSS
  `2,103,548 KiB`。输出 payload 分别为 12,556,895 和 14,333,285 bytes。
- 严格 `2 GiB` 等于 `2,097,152 KiB`；batch 16 在最大 SZ 日超出 `6,396 KiB`。此前因此停止，
  没有运行 `2025-11-18`、`2026-04-30`、`2026-07-27` 最终验证、守恒 reference 或 Meta-hit
  重跑。该停止条件错误地把标准 Arrow `string/binary` 的单个可变长 Array 32-bit offset 上限
  解释为整个进程的 RSS 上限；二者没有这种关系。当前输出最大的已检查 `symbol` Array 为
  `2026-07-21 sz_stock_trade_1m` 的 685,883 行，offset buffer 为 2,743,536 bytes，data buffer
  为 4,115,298 bytes，并未接近单个 Array 的约 `2 GiB` 边界。`large_string` 只会把 offset
  改为 64-bit，不能降低进程 RSS，因而没有当前业务必要性。Acceptance 在最终验证前据此纠正：
  保留上述失败观测和三个候选结果，但不再把 `2 GiB` RSS 当成通过条件，也不新增 batch 8、
  流式 writer、列投影或缓存层。

- 最终验证输入 identity 如下；六个 payload 与正式文件均为相同 inode：

  | 日期 / dataset | 正式 Meta SHA-256 | payload bytes | rows | row groups | observed symbols |
  | --- | --- | ---: | ---: | ---: | ---: |
  | `2025-11-18 sh_trade` | `676d6f2a095c927596dfed63fac40bb57ea6187253f6a4c4c275d8bf3d8c793b` | 749,683,842 | 59,423,448 | 57 | 3,279 |
  | `2025-11-18 sz_trade` | `7ef3a56898fa154e2c17f1bfc7190ee562259482343f0c73ef6055d7da6357ad` | 1,068,481,337 | 86,389,788 | 83 | 3,976 |
  | `2026-04-30 sh_trade` | `734591031aff4c5af770037c4798383875c97e48ce54e0b1359b770276dc3810` | 958,259,213 | 75,951,750 | 73 | 3,347 |
  | `2026-04-30 sz_trade` | `ca62f77f462756fedd5627493a255e89e72490cc957ae781257b81f8c254d48d` | 1,194,305,070 | 96,804,129 | 93 | 4,020 |
  | `2026-07-27 sh_trade` | `036ce39aef3fbaf13111e1fa1bb1eb6b4b6f1338dcd38eb6b51ce05dfc012425` | 904,940,051 | 71,788,586 | 69 | 3,424 |
  | `2026-07-27 sz_trade` | `5f12cbe181108b3bfe161bc255be99f1f39431d0cd9e49e494291a1db3c50d5b` | 1,068,039,887 | 85,782,458 | 82 | 4,066 |

- 固定 batch 16 后，三个日期都通过真实 `data-level2-minute-backfill` CLI 首次构建；每个输出均为
  一个 row group：

  | 日期 / target | stock symbols | stock ticks | output rows | elapsed | payload bytes | payload SHA-256 |
  | --- | ---: | ---: | ---: | ---: | ---: | --- |
  | `2025-11-18 sh_stock_trade_1m` | 2,289 | 54,723,485 | 538,035 | 23.979s | 11,589,829 | `2a2ff8a4525fbdc00d87c80fcd01b57e40fedb7a0271e7e9b17dc97bbff6782e` |
  | `2025-11-18 sz_stock_trade_1m` | 2,868 | 80,781,621 | 678,859 | 34.776s | 13,313,548 | `f63c9cee764c0c6e45122e0fcf2fdc6d3dd2338db2ad03f72555814eef158df3` |
  | `2026-04-30 sh_stock_trade_1m` | 2,284 | 70,956,894 | 538,252 | 28.855s | 11,931,094 | `a411c30285a72d3a7f3a4a9cd632ee7eede6821930064ea8509ce67d9790027a` |
  | `2026-04-30 sz_stock_trade_1m` | 2,866 | 90,881,151 | 677,640 | 38.367s | 13,365,540 | `d7a4cf8f6ea02891f61bc192a38c5072bb9cdd914e77b0d7297410b427463809` |
  | `2026-07-27 sh_stock_trade_1m` | 2,307 | 65,641,207 | 536,578 | 28.582s | 11,506,367 | `bb019d0801ebee44832909bd52314f46f6e007add2f097602b3ed6df3500ac20` |
  | `2026-07-27 sz_stock_trade_1m` | 2,885 | 78,965,640 | 676,989 | 35.835s | 12,934,732 | `2e650315a1e898dd9a3a4219d43e3b7c917e9bf0c96deb8cd74002c52b854846` |

  | 日期 | CLI process wall | peak RSS KiB | Meta-hit pipeline |
  | --- | ---: | ---: | ---: |
  | `2025-11-18` | 59.87s | 1,389,456 | 0.013s |
  | `2026-04-30` | 68.32s | 1,532,268 | 0.015s |
  | `2026-07-27` | 65.54s | 2,930,424 | 0.014s |

  `2026-07-27` 的进程 RSS 超过 2 GiB 但构建成功，没有单 Array offset 错误；这再次表明 RSS 不能
  作为 Arrow 32-bit offset 的代理。三个日期的第二次 CLI 调用都对 SH、SZ 输出记录 `♻️`，六个
  payload 和六个 Meta 的 SHA-256、size 与 mtime 在调用前后均未变化。

- 守恒校验按不超过 1,048,576 行的输入 batch 扫描正式 payload；只过滤正式
  `security_type=stock`，整数逐 batch 求和后以 Python integer 精确比较，浮点逐 batch 和跨 batch
  均使用 `math.fsum`，再按预设容差比较：

  | 日期 / target | volume reference | signed volume reference | notional delta | signed notional delta |
  | --- | ---: | ---: | ---: | ---: |
  | `2025-11-18 sh_stock_trade_1m` | 59,404,525,942 | -638,937,410 | 0 | 0 |
  | `2025-11-18 sz_stock_trade_1m` | 78,864,219,160 | -650,530,012 | 0 | 0 |
  | `2026-04-30 sh_stock_trade_1m` | 65,615,414,523 | -267,857,706 | 0 | `-2.384185791015625e-7` |
  | `2026-04-30 sz_stock_trade_1m` | 73,744,974,511 | -168,068,563 | 0 | `1.1920928955078125e-7` |
  | `2026-07-27 sh_stock_trade_1m` | 50,460,127,580 | -24,387,416 | 0 | `-2.384185791015625e-7` |
  | `2026-07-27 sz_stock_trade_1m` | 57,122,765,888 | -58,106,663 | 0 | 0 |

  六项的 tick count、volume 和 signed volume 均精确相等；notional 全部零差，signed notional
  全部满足 `rel_tol=1e-12`、`abs_tol=1e-6`。独立校验还确认固定 13 字段 non-null schema、完整
  key 全局排序且唯一、分钟起点整除 `60_000_000`、`low <= open/close <= high`、输入输出 stock
  symbol 集相等，以及输出只有一个正确 direct upstream 且没有 `symbol_slices`。首轮只读校验器
  因 PyArrow 25.0.0 没有 `compute.mod` 在第一项检查中止；改用 NumPy remainder 后从六项开头完整
  重跑，上述结果全部来自成功重跑。

**Evidence（2026-08-31，正式分钟事实回填）**：

- 用户在 H02 adoption 后独立授权写入正式数据，并明确允许只排除 `2025-11-25`。正式根为
  `/home/wsw/app/data`；执行显式设置 `ENV=dev` 和 `ZERO_STORAGE_ROOT=/home/wsw/app/data`，因为
  当前 feature 工作树不存在 `.env.test`。该 CLI 只消费已提交的 calendar、`sh_trade/v1` 和
  `sz_trade/v1`，没有调用 broker。执行代码仍是 base
  `2ae615ee652f896c58d16ae398d75bcefd542c97` 之上的 dirty `feature/level2_feature` 工作树，尚无
  可恢复 adoption commit；正式写入成功不能替代 commit、merge、release 或 deploy 状态。
- 正式 calendar 在 `2025-11-05..2026-08-25` 内共有 197 个 session；其中
  `2025-11-25` 的 SH、SZ 逐笔输入都不存在，其余 196 日的 392 个输入 Meta、payload 和
  `symbol_slices` 全部通过读取边界。回填前两个分钟 dataset 都不存在任何分区。预检记录的输入
  payload 合计为 `409,943,861,418` bytes，SH 每日 observed symbols 为 3,264..3,450，SZ 为
  3,962..4,108；当次有序输入 identity 清单 SHA-256 为
  `5b158fe6867199a2ecff9c2b8e35f744fe966bd3fdf6d46ebc4dee27dcc296c5`。
- 为精确排除该日，正式 CLI 分两段运行并全部成功：

  | 目标闭区间 | sessions | 发布对象 | process wall | peak RSS KiB | exit |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | `2025-11-05..2025-11-24` | 14 | 28 | 14:10.49 | 1,637,348 | 0 |
  | `2025-11-26..2026-08-25` | 182 | 364 | 3:37:23 | 2,832,788 | 0 |

  两段均按日期升序、每日期 SH 后 SZ 完成；`2025-11-25` 的两个分钟分区保持不存在。正式输出
  汇总为：

  | dataset | objects | rows | stock ticks | payload bytes | rows / partition |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | `sh_stock_trade_1m/v1` | 196 | 105,920,642 | 12,788,839,003 | 2,321,099,021 | 507,904..546,258 |
  | `sz_stock_trade_1m/v1` | 196 | 133,417,236 | 17,425,478,128 | 2,636,085,858 | 672,820..686,067 |

- 对 392 个正式输出逐一读取完整 Parquet，并检查精确 13 字段 non-null schema、无 null、分区日期、
  非空 symbol、分钟起点整除 `60_000_000`、浮点有限、`low <= open/close <= high`、正
  `trade_count`、完整 key 全局严格升序且唯一、唯一正确 direct upstream 及无
  `symbol_slices`，全部通过。按日期升序且每日期 SH 后 SZ，对
  `dataset\tdate\tsize\tpayload_sha256\n` 求得输出 manifest SHA-256
  `c0faa242a81c00901385ab63cd50046d234b2e30b75fe30f1c6653125985253c`。前述三个预注册日期的
  六个正式 payload SHA-256 与隔离 Acceptance 输出 6/6 完全相同，因此正式输出继承了这六项
  已完成的逐笔守恒证据。本次全量发布后检查没有再次扫描约 410GB 的全部逐笔列来为其余 386 个
  对象重复计算守恒 reference，不能把全量结构检查表述为 392 项独立守恒复算。
- 首轮构建日志观测到 `2026-06-08` 与 `2026-06-09` 的聚合计数相同：SH 两日均为
  74,103,732 stock ticks、546,258 行，SZ 两日均为 97,570,757 stock ticks、684,640 行。只读
  复核显示两日上游也分别具有相同行数和 observed symbol 数，但 payload bytes、inode 和 Meta
  SHA-256 均不同；四个分钟 payload SHA-256 也各不相同，不是同一文件或相同输出被重复发布。
  按已确认的 source 权威语义，该现象只作为 broker 输入观测保留，不增加运行时外部复核、失败、
  填充或备用来源规则。
- 原样复跑两个范围分别命中 28 和 364 次分钟 Meta reuse，发布数均为 0，wall 分别为 1.36s 和
  3.29s，peak RSS 分别为 241,744 和 241,912 KiB，exit 均为 0。784 个正式 payload/Meta 文件
  的 path-sorted 内容指纹在复跑前后均为
  `7248b0be327affde773a00568bff1ac0b288af7b54ff4322a2d29b65d37c0b64`，
  path/inode/size/mtime 指纹均为
  `9a50872611cb88750432b94859a8af8842630b231e2b2bd000747a63ebd6a0d6`，证明复跑没有改写既有对象。

- **Conclusion**：合成测试和最大日构建证明当前 schema、OHLC、Access market scope、空结果、
  原子发布、lineage、batch-independent logical output 与固定 batch 16 的完整日执行可以运行；
  三个预注册日期的 schema、顺序、key、守恒、资源观测和 Meta reuse 也全部满足纠正后的
  Acceptance。最小正式关系是两个 exchange-specific sparse minute facts、一个领域 builder、
  一个发布 Step 和一个 CLI-only backfill workflow；不需要 `large_string`、流式 writer、HTTP、
  cron、MQTT 或日常 `data-level2` 集成。
- **Decision（2026-08-30—2026-08-31）**：用户明确采用 H02；随后独立授权正式历史回填，并明确
  允许只排除缺少两市逐笔输入的 `2025-11-25`。授权不包含 commit、push、merge、release 或
  deploy，也不把该单日操作选择提升为一般排除规则。
- **Formalized in**：[`level2_minute_contract.md`](../../docs/data/level2_minute_contract.md)、
  [`access.md`](../../docs/engineering/access.md)、
  [`cli_contract.md`](../../docs/engineering/cli_contract.md)、
  [`job_api_contract.md`](../../docs/engineering/job_api_contract.md)、
  [`storage_layout.md`](../../docs/data/storage_layout.md) 和
  [`offline_workflow_contract.md`](../../docs/offline_workflow_contract.md)。
- **Next**：2026-08-31 的正式回填记录覆盖当次范围内 196 个两市输入完整 session，
  `2025-11-25` 按当次授权保留缺口。Owner、实现和测试已随 PR #13 的
  `57d94ea073d1736e9d40e1126933f37978d6be48` 合入 `dev`。2026-09-08 只按本地 Git
  可恢复状态校正记录，未重新核验 release、deploy 或全部正式数据；H03 后续上游补齐事实
  记录在 H03，不改写本段历史验收范围。

## H03

- **Title**：14:30 Level2 Feature 与 T+1 VWAP Rank Label
- **Status**：`open`
- **Hypothesis**：固定 14:30 event-time cutoff、post-decision VWAP 窗口和 Feature 驱动的行集合，
  可以构造无未来泄漏且 key 完全对齐的 Level2 Feature/Label 数据集。
- **Why**：完整日 Level2 universe、T+1 成交是否存在或 14:30 后数据都不能反向决定 T 日 14:30
  样本；整数 session lookahead 也不能表达 Label 到 T+1 14:36 才成熟。
- **Scope**：一对共同采用的 V1 Feature/Label、固定三字段 key、14:30 universe、32 个 Feature、
  T/T+1 VWAP rank Label、完整 timestamp maturity、无 upstream 的对象 Meta 和 CLI-only 回填。
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

V1 固定绑定 `sh_stock_trade_1m/v1`、`sz_stock_trade_1m/v1` 与 `adj_factor/v1` 的计算语义，
但不把输入 identity 写入 H03 Meta，也不在 Meta hit 时读取当前上游证明是否仍可复用。固定
Feature/Label identity、version 和日期下，有效 Meta 就表示该对象可复用；输入版本或计算语义
变化必须产生新的 H03 version，不能在 `v1` 内动态失效旧对象。同版本输入内容修订不会自动
使旧输出失效；同尺寸内容替换也不属于当前 Meta 的检测保证。实际输入摘要、可恢复内容、源码
和环境由研究证据保存，不能以固定 `v1` 名称替代。候选完整契约位于
[`stock_1430_feature_label_contract.md`](../../docs/data/stock_1430_feature_label_contract.md)。

**Acceptance**：

- decision、visibility、entry、exit、maturity 和三字段 key 无歧义；
- 修改 14:30 及以后数据不改变 Feature，修改 entry/exit 窗口外数据不改变 Label；
- Feature 固定 32 列，计划窗口、null、tie/rank 和 signed proxy 解释通过手算测试；
- Label 与 Feature 的行、key、顺序完全一致，无效监督值保留 null；
- 有效 Feature/Label Meta hit 不读取分钟、factor、Feature payload 或当前上游，直接 reuse；
- 多个真实 T/T+1 对记录 universe、coverage、key digest、实际输入 identity、内容摘要及
  可恢复位置、耗时和峰值内存；
- adoption 同步正式 owner、实现和测试，并由用户明确决定。

**Implementation / Validation Evidence（2026-08-31）**：

- `feature/1430` 在基线 `57d94ea073d1736e9d40e1126933f37978d6be48` 上实现固定 V1
  Access、纯 Feature/Label builder、Feature-before-Label Step、CLI-only workflow 和请求构造。
  验收时七个 runtime 文件按
  `src/access/access.py`、`src/cli.py`、`src/data_system/builders/stock_1430.py`、
  `src/data_system/steps/_derived_partition.py`、`src/data_system/steps/stock_1430_build.py`、
  `src/jobs/requests.py`、`src/workflows/offline_daily_data.py` 顺序取得的 `sha256sum` 输出再做
  SHA-256，摘要为 `7d0af787258d30f9e0305d6281022d18fbb72458b9873a5a5b4ca44c6f9b47dc`。
- 手算、精确边缘分钟、tie/rank、窗口外变形、全 null Label、两市 Access、跨年下一 session、
  Meta-hit 零输入读取、关系字段拒绝、Feature 成功后 Label 失败续建、workflow、request 和 CLI
  回归均已覆盖。最终 `uv run pytest -q -W default` 为 `561 passed, 1 warning`；唯一 warning
  来自既有 parallel test 的 Python 3.13 `fork()` deprecation。
  `uv run python -m compileall -q src tests` 与 `git diff --check` 均成功。
- 可复跑记录为 `cd88f0c:research/stock-1430/h03_validation.ipynb`。Notebook 先通过正式 Meta
  `require()` 校验输入，再把 30 个 calendar/minute/factor payload 逐字节复制到
  `/tmp/stock-1430-h03-_uflhd74` 并重提隔离 Meta；规范输入 manifest SHA-256 为
  `cfe55cf061bb6b42cecf8c6e0520d9680ec90aab0ece38b1b6405a7dd14b54e7`。唯一缺失输入精确为
  `2025-11-25` 的两市 H02 minute；正式输入的 60 个 Meta/payload 文件在运行前后 SHA-256、
  size 和 mtime 全部不变。

| case | T -> T+1 | rows | valid labels | label null | key SHA-256 | first wall / peak RSS KiB |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| smoke | `2025-11-18 -> 2025-11-19` | 5,157 | 5,149 | 0.1551% | `3dc21f7b20b78dcac96c8c2aa099010ba7d81757d3e4fd61b58f6a087601a777` | 4.412s / 1,061,452 |
| final year boundary | `2025-12-31 -> 2026-01-05` | 5,170 | 5,158 | 0.2321% | `ac408d882baf848e83137e37295e4509740d86ba1e69e8d5a8370bed0e874f5d` | 4.363s / 1,078,640 |
| final holiday boundary | `2026-04-30 -> 2026-05-06` | 5,150 | 5,136 | 0.2718% | `f43c2e1e5ab369f0ce71ec04ce1cd97c8ed4fb3199ddff806850a4591134b849` | 4.569s / 1,068,188 |
| final regular pair | `2026-07-27 -> 2026-07-28` | 5,192 | 5,188 | 0.0770% | `abd4432917ecbb68f9f4d509c607fa3d475ec62647a3be27de076032e0d5921e` | 4.567s / 1,079,588 |

- 四个成功样本的 35/4 列 schema、非空唯一有序 key、Feature/Label key 精确相等、rank
  范围和无关系字段 Meta 全部通过。各日期/窗口的 observed-minute 平均覆盖为
  `96.874%..98.800%`；最高 Feature null rate 是 `2026-07-27` 的 60m edge rank
  `4.5069%`，符合“精确首尾分钟同时存在”规则，不使用 fallback。没有预定义资源 SLA，
  因此 wall/RSS 只记录为事实，不提升为性能通过声明。
- 已知负例 `2025-11-24 -> 2025-11-25` 返回非零：Feature Meta 已提交，Label Meta 不存在，
  错误精确来自缺少 T+1 两市 minute。四个成功目标原样 Meta-hit 重跑为
  `1.189s..1.228s`、`242,268..242,612 KiB`；Feature/Label payload 与 Meta 的 SHA-256、
  size 和 mtime 全部未变化。单元测试另以 fail-fast double 证明命中路径不读取分钟、factor、
  下一 session 或 Feature payload。

**Acceptance Review（2026-09-05，当前候选工作树）**：

- 本轮开始时七个 runtime 文件摘要与上述 `7d0af787...` 完全一致。按既有 H03 语义补强验收，
  修复分钟 Access 与 Label 读取 Feature 时未显式关闭 Parquet 的问题；删除 builder 对 Access
  已建立的分钟/factor 对象契约、合并后唯一性及 Step 对已验证 Feature Meta 的重复检查。
  输入校验责任保持在既定读取边界，输出公式、行集合和发布语义不变。
- 新增或加强四种计划窗口、独立 entry/exit 边界与 auction 排除、非等量成交 VWAP、两日不同
  factor、average-tie rank、无效 factor 保留 null、输入所有权、UTC decision key、有效空市场、
  空 Feature 拒绝、全 null Label 发布，以及两种对象的无效 Meta 不覆盖测试。最终版本的四个
  资源生命周期场景在验收前实现上全部失败，在修正后的实现上全部通过。
- 锁定环境为 Python `3.13.13`、NumPy `2.5.1`、Pandas `3.0.2`、PyArrow `25.0.0`；
  `uv.lock` SHA-256 为 `96125da32034e999352f8a0326f6bddb0c5a9408c8880a9197f2faebf5d4f512`。
  最终七文件按上述相同算法得到 runtime SHA-256
  `92c445cac375979a6b6082e30bcf434ab238dd0870343834456bc7d9a899ccee`；运行前后 226 个
  source/test/config 文件摘要不变，完整清单保存在 `code-manifest.json`。
- `uv lock --check`、`uv run --locked --no-python-downloads python -m pytest -q -W default`
  通过，结果为 **595 passed, 1 warning**；warning 仍是既有 parallel test 的 `fork()` 弃用提示。
  `compileall`、`git diff --check`、13 个 Python 文件的 filepath/测试 owner 镜像检查、11 个
  新增或修改 public API 的类型与具体 `Example:` 检查均通过。
- 使用临时工具环境中的 Ruff `0.16.6` 复核，H03 变更范围没有 lint 或格式问题。整文件扫描
  留有与 `dev` 基线逐项一致的 20 项 lint 告警，以及两个既有测试段落的格式差异；它们不属于
  本次 H03 语义单元，未借验收重写。仓库没有配置 type checker，环境也没有 Mypy/Pyright，
  因而未运行类型检查器，不声明全仓静态检查通过。
- `cd88f0c:research/stock-1430/h03_validation.ipynb` 已使用项目 `.venv/bin/python` 的 kernel
  从头执行。CLI 在复制的候选源码中使用固定无效凭证运行，只向独立存储写入；输入仍为原先
  固定的 30 个对象，manifest 摘要仍为 `cfe55cf0...`。没有重新选择日期、扩大样本范围或使用
  正式写凭证。

| T → T+1 | Feature 行数 | 有效 Label | 首次 wall 秒 | peak RSS KiB |
| --- | ---: | ---: | ---: | ---: |
| `2025-11-18 → 2025-11-19` | 5,157 | 5,149 | 2.655 | 1,165,928 |
| `2025-12-31 → 2026-01-05` | 5,170 | 5,158 | 2.585 | 1,134,980 |
| `2026-04-30 → 2026-05-06` | 5,150 | 5,136 | 2.531 | 1,121,072 |
| `2026-07-27 → 2026-07-28` | 5,192 | 5,188 | 2.598 | 1,119,208 |

- 四组样本独立核对了正式下一 session、14:30 前 universe、decision timestamp、35/4 列 schema、
  key/顺序、rank 范围与 coverage；行数、有效 Label、key 摘要和 null coverage 与前表一致。
  四次 Meta-hit 为 `1.049..1.091s`、`244,300..244,808 KiB`，输出 payload/Meta 的摘要、大小与
  mtime 未变。缺失 `2025-11-25` 两市分钟的负例仍保留 Feature 并使 Label 失败；60 个正式输入
  文件前后不变。资源用量没有预设 SLA，不作性能达标或提速结论。
- 另将验收前源码恢复到隔离目录，以相同锁定环境和固定输入重新构建四组样本；得到的全部
  **8 个 Feature/Label payload 与最终实现字节完全一致**。该对照证明本轮清理没有改变这四组
  输出。2026-08-31 的旧临时输出已不存在，旧 Notebook 未保存完整依赖快照，且其 payload
  字节摘要与本轮不同；本轮证据独立绑定当前版本，不把旧摘要当作跨环境字节复现证明。
- 当前执行目录是 `/tmp/stock-1430-h03-acceptance-cz7ci0tg/run-8bc8bkvg`。代码、Notebook、
  固定输入与输出、manifest、环境、完整 CLI 命令/失败 stderr、回归日志和源码对照记录归档于
  `/home/wsw/app/research-evidence/stock-1430-h03-2026-09-05-8bc8bkvg.tar.gz`。归档内容可恢复，
  配套 `.sha256` 文件校验归档；至少保留至 H03 采用/拒绝决定及对应证据审查结束，不依赖旧
  `/tmp` 目录的存续。

**共享发布边界维护（2026-09-06）**：

- 用户确认将共享函数改为无返回值的 `_publish_partition`，Calendar 也使用该边界；
  H03 的两个调用继续按 Feature-before-Label 顺序执行，不消费发布状态。模块从
  `_derived_partition.py` 改名为 `_partition.py`，Notebook 的 runtime 文件清单已同步。
  上述七文件算法使用新路径所得 runtime SHA-256 为
  `3791482ad34cd68b0cde25842186047313df8024ce7f3e5174565d0a5f5c1ab9`。
- 共享边界与 Calendar 回归覆盖新发布、Meta 复用、直接 upstream、空输出、构建/写入/Meta
  提交失败；空 raw 日历场景先在旧实现复现失败断言，再由修正后的实现通过。
  `uv run --locked --no-python-downloads pytest -q -W default` 为 **605 passed, 1 warning**；
  warning 仍来自既有 parallel test 的 `fork()`。本次七个 Python 文件的 Ruff lint/format、
  filepath/测试 owner 镜像检查，以及 `compileall`、`git diff --check` 通过。
- 本轮未重跑真实数据 Notebook；保存的输出仍属于 2026-09-05 的归档版本，Notebook 已显式
  标明这一点。历史实测结果不自动证明改名后的当前版本通过 H03 实测验收。

**第一性原理重设计与验证（2026-09-07）**：

- 用户确认以 H03 数据准备为核心，明确与现行回放的衔接和缺口，并授权执行评审方案。
  业务基线仍是 `dev` 的 owner；本轮在现有 `feature/1430` 候选中实施，不创建第二个研究入口，
  不把未合入的 H03、共享发布调整或 H04–H06 写成当前正式语义。
- 保留 workflow 的依赖组装、DataPipeline/Instrumentation 的执行边界，以及只承载 start、
  end、trade_dates 的 DataContext。具体 Step 拥有日期、身份、输入准备和结果日志；两个纯
  builder 拥有 Feature/Label 计算。没有新增 engine、meta/context 对象、结果包装或缓存。
- `build_stock_1430_features` 的日期改为 `datetime.date`；`build_stock_1430_labels` 改为
  接收 `feature_keys` 和两个 `date`。调用链在 Step 转换 Access 已解析的 session，下一 session
  只由 Access 确定。删除重复字符串日期解析、`T+1 > T` 检查、数值字符串 coercion 和输出自检；
  持久化 Feature key 的类型、缺失、唯一性、日期、时间和顺序校验保留在 Label builder。
- H02 schema/key 由 `level2_stock_trade_1m.py` 的 `STOCK_TRADE_1M_SCHEMA` /
  `STOCK_TRADE_1M_KEY` 声明，Access 复用同一份定义；H03 三字段 key schema 由
  `STOCK_1430_KEY_SCHEMA` 供两种输出与 Step 投影共用。Label 先 `meta.require()`，再通过返回的
  payload 路径只读 key 列。Calendar 和 adjustment-factor Parquet reader 在成功及异常时都关闭。
- 修复合法分钟整数在窗口内求和的溢出：以 `decimal128(38, 0)` 批量聚合，trade count 在精确
  总量上排名，除法才转换为 float64。`_sum_window_integers` 独立承担这一领域保证，Feature
  和 Label 共用；最长 60 个 H02 分钟的 int64 总量落在该精度范围内。Feature universe 仍来自
  全部可见分钟，但 Pandas 数值计算只转换最长窗口及其需要的列。
- `_publish_partition` 合并 reuse、miss 时同步调用构建能力、非空检查和发布，返回 `int | None`
  供 Step 日志消费。它保留 `_publish_parquet_object` 的 payload-before-Meta 实现，Calendar、
  Feature、Label 和 H03 同步更新必要调用方；Fact/Level-2 的既有发布调用和其他 broker 候选
  不变。两个局部 Feature callback 负责 miss 时的输入准备，绑定当前日期/已选 builder；Label
  直接绑定已有构建能力。没有加入第二套分区发布规则。
- `♻️` / `✅` 消息内容、次数和顺序保留，日志 source location 回到具体 Step。错误原样传播，
  无效 Meta 不覆盖，空 Feature 拒绝，全 null 非空 Label 可发布；Feature 已提交而 Label
  失败时仍保留 Feature。新 API 不兼容旧字符串日期或旧 `features=`，不保留旧不支持输入的
  异常文本。直接用 Arrow key 数组构造 Label 后不再写入 Pandas schema metadata，因而新建
  Label 的文件字节变化；规范 Arrow schema、行、值和顺序不变。有效 Meta 命中不迁移旧制品。
- `_local_epoch_us`、`_finite_values`、三种输出自检 helper、只供自检的派生列清单及独立的
  `_reuse_existing_partition` 删除。时间转换由既有 DateTimeUtils 承担，有限/null 规则在公式
  处建立，输出由固定 Arrow schema 构造。`_label_window_vwap` 和正数/带符号比率计算保留
  各自不同的领域规则；`_build_label` 保留持久化消费及跨 session 输入准备责任。

验证绑定如下：

- HEAD 为 `57d94ea073d1736e9d40e1126933f37978d6be48`；执行前源码另有完整快照，不能只用
  HEAD 代表 dirty 候选。最终 runtime 清单按 `access.py`、`cli.py`、`stock_1430.py`、
  `level2_stock_trade_1m.py`、`_partition.py`、`stock_1430_build.py`、`requests.py`、
  `offline_daily_data.py` 的仓库路径顺序，用上述双空格 `sha256sum` 清单算法得到
  `762624ec042906b7c6bcde36baf40c4bde4cd51a4bbc2e830b438dd42d0ea4bb`。
  此次是八文件摘要，不能与历史七文件摘要直接等同。完整 226 个源码、测试和依赖文件另有
  `code-manifest.json`，Notebook 运行前后及复制的代码快照中均未变化。
- 先在旧实现复现 12 个失败：正/负 signed-volume 大整数、相差 1 的跨 int64 trade-count
  总量、entry/exit 大 volume、Calendar/factor 成功与失败时的 reader 生命周期、两个 Label
  发布场景的 key 投影及 required Meta 失败传播。归档另保留适配旧 API 的回归源码、命令及
  重新执行结果 `12 failed, 78 deselected`，可独立恢复，不用旧失败日志替代可运行版本。
  新增的 6 类持久化 key 失效测试验证保留边界没有被输出自检删除误伤。
- 最终 `.venv/bin/python -B -m pytest -q -p no:cacheprovider -W default` 为
  **636 passed, 1 warning**；warning 仍是既有 parallel test 的 Python 3.13 `fork()` 提示。
  本次 12 个 Python 文件的 filepath、测试镜像、语法，以及 public API 的类型和 `Example:`
  检查通过；`git diff --check` 通过。
- Ruff `0.16.6` 的六条 lint 告警和两个文件的既有格式差异，按文件、规则、内容及变更前
  快照逐项比对一致，本次没有新增告警或格式差异；没有重写无关测试/聚合段落。
  隔离 uv 工具环境中的 Mypy `2.3.1` 检查本次 8 个源码文件通过，参数包括
  `--follow-imports=silent --ignore-missing-imports --no-incremental`，并使用项目解释器的类型
  依赖。该结果不声明无 stub 的 PyArrow 或全仓均通过完整静态检查。静态检查过程发现的
  callable 类型推断问题已用有类型的局部 callback 和 `partial` 修正，最终版本重新测试。
- Python `3.13.13`、NumPy `2.5.1`、Pandas `3.0.2`、PyArrow `25.0.0` 和 uv.lock 摘要保持上述
  2026-09-05 环境。Notebook 用项目 kernel 从头运行，只复制既定 30 个对象到隔离存储，
  input manifest 仍为 `cfe55cf061bb6b42cecf8c6e0520d9680ec90aab0ece38b1b6405a7dd14b54e7`；
  CLI 使用固定无效凭证，正式输入的 60 个文件 SHA-256、size、mtime 前后不变。

| T → T+1 | Feature 行数 | 有效 Label | 首次 wall 秒 | peak RSS KiB |
| --- | ---: | ---: | ---: | ---: |
| `2025-11-18 → 2025-11-19` | 5,157 | 5,149 | 2.725 | 1,097,312 |
| `2025-12-31 → 2026-01-05` | 5,170 | 5,158 | 2.704 | 1,074,608 |
| `2026-04-30 → 2026-05-06` | 5,150 | 5,136 | 2.730 | 1,128,264 |
| `2026-07-27 → 2026-07-28` | 5,192 | 5,188 | 2.667 | 1,087,764 |

- 四组 key 摘要、coverage、35/4 列 schema 与已登记样本一致。将执行前源码在同一隔离输入上
  重建：8 个输出逐字段 schema/值/行序全部相等；4 个 Feature payload 字节也完全相等，
  4 个 Label 仅因不再携带 Pandas metadata 而字节不同。大整数修复由上述专门反例证明，
  不用普通样本输出相同推断该缺陷不存在。
- 已知 `2025-11-24 → 2025-11-25` 缺失输入负例仍返回非零、保留 Feature、不提交 Label。
  四组 Meta-hit 耗时 `1.061..1.105s`、peak RSS `244,096..244,516 KiB`，payload/Meta 摘要、
  size 和 mtime 不变。没有预设资源 SLA，不声称性能达标或 alpha 有效。
- 最终执行目录为 `/tmp/stock-1430-h03-redesign-cb53htly/run-cxq87ato`。最终代码/Notebook、
  固定输入和两版输出、执行前源码、回归失败、工具结果及完整命令记录归档于
  `/home/wsw/app/research-evidence/stock-1430-h03-2026-09-07-cxq87ato.tar.gz`，配套 `.sha256`
  文件校验归档；至少保留至 H03 采用/拒绝决定及证据审查结束。
- 现行 daily replay 的日级 schedule、两字段数据加载、模型 cutoff 和成交时点不能直接表达
  H03 的三字段 grid 与 Label maturity。衔接仍分别由 H04 的可见融合输入、H05 的 timestamp
  训练/模型制品、H06 的 snapshot/执行隔离负责；它们保持 `open`，本轮没有新增回放默认值、
  模型选择或交易副作用。


**正式路径回填证据（2026-09-07 执行，2026-09-08 补记）**：

- **版本边界**：以下事实绑定 `feature/1430` 的
  `cd88f0cab024af8d1c2f5e5d0bb762badeb9d5ed`。该执行版本的 H03 Meta 只有 `payload` 和
  `size_bytes`，不写 `upstream` 或 `symbol_slices`，有效 Meta 直接复用。2026-09-08 补记时，当前分支的
  Scope/Acceptance 仍要求多直接 upstream 与输入变化失效；补记只记录历史事实，没有选择
  设计。本轮收敛决定见下文；以下统计仍只属于该执行版本，不自动证明本轮修改通过验收。
- **执行授权与状态**：用户要求 H03 回填至正式路径，确认只回填输入完整的日期并记录缺口，
  随后要求优先补齐 `2026-08-25`。当次执行前工作树干净，运行代码未改；H03 研究状态为
  `open`，当次未执行 commit、push、merge、release 或 deploy。
- **代码与环境**：证据目录中的 `source.tar` 保存该 commit 的完整源码快照；八文件
  `runtime.sha256` 清单的 SHA-256 为
  `762624ec042906b7c6bcde36baf40c4bde4cd51a4bbc2e830b438dd42d0ea4bb`。
  实际环境为 Python `3.13.13`、NumPy `2.5.1`、Pandas `3.0.2`、PyArrow `25.0.0`，
  `uv.lock` SHA-256 为 `96125da32034e999352f8a0326f6bddb0c5a9408c8880a9197f2faebf5d4f512`。
  执行前 H03 相关测试 **116 passed**，所涉及的 broker、normalize、分钟与 Fact 测试
  **109 passed**；命令与结果保存在 `tests.json`。H03 和分钟 CLI 从源码快照运行，使用无效
  占位凭证；补齐上游的 FTP 下载使用当时的开发环境配置，证据不保存真实凭证。
- **补齐 `2026-08-25`**：本日分钟与 factor 已齐，缺少的是下一正式 session `2026-08-26`
  的两市逐笔及分钟对象。通过既有 `FactMaterializeStep` 只选择 `sh_stock_ordertrade` 和
  `sz_trade`，发布两个 raw、两个 processed 对象，再运行单日分钟回填 CLI。新增 SH/SZ
  分钟分别为 **538,805 / 680,782 行**，对应 **58,763,510 / 74,714,133** 笔 stock trades。
  完整分钟结构检查通过；扫描两市逐笔后，trade count、volume、signed volume 精确守恒，
  notional 与 signed notional 在预定 `rel_tol=1e-12, abs_tol=0` 下守恒。分钟 CLI 复跑命中
  两个 Meta，四个分钟 payload/Meta 的内容和文件身份保持不变。
- **正式路径与范围**：H03 写入 `/home/wsw/app/data` 下的
  `features/l2_stock_1430/v1/trade_date=T/{data.parquet,meta.json}` 与
  `labels/l2_stock_1430_t1_vwap_rank/v1/trade_date=T/{data.parquet,meta.json}`。
  按以下顺序执行 `data-stock-1430-backfill --start S --end E`，三次退出码均为 `0`：

  | 目标闭区间 | Feature/Label 对数 | process wall 秒 | peak RSS KiB |
  | --- | ---: | ---: | ---: |
  | `2026-08-25..2026-08-25` | 1 | 2.766 | 1,099,740 |
  | `2025-11-05..2025-11-21` | 13 | 23.104 | 1,282,056 |
  | `2025-11-26..2026-08-24` | 181 | 326.863 | 1,418,240 |

- **输出与校验**：共 **195 对、390 个对象**，Feature 与 Label 各 **1,010,042 行**；
  有效 Label **1,008,708**，null **1,334**，每日有效覆盖率 **99.0342%..100%**。
  `2026-08-25` 的两种输出各 **5,207 行**，有效 Label **5,203**，null **4**。全部对象经
  Meta 取得 payload 后完整读回，35/4 列精确 schema、三字段 key、分区日期与 14:30 时间、
  顺序和唯一性、逐行 Feature/Label 对齐、有限值及 rank/observed ratio 范围均通过。
  原样复跑三次调用，共 **390 次 Meta reuse、0 次发布**；**780 个 payload/Meta** 的 SHA-256、
  size、mtime 和 inode 全部不变，优先生成的 `2026-08-25` 分区也保持不变。
- **输入身份**：所需输入覆盖 197 个 session 和两个年度日历，共 **593 个直接对象、
  1,186 个文件**。`inputs-ready.json` 与 `inputs-final.json` 内容相同，SHA-256 均为
  `b47bbf9cd1cc49676b4bc78345b0841f433a7d6bccab88b22a7b0ed4341da144`。清单保存直接
  payload/Meta 的内容摘要及文件身份，并记录读取边界涉及的直接 upstream Meta 摘要与
  payload 文件身份；补齐前已有输入也与 `inputs-initial.json` 一致。
- **保留缺口**：`2025-11-24` 缺少 T+1 `2025-11-25` 的两市分钟输入；`2025-11-25` 缺少
  本日两市分钟输入。两日 H03 Feature/Label 分区均未生成。这是用户确认的本次日期范围，
  不建立自动跳过、替代输入或整日缺失转 null 的规则。
- **可恢复证据**：目录为
  `/home/wsw/app/research-evidence/stock-1430-h03-formal-backfill-2026-09-07-be972d1_/`。
  `run.json` 保存授权、版本、环境和结果；`source.tar` 与 `runtime.sha256` 绑定源码；
  `*.command.json`、对应 `.log` 和 `.time.txt` 保存命令、退出码及资源观测；
  `upstream-validation.json`、`upstream-reuse.json` 保存新增上游的摘要、守恒和复用结果；
  `h03-validation.json`、`h03-aug25-validation.json`、`h03-reuse.json` 保存逐日期结果与
  逐文件身份。实际验证脚本和输入清单均保留于同一目录。`SHA256SUMS` 覆盖 333 个证据文件，
  其自身 SHA-256 为 `9713870d8c80bfe1e9fa169a8bc34e52cd690ae2e3c916d14f24128e75150327`；
  2026-09-08 补记时校验通过，统计与清单一致。证据至少保留至 H03 采用/拒绝决定及对应审查结束。
  本次未重新回填或运行历史测试，以上数据状态与测试结果均属于所绑定的 2026-09-07 执行。

**最小实现收敛（2026-09-08）**：

- **Decision**：用户在复核差异后要求执行建议，确认 H03 V1 使用无 upstream 的 Meta、固定
  版本和有效对象直接复用；本轮不保留“任意直接输入变化禁止 reuse”的候选要求。该选择不改变
  时间、universe、32 列、VWAP、rank 或 null 规则，也不检测同版本上游修订。
- **Scope**：以 `dev@57d94ea073d1736e9d40e1126933f37978d6be48` 为基线，在
  `feature/intraday_feature` 中整理 H03 最小实现。沿用 `cd88f0c` 的 Access、builder、Step 和
  CLI 行为，但复用现有 `_publish_derived_partition`，并按正式存储契约拒绝 Feature/Label
  Meta 的 `upstream` / `symbol_slices`。Broker API、Calendar/Fact 装配及通用发布重构仍只保留
  在 `feature/1430@cd88f0c`，不属于本轮采用差异；H04–H06 仍独立 open。
- **验证范围（运行前固定）**：锁定现有依赖，执行全量回归；真实验证继续使用
  `2025-11-18 → 2025-11-19`、`2025-12-31 → 2026-01-05`、
  `2026-04-30 → 2026-05-06`、`2026-07-27 → 2026-07-28` 四组既定样本，以及
  `2025-11-24 → 2025-11-25` 缺少分钟输入的负例。只使用归档的固定 30 个输入对象，
  只向隔离存储写入；四组新建输出与 `cd88f0c` 在相同输入/环境下逐字段 schema、值和顺序
  精确相等，Meta 精确无关系字段，重跑不改写输出，缺失负例保留 Feature 并使 Label 失败。
  任一断言失败即停止验收、保留失败并修复，不替换样本、放宽比较或宣称 alpha；wall/RSS
  只作观测。本轮不重填正式路径，不补齐 `2025-11-25`，不选择模型或收益阈值。

**本轮 Evidence（2026-09-08）**：

- 证据目录为 `/home/wsw/app/research-evidence/stock-1430-h03-alignment-2026-09-08-3v8icszm/`。
  本轮源码仍是 `57d94ea` 上的未提交修改；`source.tar` 保存最终源码，`runtime.sha256` 绑定
  全部源码、测试及依赖文件，其 SHA-256 为
  `f02d9e45dceaaeaeeffe58551ecdc90b131beb0e83c7dd9c4d01593a318b6aa6`。
  `unit-run-1/source` 是全量测试和真实验证实际使用的代码快照；与最终工作树的 runtime、
  tests、`pyproject.toml`、`uv.lock` 内容逐文件一致，后续只补充文档与证据记录。
- `uv lock --check` 通过；项目 uv 环境的 Python `3.13.13`、NumPy `2.5.1`、Pandas `3.0.2`、
  PyArrow `25.0.0` 与既定基线一致，`uv.lock` 摘要仍为 `96125da3...f512`。
  `.venv/bin/python -B -m pytest -q -p no:cacheprovider -W default` 在代码快照上为
  **613 passed, 1 warning**；warning 仍来自既有 parallel test 的 Python 3.13 `fork()` 提示。
  新增两个关系字段拒绝场景在 `57d94ea` 上先复现 **2 failed**；带入的 Calendar/factor
  reader 成功与异常关闭场景在该基线上复现 **4 failed**，六项在最终全量回归中均通过。
- Ruff `0.16.6` 的当前 20 项 lint 告警与基线逐文件、规则、消息一致；三个文件的格式差异
  在基线也存在，本轮未改写无关段落。Mypy `2.3.1` 检查八个源码文件，保留
  `create_backtest_submission.normalized_strategy` 的一项既有 `var-annotated` 告警，已在
  `57d94ea` 复现；没有新增类型告警，但不声明全部静态检查通过。15 个 Python 文件的语法、
  filepath、测试 owner 镜像及新增 public API 的类型与 `Example:` 检查通过。
- 真实验证使用既定 30 个归档输入，manifest SHA-256 仍为
  `cfe55cf061bb6b42cecf8c6e0520d9680ec90aab0ece38b1b6405a7dd14b54e7`。输入归档为
  `/home/wsw/app/research-evidence/stock-1430-h03-2026-09-07-cxq87ato.tar.gz`，其 SHA-256
  为 `91397bf2352cb16c19ce62df62a6e0868149c36d5db8c10094c672975a6d0997`；本轮所有输入和
  输出也保存在证据目录。CLI 只使用隔离存储和无效占位凭证，不继承生产凭证，不写正式路径。
- 第一轮验证脚本用制表符序列化 key，而历史 Notebook 的 `_key_digest` 使用 `|`，因此在
  历史摘要比较处失败。按 `cd88f0c` 中的原算法修正验证脚本后完整重跑；运行代码、输入、
  样本和验收断言均未改变。`validate_alignment_v1.py`、`real-run-1`、对应失败日志与命令
  保留，修正依据在 `validation-script-correction.json`，成功运行记录在 `real-run-2`。

| 目标 T | Feature/Label 各行数 | 有效 Label | 首次 wall 秒 | peak RSS KiB |
| --- | ---: | ---: | ---: | ---: |
| `2025-11-18` | 5,157 | 5,149 | 2.73 | 1,089,156 |
| `2025-12-31` | 5,170 | 5,158 | 2.75 | 1,110,004 |
| `2026-04-30` | 5,150 | 5,136 | 2.73 | 1,105,080 |
| `2026-07-27` | 5,192 | 5,188 | 2.70 | 1,089,456 |

- 四组共八个新建 payload 与同一输入/环境下重建的 `cd88f0c` 输出，逐字段 schema、值和行序
  精确相等；三 key、历史 key digest、35/4 列 schema、rank 范围、null coverage 与记录一致。
  新实现分别复用旧版本和本轮版本的四组输出，共 16 个对象、32 个 payload/Meta 文件的
  SHA-256、size、mtime 与 inode 全部不变。三个隔离输入副本的内容和文件身份也前后不变。
  `2025-11-24` 负例因缺少 `2025-11-25` 分钟返回 `1`，Feature 保留，Label payload/Meta
  均未发布。资源观测不构成 SLA 或 alpha 结论。
- `validate_alignment.py`、命令、环境、输入 manifest、新旧输出、复用和失败记录均可恢复；
  `SHA256SUMS` 覆盖本轮证据文件。证据至少保留至 H03 采用/拒绝决定及对应审查结束。

- **Conclusion**：H03 的无 upstream 方向已确认，本轮最小实现通过上述技术验收；历史 195 对
  回填仍只属于 `cd88f0c` 的执行事实。本轮未验证全历史质量或收益，正式化尚未合入，状态
  保持 `open`。
- **Next（2026-09-11 更新）**：将最终拟议 owner、实现、测试和可恢复证据同步送审。候选实现
  已提交为 `feature/intraday_feature@5219ae2c7c9fd8430e2e29e34da7d07b05f99d61`，
  尚未合入本地 `dev@57d94ea`；采用修改合入 `dev` 后才成为正式采用事实。上文 2026-09-08
  Evidence 中的未提交状态保留为当次运行记录。

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

- **Next**：先独立决定本 Change 的 two-upstream 要求。H03 V1 的无 upstream 选择不自动
  改变 H04；现行存储 owner 只支持单 upstream 且 Feature/Label 不写该字段，若保留本候选
  要求，必须先定义输入 identity、失效及已有对象处理的存储契约，再实现融合。H03 schema
  稳定后，在独立实现分支验证 P/T 无泄漏和 key 对齐；在 H05 预注册比较前不选择或删除七列。

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
