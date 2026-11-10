# 发布工作流

- **状态**：强制执行
- **适用范围**：Git 分支与 worktree 选择、交付内容核对、GitHub Actions CI、测试环境与正式环境部署源、自动版本发布。
- **用途**：定义从功能开发到正式发布的唯一分支状态模型与测试部署契约。

## 分支职责

| 分支 | 职责 | 允许的主要写入方式 |
|---|---|---|
| `feature/*` | 一个可审查的代码或文档修改分支。 | 开发者提交。 |
| `dev` | 所有开发集成的唯一目标分支。 | `feature/*` 通过 PR 合入；发布成功后由 workflow 同步 `master`。 |
| `release/auto-release` | 当前待发布内容的机器镜像，也是测试环境唯一部署源。 | auto-release workflow 从 `dev` 强制镜像；不得人工提交。 |
| `master` | 已批准的正式版本，也是正式环境唯一部署源。 | `release/auto-release` 通过 release PR 合入。 |

远端必须同时存在 `dev` 和 `master`。仓库默认分支为 `master`，默认开发分支为 `dev`。

### 开发分支与工作目录选择

开发、研究和验证中的操作授权与前后告知统一遵循根
[AGENTS.md 的安全边界](../../AGENTS.md#安全边界)。本节规定何时需要隔离及如何选择基线，
不因满足隔离条件而自动获得 Git 操作授权。

分支隔离提交历史；worktree 提供独立的工作目录、索引和 HEAD。同一目录中切换分支会改变
该目录内其他会话、编辑器和进程随后读取的文件，不能作为并行工作的目录隔离。

| 场景 | 选择 |
|---|---|
| 只读分析、阅读研究资料 | 沿用当前目录读取代码或 Git 历史，不因分析本身创建分支或 worktree。 |
| 同一任务继续实现、修复、补实验或整理文档 | 优先复用已确认且范围、基线相符的 `feature/*` 和工作目录。 |
| 当前在 `dev` 或 `master`，需要提交本地修改 | 使用符合任务范围的 `feature/*`；没有可复用分支时才提出新建方案，不直接向集成或正式分支提交。 |
| 可独立采用的候选需要修改共享代码、配置或入口 | 每个独立采用边界使用独立 `feature/*`；是否需要额外 worktree 按目录隔离需求决定。 |
| 需要保留当前目录状态，或同时开发、运行、比较不同版本 | 在对应分支上使用独立 worktree，不通过切换共享目录的分支实现并行。 |

临时复现或 PR 验证确需独立检出时，可以在已授权的 worktree 中使用 detached HEAD；持续开发
和准备交付的修改必须归属于明确的 `feature/*`。验证过程中产生待交付修复时，按已有授权
处理分支和改动去向；缺少授权时先提出具体方案，不把临时验证目录默认为后续开发目录。

基线使用完整 commit 定位，并核对相对合入目标的提交与文件差异。复用或新建前必须明确
本任务依赖哪些尚未合入的修改。例如候选 A 尚未合入 `dev`，从 `dev` 创建 B 不会包含 A；
从 A 创建 B 又会继承 A 的提交，必须先确认依赖和采用边界，不能仅凭分支名称判断内容。

创建 worktree 不会自动带入源目录的未提交和未跟踪文件。迁移时只搬迁本任务已授权的改动，
核对目标目录内容和受影响验证，再按约定处理源目录；保留其他任务的修改及暂存状态。
无法安全拆分时保留源目录并说明冲突，不把所有工作树的修改一并收集进当前任务。

## 状态流转

1. `feature/*` 通过 PR 合入 `dev`，PR 必须通过 CI。
2. `dev` push 后，auto-release workflow 比较 `master` 与该次 dev commit 的文件树。
3. 存在未发布文件变化时，workflow 通过 CI 后把该 commit 镜像到
   `release/auto-release`，并创建或更新指向 `master` 的 release PR。
4. GitHub 对 `release/auto-release` 的 push 触发测试环境部署。
5. release PR 按下文标准流程取得人工批准且必需 CI 检查通过后合入 `master`；release
   workflow 通过 CI 后执行 semantic-release。正式环境部署尚未实施。
6. release workflow 成功后，sync workflow 把 `master` 合入 `dev`。
7. 回同步后的 `dev` 与 `master` 文件树相同时，auto-release workflow 必须跳过；如果同步
   期间已有新功能进入 `dev`，继续生成下一轮待发布内容是预期行为。

## 提交、合入与部署前的内容核对

核对本次交付内容时使用实际目录、提交和文件差异，不能以当前工作树干净或某个分支存在
代替完整性判断。操作授权遵循根安全边界，核对结果和临时工作位置只在对话或 PR 中说明。

1. **提交前**：读取 worktree 列表，定位本任务涉及的工作目录、分支和完整 HEAD，检查暂存、
   未暂存及未跟踪改动。确认待提交 diff 包含本次交付所需文件，且没有混入其他任务内容；
   留在其他目录的任务改动必须明确其保留位置及是否属于本次交付，不得因主目录干净而遗漏。
2. **验证与提交对应**：确认验证覆盖实际待提交内容和影响结果的输入。基线、实现或输入发生
   实质变化时重验受影响部分；提交后核对实际 commit 的内容与已验证内容一致。仅有 HEAD
   或分支名不能代表运行时存在的未提交修改，也不能证明其他 worktree 中的验证适用于此提交。
3. **PR 与合入后**：核对来源、目标、完整 diff、CI 对应提交及最终合入内容。发生冲突解决或
   其他实质修改时验证受影响行为。Merge commit 或 rebase 可以改变提交 SHA，必须检查最终
   内容及其 CI，不能仅因 SHA 不同判断代码丢失，也不能把合入动作本身当作验证。
4. **发布与部署**：确认本次预期交付已进入 `dev`，并核对 auto-release 使用的提交、当前
   `release/auto-release` 及其 CI。测试部署再按[精确 SHA 部署契约](#精确-sha-部署契约)
   核对部署结果和服务返回的 commit；正式发布按下文 Release PR 约束核对。不得由本地
   commit、PR 合并或 CI 成功推断服务器已经运行该版本。

发现漏项、任务内容混入或代码与验证不一致时，暂停受影响的提交、合入或部署，说明差异及
修复方案；不得为消除差异自动合并其他 worktree、无关候选或未获采用决定的共享实现。

## PR 合并约束

### Release PR

semantic-release 必须能够分析 `dev` 中的原始 Conventional Commit 类型。release PR
不得 squash 成固定的 `chore(release):` 标题；必须使用 merge commit 或 rebase merge
保留原始提交。

- commit body/footer 包含 `BREAKING CHANGE:` 或 `BREAKING CHANGES:`：major。
- `feat:`：minor。
- `fix:`、`perf:`：patch。
- `docs:`、`test:`、`refactor:`、`chore:`、`build:`、`ci:`、`style:`：不发布。
- `type!:` header 不作为 breaking release 依据，并由 CI 拒绝。

#### GitHub 保护规则

在仓库 `Settings → Rules → Rulesets` 中配置 `protect-master-release`，保存后生效：

| 配置项 | 标准值 |
|---|---|
| Enforcement status | `Active` |
| Target branches | `master`（`refs/heads/master`） |
| Require a pull request before merging | 启用 |
| Required approvals | `1`；由具有 write 或更高权限、且非 PR 作者的审查者批准 |
| Require status checks to pass | 启用 |
| Required status check | `run-ci / build-and-test` |
| 检查来源 | `GitHub Actions` |

该检查由 `.github/workflows/auto_release_pr.yml` 的 `run-ci` job 调用
`.github/workflows/ci.yaml` 的 `build-and-test` job 产生。auto-release 在当前 `dev`
commit 上通过 CI 后，将同一 commit 镜像到 `release/auto-release`，因此合并前应核对
release PR 当前 head SHA 上的检查结果。

必需检查名称必须与实际产生的名称完全一致；修改上述 job 名称时，必须同步更新规则集。
可复用 workflow 的检查名称格式为 `调用 job / 被调用 job`，见
[GitHub 必需检查名称说明](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/troubleshooting-rules)。

#### 标准审批与合并操作

1. 打开 auto-release workflow 创建或更新的 release PR，确认来源为
   `release/auto-release`、目标为 `master`，检查本次发布的变更。
2. 使用有 write 或更高权限的审查账号，进入 `Files changed`，点击
   `Review changes → Approve → Submit review`。
3. 返回 `Conversation`，确认至少一条有效批准，以及当前 head SHA 的
   `run-ci / build-and-test` 检查成功；所有合并条件满足后继续。
4. 标准操作选择 `Create a merge commit`，再点击 `Confirm merge`，保留原始提交供
   semantic-release 分析。
5. 合并后查看 `Release` workflow 和随后的 `Sync master to dev` workflow；版本发布、
   分支同步与服务器部署分别按各自结果确认。

普通评论不计入批准记录。PR 作者不能批准自己的 PR；自动 release PR 由
`github-actions[bot]` 创建，仓库所有者可以使用自己的账号提交批准。操作说明见
[GitHub PR 审批指南](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews)。

#### 合并阻塞排查

- `Review required`：核对是否通过 `Approve → Submit review` 提交了有效批准，以及
  审查账号是否有 write 或更高权限；在 `Conversation` 留言不会解除此阻塞。
- `Expected — Waiting for status to be reported`：规则要求的检查尚未上报结果。先核对
  PR 当前 head SHA 的检查列表，再核对规则中的检查名称、来源及 workflow 触发条件。
  当前工作流不产生 `authorize-release-candidate`；若规则仍要求该名称，应在
  `protect-master-release` 中将其替换为 `run-ci / build-and-test`，来源选择
  `GitHub Actions`，保存后刷新 PR。重复审批或等待不会产生一个不存在的检查。

### 自动版本发布

release workflow 在 `master` 的 CI 成功后执行 semantic-release，以已合入的提交生成
版本号、`${version}` 格式的 Git tag 和 GitHub Release。版本说明统一保存在
[GitHub Releases](https://github.com/UlricWu/trading/releases)，仓库不维护 `CHANGELOG.md`。

发布配置只加载 commit-analyzer、release-notes-generator 和 github 插件；workflow 的
安装列表必须与配置一致。发布过程不得创建 release commit 或直接更新 `master`，继续
遵守必须通过 PR 和 CI 的分支保护规则。

### Research 记录与正式化

`research/` 中的研究记录、采用候选、Notebook 和结论可以通过普通 feature PR 进入 `dev`，
但它们不得成为运行时依赖或改变正式行为。研究编号与隔离触发条件由
[研究工作流](research_workflow.md#9-branch实现与外部状态)定义；分支及目录选择按上文执行。

可独立采用的候选共享实现分别通过独立 feature PR 审查，在 adoption 前不得进入 `dev` 的
正式实现、默认入口或 registry。采用具体候选方案的 PR 必须同步修改最终 owner docs、实现、
测试和卷宗中该候选的 `Status`。该 PR 合入
`dev` 前，候选状态仍为 `open`；普通研究记录不使用采用状态。merge、release 和 deploy
继续分别判断。

研究记录、候选状态、Evidence 和 adoption 条件由 `research_workflow.md` 拥有。本文件只拥有
branch、PR、merge 和目标分支机制，Git 可达性本身不能证明候选已经 adopted。

## 测试部署边界

从零安装、逐参数原理、验收和排障步骤见
[测试环境自动部署操作指南](test_deployment_setup_guide.md)。该指南只说明如何落实本节
契约，不单独拥有发布语义。

测试发布链路为：

```text
GitHub release/auto-release push
  -> Tailscale Funnel 公网 HTTPS
  -> 127.0.0.1:9000 /github/webhook
  -> minquant-webhook 接收、校验、去重、持久化
  -> wsw user systemd dispatcher
  -> /home/wsw/app/code/trading 精确 SHA 部署
  -> minquant-api.service
  -> 127.0.0.1:5050/health
```

腾讯云中继、公网 IP、腾讯云 TLS、SSH 部署跳转、Nginx、Docker、SQLite、外部消息队列和
cron 部署都不属于测试发布链路。Funnel 只允许映射 Webhook 接收端口，不得映射推理服务
5050。测试 API unit 必须显式注入 `MINQUANT_API_HOST=0.0.0.0` 和
`MINQUANT_API_PORT=5050`。`start.sh`、`kill.sh`、`status.sh` 可以用于人工本地开发，
但不是自动部署入口；开发启动和状态检查默认使用 5051，避免与同机测试服务互相占用或
误检。

以下状态相互独立，不得互相替代：

- `release/auto-release` 更新成功：经过 CI 的测试部署源已准备好。
- Webhook 返回成功：合法 delivery 已持久化接纳，或该 delivery 已被接纳过。
- delivery result 为 `succeeded`：精确 SHA、依赖、服务启动和身份健康全部通过。
- release PR 合入 `master`：正式发布源获批准。
- semantic-release 成功：版本号、tag 和包含版本说明的 GitHub Release 已生成。

## Webhook 接收契约

### 网络与身份

- Tailscale Funnel 为 Ubuntu 节点提供 `*.ts.net:443` 的公网 HTTPS、TLS 终止和到
  `127.0.0.1:9000` 的反向代理；仓库不拥有 tailnet 名称或 Funnel 当前启用状态。
- 接收器只监听 `127.0.0.1:9000`，以 system user `minquant-webhook` 和 group
  `minquant-deploy` 运行。该用户为 `nologin`，不得获得 `wsw` shell 或应用目录写权限。
- GitHub Webhook secret 的磁盘 owner 是 root；systemd 通过 credential 把它只读交给
  接收进程。secret、Tailscale auth key 和业务凭证不得进入仓库、payload 记录或日志。
- 部署 dispatcher 和 API 是 `wsw` 的 user systemd unit。启用 user unit 前必须为 `wsw`
  开启 linger，保证注销 SSH 后 unit 仍运行。

### 请求校验与接纳

接收器只接纳同时满足以下条件的请求：

1. `POST /github/webhook`。
2. `X-Hub-Signature-256` 与原始 body 的 HMAC-SHA256 完全匹配。
3. `X-GitHub-Event` 精确为 `push`。
4. payload repository 为 `UlricWu/trading`。
5. payload ref 为 `refs/heads/release/auto-release`。
6. payload `after` 是 40 位小写 Git commit SHA。
7. `X-GitHub-Delivery` 是 1 到 128 位安全唯一标识。

接收器不得执行 payload 内容，也不得把 payload 字段拼入 shell。合法 delivery 必须先以
exclusive 原子文件创建到 `/var/lib/minquant-webhook/deliveries`，再创建 queue marker，随后
立即返回 `202`；相同 delivery 再次送达返回 `200`，且不得形成第二次部署。签名正确但不
符合上述来源的请求返回非成功状态并且不入队，包括 GitHub `ping`。

持久化目录职责如下：

| 路径 | 写入身份 | 内容 |
|---|---|---|
| `staging/` | `minquant-webhook` | 原子创建时的临时文件。 |
| `deliveries/` | `minquant-webhook` | 已接纳的最小合法请求记录。 |
| `queue/` | `minquant-webhook`、`wsw` | 尚未形成终态结果的 delivery marker。 |
| `results/` | `wsw` | `succeeded` 或 `failed` 终态记录。 |

dispatcher 只读取持久化记录并以参数数组执行固定安装路径
`/usr/local/libexec/minquant-deploy`；唯一注入环境是经重新校验的 `RUN_ID` 和
`DEPLOY_SHA`。进程崩溃时 queue marker 保留并由下一次 path trigger 重试。结果文件先原子
持久化再删除 queue marker。当前实现不自动删除 delivery 或 result；采用保留期限前，维护者
不得把删除任务加入该链路。

## 精确 SHA 部署契约

### 路径与依赖

- 自动部署工作树固定为 `/home/wsw/app/code/trading`，只用于测试发布，禁止人工修改。
  人工开发目录 `/home/wsw/app/dev/trading` 独立存在，可以切换开发分支或包含未提交修改，
  Webhook 不得修改它。
- 共享测试配置为 `/home/wsw/app/shared/trading/.env.test`，共享日志为
  `/home/wsw/app/shared/trading/logs`，部署锁、API 身份环境文件和当前成功记录位于
  `/home/wsw/app/deploy/trading`。
- 测试数据根固定为 `/home/wsw/app/data`，不增加 `trading` 子目录。它在测试服务器上共享；
  raw 必须保持正式源端身份。Tushare raw 保持本地 payload；Level-2 的网盘 raw Meta
  切换及本地文件回收仅按[存储 owner](../data/storage_layout.md#level-2-网盘-raw-与本地缓存)
  在兼容代码部署后，以单独授权的维护命令执行。Staging、processed、features、labels 和
  experiments 可以在存储契约变化后按受影响范围人工清理并重建。普通代码部署不得自动
  删除或迁移数据。
- 测试服务器的 `/home/wsw/app/data/raw` 固定为 HDD `/home/wsw/cold/raw` 的 bind mount；
  `staging`、`processed`、`features`、`labels` 和 `experiments` 位于承载
  `/home/wsw/app/data` 的 SSD。数据入口仍只接收一个 `ZERO_STORAGE_ROOT`，不得从代码推导
  第二个物理 root。raw mount 不存在时不得启动数据写入或让 payload 落入 SSD mountpoint；
  API unit 启动前、部署 worker 进入幂等或启动路径前、离线 runner 提交 Job 前都必须验证
  `/home/wsw/app/data/raw` 是 mountpoint，失败时必须以非成功状态停止。
- `wsw` 拥有应用、部署和运行时目录。仓库 Git remote 使用只读 GitHub deploy key；固定
  identity 与 strict host key 配置属于该 repository 的 local `core.sshCommand`，不得记录
  私钥内容。
- 目标系统必须提供 Bash、Git、GNU `flock`、curl、systemd、uv 和 uv 可发现的 Python
  3.13。非交互部署必须执行 `uv python find --no-python-downloads 3.13`，不得依赖激活的
  Conda 环境，也不得自动下载 Python。
- 必须先执行 `uv lock --check`，再执行
  `uv sync --locked --no-dev --no-install-project --no-python-downloads`。依赖安装到
  `/home/wsw/app/code/trading/.venv`，不复用旧 `/home/wsw/app/venv`。

### 顺序、并发和成功语义

1. worker 在创建锁、fetch、幂等返回或停止 API 前，必须确认
   `/home/wsw/app/data/raw` 是 mountpoint；检查失败返回非成功且不得改变部署状态。
2. worker 以 `flock` 获取唯一部署锁。
3. 只 fetch `release/auto-release` 到对应 remote-tracking ref。
4. fetch 后的 tip 必须与 delivery 的 `after` SHA 完全相同；旧或乱序 delivery 失败，不得
   部署其他 SHA，也不得让服务器降级。
5. 如果工作树已经是目标 SHA、没有 tracked 修改、API unit active 且身份健康完全匹配，
   worker 幂等成功，不重启服务。
6. 其他情况先停止 `minquant-api.service`，再以 detached HEAD、force checkout、hard reset
   和只清理非 ignored untracked 文件把 `/home/wsw/app/code/trading` 精确切到目标 SHA。
   `.env.test` 与 `logs` 必须重新链接到共享路径。
7. lock 校验和 uv sync 成功后，worker 再次 fetch 并确认远端 tip 仍等于目标 SHA；随后
   原子写入 API commit identity，启动 `minquant-api.service`。
8. `GET http://127.0.0.1:5050/health` 必须返回 200，JSON 必须且只包含
   `ok=true`、`environment=test`、`release_ref=release/auto-release` 和目标
   `commit_sha`。最多检查 30 次、间隔 2 秒，必须连续成功 2 次。
9. 只有健康检查通过且 `/home/wsw/app/deploy/trading/current-test-release` 原子写入后，
   worker 才返回 0；dispatcher 随后写入 `succeeded` result。任意失败写入 `failed` result。

部署允许中断当前 API 及内存 Job。当前契约不做自动回滚：停止服务后的 checkout、依赖、
启动或健康失败时，API 可能保持停止或不健康，result 必须为失败并由维护者通过 journal 和
记录人工接管。部署不执行数据库、Parquet schema、metadata 或其他持久化 migration。

仓库内 Webhook、dispatcher、worker 和 unit 是控制面安装源；应用 SHA 部署不得覆盖
`/usr/local/libexec` 或 systemd unit。控制面更新必须经过人工审查、复制、daemon-reload 和
重启，不得由接收中的 Webhook 自更新。

## 测试环境离线数据定时任务

- 离线数据 cron 只安装在 `wsw` 下，以 `/home/wsw/app/code/trading` 为
  `MINQUANT_PROJECT_ROOT`、`/home/wsw/app/data` 为 `ZERO_STORAGE_ROOT`。
- 安装时必须显式提供五段 `MINQUANT_OFFLINE_DATA_CRON_SCHEDULE`。仓库不拥有上游数据
  就绪时间或 cron daemon 时区，因而不提供默认时刻。
- cron 在上游前一日文件齐备后运行，不得固化业务日期。运行器默认通过
  `DateTimeUtils.days_before(DateTimeUtils.today(), 1)` 取得 Asia/Shanghai 的前一自然日，
  workflow 中行情按正式交易日历筛选，公告按自然披露日采集；休市日仍须完成公告生产。只有人工单次补跑可以用
  `MINQUANT_OFFLINE_DATA_DATE=YYYY-MM-DD` 覆盖。运行时刻必须考虑上游在次日供数，
  不得用系统 UTC 日期或“上一个交易日”替代前一自然日而在休市期间重复生产。
- 一次运行先提交并等待单日 `data-standard` Job，再提交并等待 `data-level2` Job。Standard
  失败后仍尝试 Level-2，只有二者均为 `SUCCESS` 时返回 0。
- 两个 Job 的成功包含各自 Data workflow 定义的派生分区生产；Level-2 必须完成分钟事实
  和当日 Feature/成熟 Label。Cron 不再另行调用人工 backfill 入口，也不生成融合 Feature。
- 运行器以共享 `flock` 防止重叠，锁冲突返回 75。它必须直接校验 systemd 管理的 API 健康
  身份与当前工作树完整 SHA；API 不可用或身份不符时失败，不得自行启动、停止或接管服务。
- Job 只存在于 API 内存。部署或服务重启可以中断正在等待的 cron 运行；状态请求失败不得
  导致自动重提或伪造成功。

## 正式服务器部署待确定

正式环境只允许部署 `master` 或本次 semantic-release tag。生产认证身份、审批、目标、
路径、artifact、migration、切换、健康、报警和回滚尚未定义。在这些值落地前，不得把测试
部署脚本用于生产，也不得添加静默成功的正式部署占位步骤。

## 权限与并发

- 普通 CI 只拥有 `contents: read`。
- auto-release 仅在镜像分支和维护 PR 的 job 中拥有写权限。
- release 仅在必须写 tag 和 GitHub Release 的 job 中拥有写权限；sync 仅在必须写分支的
  workflow 中拥有写权限。
- auto-release 使用单一并发组并取消旧运行，推送前确认目标 SHA 仍是最新 `dev`。
- `release/auto-release` 使用 `--force-with-lease` 更新，禁止无租约 force push。
