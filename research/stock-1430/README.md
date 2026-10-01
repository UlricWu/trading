# 股票 14:30 数据基础与历史研究记录

## 目标与背景

本卷宗保留股票 14:30 日频输入、分钟事实及 L2 Feature/Label 的采用记录和历史证据。
原目标还包括融合、模型资产与受限回放；2026-10-01 用户决定将后续研究集中于
[subscription-training](../subscription-training/README.md)，本卷宗不再新增研究假设。
正文只拥有 H01–H03 的状态、结论和决定，正式行为仍由各段引用的 `docs/` owner 拥有；
治理见[研究工作流](../../docs/engineering/research_workflow.md)。

研究起点（2026-09-11，`dev@57d94ea`）：既有训练/回测的日频两字段 key、daily timing
及 experiment artifact，不能证明三字段 grid、完整 timestamp maturity、模型 cutoff
和执行窗口隔离，也不足以绑定全部代码、输入、环境与随机性。

先读各假设摘要，再按 Evidence 定位 Notebook 的方案、结果与恢复入口。
完整历史记录冻结于 `c2b84bc4533d1e38fe826f55c0cff91b5504bf94:research/stock-1430/README.md`
（可用 `git show` 恢复）；它只作历史证据，当前状态和结论以本文件为准。
本次整理是本地 draft，未生成新实验或改写原冻结证据。

## 依赖与 Change 索引

```text
H01 daily_feature
H02 minute_facts → H03 l2_datasets
```

H01/H02 可独立判断，H03 依赖 H02。依赖不代表排期或统一生命周期；新时期、输入或
反例的实质研究另建研究记录，并链接本卷宗的已有结论；具体方案的独立采用按研究工作流管理。

- [H01 日频 Feature 依赖感知回填](#h01)
- [H02 Level2 股票分钟事实](#h02)
- [H03 14:30 Level2 Feature 与 T+1 Label](#h03)

## 共同解释边界

数据构建正确不证明预测价值；Rank IC 不证明扣费收益，window-VWAP 回放不证明真实
排队、冲击、容量或未来表现。因子选择及增量归因须事前固定候选、选择/最终验证数据、
指标和停止条件；新的研究问题另建记录，具体方案的独立采用按研究工作流管理。

实际读取时的输入校验、已有输出的复用和实验复现分别判断。H02 的一跳源对象验证仍由
Meta/Access 边界承担，下游不复制；免读上游不免除构建/消费时的契约校验。
有效对象和固定版本名不证明历史输入未修订或实时就绪；相关输入、基线、Scope 或
Acceptance 实质变化后重验，wall/RSS 没有预设 SLA 时只作观测。

**后续数据维护（2026-09-22）**：当前正式库的
[Level-2 缺失情况与异常处理](../../docs/data/source_contract.md#level2-data-status)已更新：
`2025-11-25` 原始数据、沪深逐笔与分钟，以及 `2025-11-24/25` 的 H03 Feature/Label 已补齐；
`2026-06-08/09` 的旧 raw 跨日重复异常已按新交付数据修复。下文各历史运行中的缺口、负例、
聚合计数与哈希保留当时事实，不代表当前正式库状态；修复没有重跑或改写冻结实验。
`2026-09-21` 新交付文件存在字段编码和序号变化，标准化未完成；用户要求删除该日正式数据，
全库核查无该日分区，实际删除0个对象。其影响的 `2026-09-18` H03 Label 仍缺失，详见
[字段差异与正式库清理核查](../../docs/data/source_contract.md#level2-format-2026-09-21)。

## H01

- **Status**：`adopted`
- **Hypothesis**：同一依赖感知 Feature Step 可承担日常派生与精确历史回填，
  独立 facts 冷启动显式准备 warm-up，不隐式扩大请求范围。
- **Scope**：`data-standard` 顺序物化 enabled Feature/刚成熟 Label；CLI-only
  `data-standard-bootstrap` 只构建显式 facts 区间，`data-feature-backfill` 精确选择
  一个 set/version 与目标闭区间。不含新 HTTP/cron、隐式 warm-up、刷新或覆盖对象。
- **Depends on**：无其他 Change；使用正式 calendar、daily facts、builder 和 Meta。
- **Acceptance**：[冻结验收口径][h01-history]包含 Calendar → Facts → enabled Feature
  → mature Label、全部 disabled 时 warning/空 Step 成功、V1 唯一的 `lookback_sessions=61`
  权威、精确目标范围及周末/长假/缺失边界、单 symbol 历史不足为 null 而空输出失败、
  有效 Meta 免读上游与部分成功续建。Feature 回填不调用 broker、不写 facts/Label，
  facts 冷启动不写 Feature/Label；两者均为 CLI-only、不是 HTTP Job kind。
  真实验证从 `2019-01-01` 解析 61 个 warm-up 和随后 62 个目标 session，保存输入、
  coverage、资源与复用结果；采用须同步 owner、实现、测试并由用户决定。
- **Evidence**：[2026-08-28—29 验收及正式回填][h01-history]绑定候选
  `b1bff7445fd9ce180b8fe7e0665829702dd15b9c`、正式写入版本
  `5b0d4ede2146748511351c97ead5e372e0fd32f8` 和锁定环境。
  保留错误 Conda 环境的 5 项失败、空 `stock_basic/stock_st` 的冷启动失败及修复、
  外部 ST 资料差异、逻辑 string 检查修正。最终锁定回归 515 项通过；62 个 Feature
  共 224,916 行，正式 Feature 与隔离输出逐字节相同，186 个 Label 和幂等检查通过。
- **Conclusion**：验收通过，同一组最小 Step 足以承担三条路径。Tushare broker
  记录集合按 source 契约信任，空响应在 normalize 构造成可消费空对象；外部差异不
  触发运行时复核、备用来源或填充。
- **Decision（2026-08-29）**：用户明确采用，并授权测试部署及正式 Feature/Label 回填。
- **Formalized in**：[source](../../docs/data/source_contract.md)、
  [daily Feature/Label](../../docs/data/daily_feature_label_contract.md)、
  [Access](../../docs/engineering/access.md)、[CLI](../../docs/engineering/cli_contract.md)、
  [Job API](../../docs/engineering/job_api_contract.md)、
  [offline workflow](../../docs/offline_workflow_contract.md)。
- **Next**：owner、实现和测试随 PR #12 的
  `2ae615ee652f896c58d16ae398d75bcefd542c97` 合入 `dev`，记录中的派生回填已完成。
  2026-09-08 状态校正只核对本地 Git，未复核 release、deploy 或当前正式数据；
  新反例另建研究记录。

## H02

- **Status**：`adopted`
- **Hypothesis**：两市正成交逐笔可聚合为守恒、稀疏、phase-aware 的一分钟事实，
  消除下游重复扫描/定义，并通过有限 symbol batch 完成整日构建。
- **Scope**：`sh_stock_trade_1m/v1`、`sz_stock_trade_1m/v1` 的固定 schema/key、OHLC、
  单 upstream、原子发布及 CLI-only 回填；Access 可显式选择交易所，缺省仍为全市场。
  tick-signed 值只是方向代理；缺行不等于零成交，不含 dense grid、补零、order-book、
  Feature/Label、FTP、HTTP、cron、MQTT 或日常 `data-level2` 集成。
- **Depends on**：无其他 Change；使用正式两市 trade、stock 和 phase 语义。
- **Acceptance**：[冻结验收口径][h02-history]要求 tick/volume/signed-volume 整数精确守恒，
  notional/signed-notional 对 `math.fsum` 满足 `rel_tol=1e-12, abs_tol=1e-6`；
  OHLC、phase/午休 sparse、唯一有序 key、有效空 stock 与缺失输入区分、数值失效/
  溢出/冲突价格失败及一跳 lineage/reuse 均验证。整日禁止整体转 Pandas；
  预注册 batch 16/64/256 比较输出与最大输入资源，最终固定 batch 16，
  在 `2025-11-18`、`2026-04-30`、`2026-07-27` 隔离验证及复跑；
  RSS 不作为未由 owner 定义的正确性预算，采用须同步 owner、实现、测试并由用户决定。
- **Evidence**：[2026-08-29—31 验收、正式回填及 2026-09-13 恢复核对][h02-history]。
  保留误将 Arrow 单 Array offset 上限当成进程 RSS 预算的停止记录，以及修正后六项
  守恒通过的事实。全量回归 543 项通过；196 个 session、392 个正式对象完成结构检查
  和免改写复用，仅六个预注册输出具有独立逐笔守恒复算，不能推广为全量守恒复算。
  恢复核对见 `/home/wsw/app/research-evidence/stock-1430-evidence-2026-09-13-a74ry7d6/h02-recovery-audit.json`
  和 `h02-recovered-outputs/`：六个输出可恢复，但缺历史程序、命令、环境及精确 dirty
  源码/输入绑定，不能声称完整实验可复跑；不新增 Notebook 弥补这些缺失。
- **Conclusion**：纠正后的验收通过。两个交易所 sparse minute facts、领域 builder、
  发布 Step 与 CLI 已足够，无需 `large_string`、流式 writer 或新运行入口。
- **Decision（2026-08-30—31）**：用户明确采用，随后独立授权正式回填并仅排除
  `2025-11-25`；该单次选择不成为一般排除规则，授权不含 Git/发布/部署操作。
- **Formalized in**：[minute facts](../../docs/data/level2_minute_contract.md)、
  [Access](../../docs/engineering/access.md)、[CLI](../../docs/engineering/cli_contract.md)、
  [Job API](../../docs/engineering/job_api_contract.md)、
  [storage](../../docs/data/storage_layout.md)、[offline workflow](../../docs/offline_workflow_contract.md)。
- **Next**：owner、实现和测试随 PR #13 的
  `57d94ea073d1736e9d40e1126933f37978d6be48` 合入 `dev`；历史回填范围保留当时缺口，
  后续补齐不改写旧验收。找到原始程序/输入时校正恢复引用，新数据实质验证另建研究记录。
  2026-09-08 校正未复核 release、deploy 或全部正式数据。

## H03

- **Status**：`adopted`
- **Hypothesis**：固定 14:30 cutoff、post-decision VWAP 窗口与 Feature 驱动行集合，
  可构造没有计算未来泄漏、key 完全对齐的 Level2 Feature/Label。
- **Scope**：共同采用 `l2_stock_1430/v1` 与 `l2_stock_1430_t1_vwap_rank/v1`，
  三字段 key、5/15/30/60 计划分钟共 32 列；Feature 只看 T 日 14:30 前 CONTINUOUS
  stock，Label 使用 T/T+1 的 `[14:31,14:36)` VWAP 与两日 factor，T+1 14:36 成熟。
  Label 继承 Feature 行/key/顺序，无效监督为 null；无 upstream Meta、CLI 回填及
  日常物化。不含融合、模型、交易、资格过滤、实时源或新 HTTP/cron 入口。
- **Depends on**：H02。
- **Acceptance**：[冻结方案](h03_validation.ipynb#h03-protocol)要求时间/key 无歧义、
  未来及窗口外变形不影响对应输出、窗口/null/tie/rank/signed proxy 手算通过、
  Label 精确继承 Feature、有效 Meta 免读分钟/factor/Feature/当前上游；
  固定四组 T/T+1 和缺失分钟负例，保存输入内容、恢复副本、coverage、资源与复用结果。
  2026-09-20 日常接入另覆盖到达日 maturity、缺失输入失败/重试、休市与 cron 日期，
  采用须同步 owner、实现、测试并由用户决定。
- **Evidence**：[历史运行与恢复入口](h03_validation.ipynb#h03-evidence)定位
  2026-08-31—09-13 的归档、预设负例、资源生命周期/大整数回归及准备失败。
  Notebook 保存运行 `h03-run-sl1ucp7y` 绑定 `9e5f9bb`、30 个归档对象和 `cd88f0c`
  参考源码：53 项定向回归通过，四组共 20,669 行、八个 payload 精确一致，16 个对象
  复用身份不变，缺失 T+1 分钟时保留 Feature、不发布 Label。
  [2026-09-14 集成验证](h04-validation.ipynb#h04-integration-20260914)仍使用相同输入/断言。
  [2026-09-20 日常接入证据](h03_validation.ipynb#h03-daily-20260920)绑定
  `d675500bbbe0e04f821dc270480b965490183f78`：713 项非 contract 测试和 1 项真实 7zip
  contract 测试通过，修复前失败保留；领域公式/schema/时间边界未改。
- **Conclusion**：固定 V1 数据计算与物化契约已采用；日常调用复用同一领域实现，
  历史回填仍使用目标日期。隔离构建与历史 195 对回填不证明全历史质量、实时就绪、
  因子有效性、模型表现或收益，历史运行只支持各自绑定版本。
- **Decision**：2026-09-08 用户确认无 upstream、固定版本、有效对象直接复用，
  不检测同版本上游修订；2026-09-20 明确采用既有数据集并将分钟、当日 Feature/
  当天成熟 Label 接入 `data-level2`，恢复每日任务、补齐至 `2026-09-18`。
  决定不采用 H04、不生成融合 Feature、不增加告警。
- **Formalized in**：[Feature/Label](../../docs/data/stock_1430_feature_label_contract.md)
  拥有数据语义，[offline workflow](../../docs/offline_workflow_contract.md)拥有到达日编排；
  CLI、HTTP、cron 分别遵循其工程 owner。
- **Next**：部署、正式回填与 cron 安装分别核验，不能由采用记录或 merge 代替运行证据。
  后续缺口见共同背景；新时期/输入/反例另建研究记录，不向本段追加实质实验。

## 后续研究

2026-10-01 按用户决定，原 H04 融合候选迁至
[subscription-training H09](../subscription-training/README.md#h09)，研究状态和结论只在
该段维护。[融合验收 Notebook](h04-validation.ipynb) 留在原路径保存历史运行和恢复入口。
原 H05/H06 固定训练与受限回放草案没有实验证据，不再推进；保留的训练/执行前提及
数据版本想法见[后续研究边界](../subscription-training/README.md#未来想法)。
这表示本卷宗结束后续研究，不表示原完整训练/回放目标已完成或被实验否定。

本次迁移与清理是本地工作树 draft，尚未 commit 或合入目标分支；没有新实验、
release、deploy 或正式数据写入，已采用的数据契约和原冻结证据保持不变。

[h01-history]: https://github.com/UlricWu/trading/blob/c2b84bc4533d1e38fe826f55c0cff91b5504bf94/research/stock-1430/README.md#h01
[h02-history]: https://github.com/UlricWu/trading/blob/c2b84bc4533d1e38fe826f55c0cff91b5504bf94/research/stock-1430/README.md#h02
