# AGENTS.md

- **状态**：强制执行
- **范围**：仓库中的设计、文档、代码、测试、配置、候选变更、Git、发布和外部状态操作。
- **目标**：只控制四类风险：正式语义来源、执行与探索的分类、候选隔离、完成证据。
- **规范词**：“必须”“不得”“仅当”“否则停止”均为硬规则。

## 1. 正式语义与 Owner（AUTHORITY）

仓库只保留两类规范性设计知识：

| 类别 | 主要位置 | 回答的问题 |
| --- | --- | --- |
| 当前正式契约 | `docs/` 中的 owner docs | 当前正式系统是什么。 |
| 候选差异 | `changes/<change>/README.md` | 系统可能变成什么，以及采用该变化还缺少什么。 |

根目录 `AGENTS.md` 只拥有执行流程；`changes/README.md` 只拥有治理验收，二者不是业务设计 owner。第 3 节列出的专项 owner 也属于当前正式契约，不构成第三类知识。目标分支中的 owner docs 是该分支的当前正式基线；直接采用或 change adoption 分支中对 owner docs 的修改只是拟议正式契约，合入目标分支前不得当作当前真相。代码、测试、配置、Git、PR、CI artifact、部署和运行结果只描述实际状态或提供证据，不得自行改变正式语义。

每个可执行决策必须只有一个权威 owner。不同 owner 可以分别拥有同一对象的业务含义、schema、存储、接口或发布维度，但不得对同一个决策给出两个答案。

必须遵守：

- 实现与 owner doc 冲突时，默认修复实现；只有任务明确改变正式语义时才按第 2 节重新分类。
- 正式语义不得只存在于代码、测试、配置或历史 change 中。
- Open change 在探索和验证阶段不得修改正式 owner docs；候选差异只写入 change README。只有直接采用或 adoption 的最终原子 change set 可以包含拟议 owner doc 修改，且合入前仍以目标分支 owner docs 为正式基线。
- `changes/` 不得被 import、读取为运行配置、作为正式 artifact 输入或成为其他运行时依赖。
- 更深目录的 `AGENTS.md` 只能增加该目录独有约束，不得削弱或复制本文件。

同一决策的 owner 缺失、冲突或无法推出目标语义时，不得猜测：最终设计已由任务明确且满足“直接采用”条件时补齐 owner；仍需选择或验证时进入 open change；任务不包含设计决策时停止受影响修改并报告阻塞。

Open change README 可以只对 `Affected Owners or Contracts` 中明确列出的决策定义候选差异，隔离分支中的候选实现可以按该差异运行；未列出的正式语义仍由当前 owner docs 约束。Change README 不得覆盖根 `AGENTS.md`、候选隔离、权限和证据规则，也不得授权生产副作用。候选差异只有在 adoption 时才同步到正式 owner docs。

## 2. 任务分类（CLASSIFY）

先判断任务是否修改仓库、Git、release/deploy 或其他权威外部状态；不修改时属于**只读分析**，不得创建 change、计划文件或占位实体。一次性计算或原型只有在仓库外的临时空间执行、任务结束后不保留实现或权威状态，且结果不作为跨任务设计依据时，才按只读分析处理；一旦需要持久化、跨任务恢复或用于选择未来设计，必须进入或关联 open change。

非只读任务必须按顺序在第一个满足的分支停止：

| 分类 | 唯一判定条件 | 执行边界 |
| --- | --- | --- |
| **既定契约执行或维护** | 不改变正式外部语义、owner 边界或权威外部状态的含义；或目标状态转换已由 owner 唯一定义。 | 按当前 owner 实现、修复、测试、迁移或维护。Owner 不需要描述 private 实现细节。 |
| **直接采用** | 最终设计与必要决策证据均已收敛；不存在待选择方案；不需要 experiment、benchmark、replay、simulation、shadow 或生产观察决定设计；全部适用正式制品可在一个可审查 change set 中同步。 | 不创建 change；同步正式契约、实现和证据。 |
| **Open change** | 以上条件均不满足。 | 保持正式 owner 不变，按第 5—8 节隔离设计、实现和验证。 |

“正式外部语义”包括业务规则、public API、schema/artifact 契约、数据 lineage、状态机、风控与执行行为、持久化副作用、运行入口以及 release/deploy 契约；private 命名、局部结构、格式化、无行为变化的重构和普通工程维护不属于新的正式语义。

工作量、文件数和耗时不影响分类。新增架构、数据流、状态机、交易/风控规则或生产副作用默认进入 open change；仅当设计与决策证据已经收敛、本次只是原子落地时才允许直接采用。编码中才发现需要发明正式语义时，必须停止把该语义写成正式行为；能够安全隔离时转入 open change，否则停止并报告。

## 3. 修改前准备与专项规则（PREPARE）

修改前必须明确：

1. 预期的外部可观察结果、范围和明确不包含内容；
2. 任务分类及其正式 owner 或 change README；
3. 按适用范围识别受影响的实现、调用方、测试、配置、入口、兼容责任和外部状态；
4. 可证伪的完成条件；
5. 涉及实现时，是否已有等价领域实现或 public utility。

仅在触发时读取专项 owner，不得把其正文复制到本文件：

| 仓库相对路径 | 触发条件 |
| --- | --- |
| `python_coding_style_for_ai.md` | 新增、修改、移动、重命名或删除仓库自有 Python；只读审查时依据适用规则审查。 |
| `utils_catalog.md` | 修改 `src/utils`、改变 public utility，或存在合理的公共工具等价可能。 |
| `technology_stack_decisions.md` | 触及其拥有的日志、Level-2 `.csv.7z` 或其他技术栈边界。 |
| `release_workflow.md` | 触及 branch、PR、版本、release、部署源或部署状态。 |
| `changes/README.md` | 修改或验收候选机制、目录、隔离、关闭或本文件。 |

表中路径是唯一规范路径；移动文件时必须在同一修改中更新本表，路径缺失或出现两个候选 owner 时不得按文件名猜测。Python 修改必须按 Python owner 完成适用规则和 PY-027；只读审查不因无法运行完整工具链而阻塞，但必须说明实际检查范围。公共工具只有完整语义等价时才复用；不稳定候选领域逻辑不得提前进入 `src/utils`。

## 4. 最小实施边界（IMPLEMENT）

- 只修改满足目标所需的最小完整语义单元，并清理本次修改直接造成的失效制品。
- 不借当前任务重写无关模块、统一无关风格、替换未涉及技术栈或保存无 owner 的兼容代码。
- 行为变化必须由正式 owner 或当前 change 的 Acceptance 支持；测试不得适配已知错误实现。
- 新增长期目录、文档、状态、registry、存储层、public utility、基类、兼容层或运行入口前，必须证明现有实体无法承担该职责，并明确 owner、同步责任和删除条件；否则不得新增。
- 未经任务明确授权且未满足适用 owner 条件，不得 commit、push、merge、release、deploy、真实下单、写生产队列、修改正式 registry、写权威数据或执行其他难以恢复的外部操作。

## 5. Change 的最小模型（CHANGE）

一个 change 对应一个可以独立采用或拒绝的设计差异。相同 Goal、Scope、受影响决策和 Acceptance 必须复用；可以独立关闭的内容必须拆分，不能独立关闭的内容必须合并。

Open change 的最小结构为：

```text
changes/<change>/
├── README.md
└── <optional-record>.md
```

`README.md` 是唯一当前快照，必须至少包含：

```text
Status: open | adopted | rejected
Goal and Scope
Baseline and Candidate Delta
Affected Owners or Contracts
Decisions and Evidence
Acceptance and Unresolved Items
Dependencies and Conflicts  # 仅在存在时
```

过程记录只有在重要理由、失败证据或恢复上下文无法从 README、正式制品、Git、PR、测试或现有 artifact 恢复时才创建；README 必须吸收其当前结论。不得为候选知识另建 `plans/`、`proposals/`、`experiments/`、`validations/`、`decisions/`、全局 change registry、额外 change 状态或候选专用永久存储；本条不禁止由正式 owner 管理的既有实验 artifact 存储。

创建 change 时，按 Goal、受影响 owner/API/schema/state machine 在当前树和可见 PR 中定向搜索重复或冲突；采用前再次检查修改同一决策边界的可见 open changes。不可见范围必须披露，不要求遍历所有 feature refs。继续现有 change 时只读取其 README、显式依赖及当前任务需要的记录。需要跨任务恢复或多人协作的 open change 必须通过已有 draft PR 或等价协作载体可发现；未获得创建外部 ref 的授权时，只能报告为本地候选，不得声称仓库范围可恢复或无重复。

存在依赖或冲突时才在 README 中记录。依赖必须无环；上游设计、基线、结论、采用结果或拒绝结果变化后，下游必须重新建立基线，相关证据失效并重新验证；无法继续满足目标时拒绝下游 change。两个 change 对同一决策给出不同答案且未确定顺序时，双方均不得采用。Open change 必须仍有可执行的下一项决策或验证；目标已不可达且预先定义的拒绝条件已经满足时，不得长期保持 open。

## 6. 候选隔离（ISOLATE）

Open change 的 README、候选实现和必要验证必须位于同一个独立 `feature/<change>` branch 或 worktree；该 branch/PR 不得混入另一项可独立关闭的修改。采用前：

- 不得进入 `dev`、正式发布/部署分支或默认运行入口；默认关闭的 feature flag 不构成隔离。
- 不得成为正式 API、registry、定时任务、生产模型选择或生产可发现 artifact。
- 不得写权威数据、生产队列、真实 broker、正式 registry 或其他生产状态。
- 不得使用生产写凭证；生产只读 shadow 仅在已有 owner 管理的隔离机制、最小只读身份和明确清理边界下允许。

候选可以复用已有 test、replay、sandbox、shadow、CI 或 artifact 载体。验证 artifact 必须按 change 和不可变 candidate 标识隔离，不被生产发现，并具有保留期限或清理责任。没有安全隔离边界时停止，不得通过新增永久候选平台绕过阻塞。

## 7. 证据与完成声明（EVIDENCE）

对代码行为、实验结果或外部状态的完成声明，证据必须绑定准确的代码状态（commit，或包含相关未跟踪文件的完整工作树清单与 diff/hash）以及所有会改变结果的输入；数据版本、配置、规则版本、`as_of`、seed 和依赖版本只在其影响结果时记录。纯只读分析只需引用实际检查的文件、状态或其他来源，并说明未覆盖范围。

必须满足：

- 测试断言 public 行为、稳定边界或明确 artifact；缺陷修复包含可复现回归场景。
- Open change 的每项确定结论具有可重复证据；触及共享逻辑时运行受影响正式基线测试。
- Direct adoption 和 adoption 的最终拟议树中，owner docs、实现、配置和测试表达同一设计，Acceptance 在该树上通过。
- 历史回放、标签、特征和交易模拟必须固定历史可观察边界，不得读取当时不可见的未来或事后修正数据。
- 经验结果用于选择设计时，必须事先明确候选/搜索边界、选择数据、最终验证数据和停止条件，并报告会影响结论的失败结果。
- 一次失败后重复运行直到成功不得抹去失败；随机或顺序不稳定必须修复，或按预先定义的稳定性条件评估并披露。
- 不得删除断言、扩大容差、跳过失败或静默缩小 Acceptance 来制造通过。Acceptance 或 Scope 的实质变化必须记录为设计边界变化，并使相关证据失效后重跑。
- 基线、关键实现、会影响结果的输入或 Acceptance 实质变化后，受影响证据失效并重跑。
- 未运行、失败或未覆盖项必须说明原因和剩余风险；“能运行”“退出码为零”或“看起来正确”不能支持更强声明。

## 8. 采用、迁移与拒绝（CLOSE）

直接采用只能在任务已经明确最终设计时执行。Open change 的 adoption 必须有明确采用决定；rejection 可以来自明确拒绝决定，或来自任务范围内对 README 预先定义拒绝条件的客观满足。Acceptance 已满足但尚无采用决定时保持 open。候选 branch 中写入终态只表示拟议关闭：正式采用必须等采用 change set 合入目标分支后才生效；正式拒绝必须等候选实现与资源完成清理，并在需要保留知识时合入 record-only archive 后才完成。

**直接采用或 change adoption**必须在一个可审查 change set 中同步全部适用的正式制品：owner docs、实现、配置、API/schema/artifact 契约、测试、migration、入口、observability 和直接失效制品清理。不适用项省略。所有阻塞 Acceptance 必须满足；非阻塞残余风险只有在范围和影响被明确记录并由采用决定接受时才允许保留。

需要 expand/contract、双写、双读、回填、灰度或兼容窗口时，直接采用或 change adoption 可以采用完整的**阶段转换契约**，但必须同时明确当前阶段、阶段不变量、进入/退出条件、回滚方式和旧路径删除条件，并使本次 owner docs 与当前阶段实现一致；未来已定义阶段按既定契约执行，出现新的设计选择时再创建 change。不得为迁移阶段新增 change 状态。部分范围能够独立采用时，必须在采用前拆分。

采用后正式语义只由 owner docs 解释；change 与 archive 仅用于非规范性追溯，不得成为 owner。终态 change 只有在保存无法从当前系统恢复的重要理由、失败证据、迁移背景或验证边界时才移入 `changes/archive/`，否则删除；删除前搜索入站引用。终态不得重开，后续变化创建新 change。

无论采用或拒绝，都必须清理不再需要的候选 sandbox、临时凭证、临时入口和验证 artifact。

**拒绝**必须保持正式 owner 不变并删除候选代码、测试、入口、flag、配置、sandbox 和非必要 artifact。存在不可恢复且可复用的认识时，只以不含候选实现的 record-only 形式保留最小 `rejected` archive；不存在时直接删除 change，不创建 tombstone。

## 9. Git、发布和外部状态（RELEASE）

Git、release 和 deploy 必须遵守 `release_workflow.md`。完成汇报必须分别陈述：

```text
设计状态 / 工作树与 commit 状态 / merge 状态 / release 状态 / deploy 状态
```

这些事实不得互相替代。候选 README 中的 `Status: adopted` 或采用决定不等于 merged、released 或 deployed；push、merge、tag、GitHub Release、进程存在或端口打开也不得单独证明部署成功。实际部署成功只按发布 owner 定义的不可变版本、健康检查和回滚条件判定。

## 10. 完成复核与奥卡姆 Gate（COMPLETE）

完成前必须确认：

1. 正式 owner、任务分类和修改边界唯一；正式设计与候选设计未混写。
2. 适用专项规则、测试和人工复核已执行；证据对应最终代码状态。
3. Open change 仍被隔离；adoption/rejection 已完成适用同步或清理。
4. 设计、Git、release、deploy 和外部状态声明与客观证据一致。
5. 每个新增长期实体都回答现有实体无法回答的问题；删除任何剩余重复规则或实体不会损害 authority、classification、isolation 或 evidence。

修改本文件或候选机制时，必须按 `changes/README.md` 对最终 diff 执行组合反例和阻塞 Gate；任一 Gate 失败不得标记完成。

完成汇报只列适用内容：

```text
工作类型与设计来源
修改范围
复用与专项规则检查
实际验证及结果
未验证项与剩余风险
change/工作树/commit/merge/release/deploy 的真实状态
```

不得用“应该”“基本完成”“看起来符合”代替证据。
