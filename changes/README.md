# 候选设计机制验收

- **状态**：强制执行
- **执行 owner**：根目录 [`AGENTS.md`](../AGENTS.md)
- **用途**：用组合反例和五个阻塞 Gate 验证根治理是否控制 authority、classification、isolation 和 evidence；本文件不定义第二套执行规则。

根规则与本文件不一致时，以根规则为执行 owner，并判定本次治理验收失败。

## 1. 验收方法

修改根 `AGENTS.md` 或候选机制后，必须对最终 diff：

1. 记录实际检查的文件、工作树或 commit，以及可见 PR/branch 范围；不可见范围只披露，不伪造全局结论。
2. 搜索重复 owner、运行时 `changes/` 依赖、额外 change 状态、平行候选目录、全局 registry、永久候选存储和失效引用。
3. 执行第 2 节场景；每个场景必须由根规则唯一推出一个结果，不得依赖未写明的惯例。
4. 执行第 3 节 Gate；任一 Gate 失败，Verdict 为 `fail`。

修改本文件或根规则时，必须同时使用修改前和修改后的场景/Gate 验收。删除或放宽原有场景/Gate，只有在提供可复现反例并以不弱于原保护范围的替代项覆盖时才允许；不得通过删除验收标准使治理修改自我通过。

工作树结果可以用于内容验收，但没有 commit 时不得声称已经 merge、release 或 deploy。

## 2. 组合反例

| ID | 输入 | 必须得到的唯一结果 |
| --- | --- | --- |
| CASE-01 | 只读解释，或只在仓库外临时空间执行且不保留、不中长期设计依据的一次性计算。 | `CLASSIFY`：只读分析；不创建 change 或计划实体。持久化或用于跨任务决策时改为 open change。 |
| CASE-02 | Private fixture 重命名、无用 import 删除或格式修复，没有正式行为变化。 | `CLASSIFY`：既定维护；owner 不需要描述 private 实现细节。 |
| CASE-03 | 代码与唯一正式 owner 冲突，任务未要求改变契约。 | `AUTHORITY`、`CLASSIFY`：修复实现和回归测试。 |
| CASE-04 | 新语义没有 owner：设计与证据已收敛时原子落地；仍需比较或验证时再决定。 | `CLASSIFY`：前者直接采用，后者 open change；不得同时成立。 |
| CASE-05 | 编码中才发现需要发明语义，当前工作区无法安全隔离。 | `CLASSIFY`、`ISOLATE`：停止，不污染正式分支。 |
| CASE-06 | 新增 alpha、backtest、尾盘交易或状态机，采用取决于 replay/benchmark。 | `CLASSIFY`：open change，不直接改正式 owner。 |
| CASE-07 | 已有相同目标 change；一个 change 混入可独立能力；并行 changes 冲突；上游被拒绝；或跨任务 change 没有可发现载体。 | `CHANGE`：分别复用、拆分、阻塞/重划边界、下游重建基线；本地候选不得声称全仓库可恢复。 |
| CASE-08 | 候选以默认关闭 feature flag 合入 `dev`，或 branch 混入无关修改。 | `ISOLATE`：拒绝；flag 和混合 PR 都不是隔离。 |
| CASE-09 | 候选需要大型 benchmark artifact 或生产 shadow 数据。 | `ISOLATE`：只使用已有、非权威、按 candidate 标识且可清理的载体；生产访问仅最小只读权限。 |
| CASE-10 | Schema 迁移需要 expand、回填、dual-read、contract。 | `CLOSE`：采用完整阶段转换契约，不新增 change 状态。 |
| CASE-11 | 文档修正不受 seed/as_of 影响；历史回放却读取当时不可见数据。 | `EVIDENCE`：前者不制造空字段，后者证据无效。 |
| CASE-12 | 测试失败后重复到一次成功；通过后基线变化；或删除断言、扩大容差、缩小 Acceptance。 | `EVIDENCE`：不得制造通过；相关证据失效并重跑，边界变化必须显式记录。 |
| CASE-13 | 经验结果用于选择设计，但只报告胜出结果或把最终验证集继续用于调参。 | `EVIDENCE`：证据无效，必须固定搜索/选择/终验边界并披露物质失败。 |
| CASE-14 | 只采用 change 中可以独立关闭的一部分。 | `CHANGE`、`CLOSE`：采用前拆分。 |
| CASE-15 | Change 已无可行下一步且预设拒绝条件满足；rejection 后无独有知识，或存在可复用失败认识。 | `CHANGE`、`CLOSE`：不得僵尸 open；分别清理删除或保留最小 record-only archive。 |
| CASE-16 | Adoption/direct-adoption branch 已修改 owner docs 或 README 写 `adopted`，但尚未合入；或只完成 merge/tag/release 没有健康验证。 | `AUTHORITY`、`CLOSE`、`RELEASE`：目标分支仍是正式基线；不得声称正式采用或 deployed。 |
| CASE-17 | Open change 正在验证新 public utility 或技术栈，正式 catalog/技术 owner 尚未改变；另有 Python 只读审查无法运行完整工具链。 | `AUTHORITY`、`PREPARE`：候选差异只写 README 并隔离验证；未列出规则继续生效；只读审查按实际范围披露。 |
| CASE-18 | 未明确授权即准备真实下单、生产写入、push、merge 或 deploy。 | `IMPLEMENT`、`RELEASE`：拒绝该动作。 |
| CASE-19 | 正式或候选运行时 import/read `changes/`。 | `AUTHORITY`：拒绝运行时依赖。 |
| CASE-20 | 准备为候选知识新增 plan/experiment 目录、第四种状态、全局 registry 或永久平台；仓库已有正式实验 artifact 存储。 | `CHANGE`、`IMPLEMENT`、`COMPLETE`：拒绝新增候选治理实体，但不误删正式 owner 管理的 artifact 存储。 |

## 3. 阻塞 Gate

### GATE-1 Authority

- 每个可执行决策只有一个权威 owner；不同维度的 owner 边界明确。
- 当前 owner docs 只表达正式契约，open change 只表达候选差异。
- 代码、测试、历史记录和运行状态不能反向定义正式语义。
- Change README 只覆盖明确列出的候选差异，不能覆盖根安全边界或未列出的 owner。
- `changes/` 不成为运行时依赖。

### GATE-2 Classification

- 只读分析、既定维护、直接采用和 open change 覆盖所有输入，且同一输入只能得到一个结果。
- 普通内部维护不会因 owner 未描述 private 细节而升级为 change。
- 需要设计选择或经验验证的系统能力不会绕过 open change。

### GATE-3 Isolation and Lifecycle

- 一个 change 只有一个独立采用边界、一个当前 README 和三种状态 `open/adopted/rejected`。
- Open change 不能进入正式集成、默认入口、release/deploy 链或权威外部状态。
- 候选 artifact、sandbox 和 shadow 使用已有隔离载体，并具有不可变标识、最小权限和清理责任。
- 依赖无环；并行 change 的同一决策冲突在 adoption 前解决。
- 不把特定 feature-PR merge 方式或终态 README commit 可达性当作候选生命周期真相；必要知识以最终 owner docs 或 archive 内容保留，merge 机制由 `release_workflow.md` 拥有。

### GATE-4 Evidence and Closure

- 证据绑定实际检查的代码/文档状态和所有会影响结果的输入；无关字段不强制。
- 历史可观察边界、选择偏差、失败结果、flaky 和证据失效条件均被处理。
- Direct adoption 或 adoption 同步正式契约与当前实现；分阶段迁移使用完整阶段转换契约。
- Adoption 满足所有阻塞 Acceptance；残余风险只有被明确接受时保留。
- Rejection 删除候选实现；只有不可恢复的独有知识才 archive；无可行路径的 change 不成为僵尸 open。
- 设计、commit、merge、release 和 deploy 分别判断。

### GATE-5 Occam

对每条硬规则和每个长期实体逐项回答：

1. 它是否直接保护 authority、classification、isolation 或 evidence 中至少一项？
2. 它是否具有可判定触发条件和唯一结果？
3. 它是否与已有根规则或专项 owner 重复？
4. 它是否会阻塞合法维护、只读审查、分阶段迁移或安全验证？
5. 删除它是否会使错误设计被采用、候选污染正式系统、证据无法复现或完成声明失真？

不保护四类风险、不可判定、重复或造成死锁且删除后不损害正确性的规则或实体，必须删除。任一 Gate 失败，Verdict 必须为 `fail`。

## 4. 验收输出

```markdown
Verdict: pass | fail

- 验收对象：
- 检查的工作树或 commit：
- 可见 PR/branch 范围：
- 适用 owner docs：

| Case/Gate | Result | Evidence | Blocking issue |
| --- | --- | --- | --- |

未覆盖边界与残余风险：
```

不得用“看起来完整”“应该通过”或无法复现的推断代替证据。
