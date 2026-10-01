# Research 工作流

- **状态**：强制执行
- **适用范围**：研究目标、研究记录、采用候选、Notebook、研究结论、依赖、采用、拒绝和保留
- **Owner 边界**：本文件拥有研究治理，不拥有任何业务正式语义或 Git merge 机制

## 1. 核心模型

一个研究目标对应一份卷宗。H 编号用于引用研究问题、假设及其结论；递进研究可以包含多个
H，不自动产生 Change。只有形成可独立正式化、采用或拒绝的具体候选方案时，才按 Change
管理；Change 仍记录在同一卷宗中，不新增目录、编号体系或阶段状态。

Notebook 保存实验事实；卷宗 README 保存研究结论及采用候选的状态和决定；Owner docs
只保存采用后的正式行为。

```text
Notebook
  保存代码、输入、过程和结果事实
        ↓
research/<topic>/README.md
  保存研究问题、假设和结论；采用候选另有状态和决定
        ↓ 候选 adopted
docs/ 中的 Owner docs
  保存当前正式行为
```

不创建独立 Initiative、Conclusion、Experiment 状态目录或全局 Change registry。

## 2. Authority

- `research/<topic>/README.md` 是该研究目标的唯一入口；各段落拥有对应研究结论，采用候选
  段落另拥有自身状态和决定。研究记录默认不设生命周期状态。
- `research/README.md` 只提供主题导航，不重复维护状态、Acceptance 或结论。
- Notebook 拥有实际运行内容和输出事实，但不能自行决定 adoption/rejection，也不能定义正式
  行为。
- 目标分支 `docs/` 中的正式 owner 独占当前业务契约。Research 内容、代码、测试、commit、PR
  和运行结果都不能反向改变正式语义。
- `research/` 不得被运行时代码、正式配置、CLI registry、workflow 或生产制品读取。
- 目标分支中的 topic README 是可恢复的当前研究记录；尚未合入的 feature branch、dirty
  worktree 和 untracked 文件只能声明为 draft。

## 3. 何时创建记录

只有同时满足以下条件时才创建研究记录：

- 已形成明确假设；
- 能说明准备判断或改变什么；
- 有有限的研究范围及可被事实支持或否定的判据。

研究记录不要求能够独立采用或拒绝。提出具体正式行为或契约的候选，明确正式化范围及所属
owner，且需要独立采用决定时，才将其作为 Change；研究结果有利不自动产生采用候选。

尚未满足条件的内容只写在 topic README 的“待澄清问题”或“未来想法”中，不拥有状态，也不
构成 roadmap 承诺。删除后不损失当前理解的内容不记录。

开始一个研究目标时只创建：

```text
research/<topic>/README.md
```

没有可执行实验时不创建 Notebook、空目录、候选代码或专用 branch。

## 4. Topic README

Topic README 至少包含：

- 目标；
- 理解各假设所必需的当前背景；
- 假设间依赖图；
- 只含链接的研究索引；
- 研究记录及采用候选的段落。

研究记录按实际需要使用以下字段，不强制空字段：

```markdown
## H01 标题

- Hypothesis: 可证伪的研究判断
- Why: 必要性
- Scope: 本轮研究范围
- Not included: 明确排除项
- Depends on: 所依据的研究记录、证据或正式能力
- Acceptance: 运行或评审前确定的通过条件
- Stop: 运行前确定的停止条件
- Evidence: Notebook 或其他事实的稳定引用
- Conclusion: 当前认识、原因和适用边界
- Next: 本轮尚缺的工作，或后续独立问题及客观重验条件（按需，明确区分）
```

采用候选在上述记录中明确独立正式化范围并使用 `Status`；有决定时记录带日期的 `Decision`，
采用后记录 `Formalized in`。三态仅适用于此类 Change。研究方案的用户约束保留日期和来源，
不作为 adoption/rejection 决定。已完成的研究不强制填写 `Next`，后续想法可以链接下一项研究。

README 可以包含导航索引，但不得在索引和采用候选段落中手工维护两份状态。

README 是研究判断的摘要。每项记录保留理解假设、当前结论及必要后续问题所需的最小完整
记录；详细实验方案、参数表、逐次运行经过、完整指标、哈希清单及复跑命令放在对应
Notebook 或冻结证据中。Acceptance 可引用运行前冻结的确定版本，摘要不得省略会改变
判定的条件；Evidence 必须能定位支持或限制当前结论的具体运行，包括相关失败结果。
完整性由卷宗与可恢复证据共同保证，不要求 README 重述全部事实，也不以篇幅目标删除必要信息。

### 写入与按需读取

- 未成熟想法按第 3 节简记；形成独立研究问题时新增 H，并引用已有结论。同一假设的实验、
  补证、修复和复跑更新对应 Notebook 与证据，相关证据变化时更新 README 的 Evidence，
  认识或后续动作变化时
  更新 Conclusion 及必要的 Next，不反复追加整份实验报告。下一项研究启动不自动证明原研究
  已完成，也不使已完成的研究重新成为待完成工作。
- 先由根索引定位主题，再读 topic README 确定相关研究记录或采用候选；只在解释、审核或复现
  需要时读取对应 Notebook 章节、单元和冻结证据。依赖链接用于定位，不要求递归通读所有关联资料。
- 读取 Notebook 时按需提取 Markdown、相关代码及必要输出，不默认加载整份 JSON、图片编码
  或无关的大型输出。阅读结论不要求执行 Notebook。

## 5. 采用候选的生命周期

只有采用候选（Change）使用以下三种状态；普通研究记录按第 10 节说明完成事实和结论，
不增加 `completed`、`closed` 或阶段状态：

```text
open | adopted | rejected
```

- `open`：候选正在澄清、验证、等待采用决定、客观触发器或正式化尚未合入。
- `adopted`：最终 Owner docs、实现和必要测试已经同步进入目标分支。实验通过、批准开始实现、
  commit 或 PR 尚未合入都不构成 adopted。
- `rejected`：用户或正式 owner 已明确拒绝，候选实现和不再需要的制品已经清理。

不增加 `defer`。候选等待未来条件表达为 `open + Next trigger`。已 adopted/rejected 的段落
不追加新的实质实验；新时期、新数据或新反例另建研究记录，并链接旧结论。新记录只有满足
第 3 节的独立采用条件时才是新的 Change。

Open Change 必须有可执行的 `Next` 或可客观判断的 `Next trigger`。已经没有可行路径且满足
预设拒绝条件时，按下述 Decision 权限完成 rejected 或删除；尚缺用户决定时，在 `Next` 中
明确待决定项，不得自行关闭，也不得无后续动作地永久保留 open。

只有正式 owner 已定义客观自动判定条件时，Acceptance 才能自动触发决定；其他 adoption 和
rejection 必须由用户明确决定。

## 6. Notebook 与代码版本

Notebook 只在需要数据探索、计算、比较、回放、图表或结果复现时创建，不自动生成占位文件。

Notebook 只记录会影响结论的内容：

- 对应 H 编号和验证目的；
- 数据来源、范围和 as-of；
- 实际候选代码或稳定引用；
- 会影响结果的参数、环境和 seed；
- Acceptance 引用；
- 成功、失败、输出和必要限制。

最终认识写回对应研究记录或采用候选的 Conclusion，避免 Notebook 成为第二个结论 owner。

Notebook 按实际实验组织；一个假设可引用多次运行，多个假设也可共享比较实验。详细方案、
证据说明与复跑步骤使用可定位的章节或单元；保存运行当时的 Acceptance 引用或快照，不能
用事后更新的摘要替代事前条件。历史方案和当时解释须标明日期及适用版本，不拥有当前研究
状态或结论。确切复现使用所引用运行的冻结源码、Notebook、环境和输入；当前文件上重跑新
输入不能冒充旧证据恢复。

同一候选的 bug 修复、重构或无语义变化修改继续使用同一 Notebook。多个竞争实现优先保存为
独立代码文件，由一个 Notebook 在相同数据和指标下比较。版本代表可独立采用的不同正式行为时，
才拆成多个 Change；它们可以引用同一个比较 Notebook。

探索期间 Notebook 可以修改。Notebook 被用于 adoption/rejection 时，Evidence 必须指向一份
可恢复的确定版本，例如 Git 中的文件版本或不再修改的归档副本。只有 digest、没有可恢复内容，
或者只存在于 untracked worktree，都不能声称可跨任务复现。

## 7. 目录与保留

一个假设只有一个文件时直接放在 topic 目录。需要保留两个以上相关文件时才建立子目录：

```text
research/<topic>/
├── README.md
└── h01-factor/
    ├── explore.ipynb
    ├── final-validation.ipynb
    ├── candidate-a.py
    └── candidate-b.py
```

子目录默认不再创建 README；状态和 Conclusion 仍由 topic README 拥有。大型数据、模型和输出
不默认提交 Git；若决定依赖它们，Evidence 必须记录可恢复位置和必要保留期。可以由 Notebook
和正式输入重新生成且没有独有知识的制品可以清理。

研究结果不支持假设时仍保留可复用认识和必要 Evidence，不据此标记 `rejected`。Rejected
Change 有可复用失败认识时保留最小 Conclusion 和必要 Evidence；没有实验、没有独有
认识且删除不影响未来判断时，可以删除该草案，不制造永久 tombstone。

## 8. 依赖与未来边界

研究记录及采用候选用 `Depends on` 和 topic README 中的图表达依据和依赖，不增加阶段
状态。递进研究可以引用前一轮的不支持结果；证据依赖不要求上游方案已采用，也不因上游
候选被拒绝而自动失去价值。依赖必须无环，实际输入、基线或关键结论改变时重验受影响证据。

采用候选之间的正式能力依赖另须满足：

- 出现环表示采用边界不能独立，必须合并或重新划分；
- 上游仍 open：下游可以研究，但不得假装上游能力已经正式采用；
- 上游 rejected：重新判断依赖该能力的下游是否仍有独立价值；
- 两项能力不能独立采用：合并为一个 Change；
- 一个 Change 含有可独立关闭的能力：拆分。

“未来 V2/V3”“未来 MQTT”和未决定排期只放在非权威未来想法中。只有形成当前必要、可证伪、
可执行的假设时才新增研究记录；需要独立正式化决定时再按 Change 管理。

## 9. Branch、实现与外部状态

Research 知识模型不要求一个 H 或 Change 对应一个 branch。所有仓库写入仍遵循
`release_workflow.md`：

- 纯 topic README、Notebook 和自包含研究代码可以在一个可审查 feature PR 中更新同一卷宗；
- 候选需要修改共享代码、配置或入口时，按可独立采用边界使用 feature branch/worktree 隔离；
- 能够独立采用的共享实现不得混入同一 PR；
- adoption 前候选共享实现不得进入目标分支、默认入口、正式 API、registry、cron、模型选择或
  生产可发现制品；默认关闭的 feature flag 不构成隔离；
- 研究不得写入权威数据、生产队列、真实 broker、正式 registry 或其他生产状态，也不得使用
  生产写凭证。

没有安全隔离边界时停止。不得建立永久候选平台来绕过阻塞。

## 10. Evidence 与完成

使用实验选择设计时，必须在看到最终结果前确定候选范围、选择数据、最终验证数据、Acceptance
和停止条件，并保留会影响结论的失败结果。历史回放、标签、特征和交易模拟不得使用当时不可见
的未来数据或事后修正数据。

本轮研究在原定 Scope 内完成必要验证，满足 Acceptance 中保障过程正确性和证据有效性的
条件，达到预先确定的停止条件，保存必要证据并形成与其强度相称的 Conclusion 后，即完成
本轮工作。结论可以支持、不支持或尚不能确定原假设；假设支持判据未通过不妨碍完成。
研究完成不表示正式采用。原 Scope 排除的事项和后来提出的新问题，不构成本轮完成的
附加条件；后续研究继承已有证据，不使上一轮一直处于待完成状态。

因技术失败或必要证据缺失而提前中止时，明确记录未完成的验收、失败原因及剩余问题，
不能以研究转向或去掉状态代替完成。仍在研究的问题应记录可执行修正或客观重验条件。

基线、关键实现、影响结果的输入、Scope 或 Acceptance 实质变化后，旧 Evidence 不再自动支持
当前判断，必须重验。声明强度不得超过实际保存的数据、代码状态和环境信息。

整理已有记录时，先确认独有内容的去向，再缩短 README；保留失败、事前条件、限制和恢复
入口，并检查受影响的链接。原冻结证据不回写，迁移后的历史说明不构成新运行或新的事前
登记。仅移动文案不要求重新计算；应验证 Notebook 代码单元、执行计数和保存输出未变，
且验收条件、采用决定及结论语义保持不变。按本工作流整理旧记录时，可以移除普通研究的
`Status` 及由它产生的待关闭表述；不得把原未通过或未完成的验收改写为通过，不回写冻结证据。

Adoption PR 必须同步：

- 最终 Owner docs；
- 实现和必要测试；
- topic README 中该采用候选的 `Status: adopted`、Conclusion、Decision 和 Owner docs 链接。

Schema 或持久化状态需要分阶段迁移时，Adoption 必须定义完整的阶段转换契约，不增加第四种
Change 状态。

历史数据是否已经回填、模型是否进入 registry、release 是否完成、deploy 是否成功以及生产是否
启用，都是独立状态，不能由 adoption 或 merge 自动证明。

## 11. 治理修改与候选关闭 Gate

Gate 不是生命周期状态、工作阶段或持久化实体，而是对研究机制修改或 Change 关闭操作的最终
diff 与证据状态进行的完成性判断。

- 创建或更新研究记录及 open Change 时必须持续满足这些约束，但不要求生成 Gate 报告。
  普通研究按第 10 节更新完成事实和结论，不执行 adoption/rejection。
- 修改研究机制，或者准备采用候选的 adoption/rejection 时，必须检查全部五个 Gate。
  Adoption 与 rejection 按本次状态转换对应的关闭条件判断，不得用“不适用”规避实际约束。
- 任一 Gate 失败，不得关闭 Change 或宣称治理修改完成。Change 保持 `open`，并通过 `Next`
  记录可执行修正，不增加 `blocked`、`defer` 等状态。
- Gate 不替代 Change 自身的 Acceptance，也不替代用户或正式 owner 的明确 Decision。
  Acceptance 通过不能自动产生 adopted。
- Gate 判断必须绑定实际检查的最终 diff、代码状态和 Evidence；预期结果、dirty worktree 或尚未
  合入的 PR 不能证明目标分支已经完成状态转换。
- Gate 结果可以记录在 PR、评审或任务完成报告中，不创建全局 Gate registry、独立状态文件或
  重复的长期检查表。

### GATE-1 Authority

- Topic README、Notebook 和 Owner docs 的职责没有重叠；根索引不复制状态。
- `research/` 没有运行时依赖，也不存在第二个候选状态 owner。

### GATE-2 Minimality

- 一个目标只有一个卷宗；普通研究不因 H 编号而产生 Change，采用候选才有三态及独立采用边界。
- 没有为尚未发生的实验创建空 Notebook、目录、状态或平台。

### GATE-3 Isolation

- Open research 只进入 `research/`；候选共享实现、正式入口和外部副作用保持隔离。
- 相关假设共享背景，但可独立采用的共享实现没有混入同一 PR。

### GATE-4 Evidence

- Acceptance 在最终结果前确定；证据绑定实际代码和所有物质输入。
- 失败、未来数据、选择偏差、基线变化和不可恢复制品均按实际处理。

### GATE-5 Closure

- 研究完成事实与原 Scope、停止条件、Evidence 和 Conclusion 一致；记录整理不冒充验收通过。
- Adopted 同步 Owner docs、实现、测试和候选状态；Rejected 清理候选并保留必要认识。
- design、working tree、commit、merge、release、deploy 和外部运行状态分别报告。
