# 发布工作流

- **状态**：强制执行
- **适用范围**：Git 分支流转、GitHub Actions CI、测试环境与正式环境部署源、自动版本发布。
- **用途**：定义从功能开发到正式发布的唯一分支状态模型，避免发布后回同步产生空发布。

## 分支职责

| 分支 | 职责 | 允许的主要写入方式 |
|---|---|---|
| `feature/*` | 单个功能或修复的开发分支。 | 开发者提交。 |
| `dev` | 所有开发集成的唯一目标分支。 | `feature/*` 通过 PR 合入；发布成功后由 workflow 同步 `master`。 |
| `release/auto-release` | 当前待发布内容的机器镜像，也是测试环境的唯一部署源。 | auto-release workflow 从 `dev` 强制镜像；不得人工提交。 |
| `master` | 已批准的正式版本，也是正式环境的唯一部署源。 | `release/auto-release` 通过 release PR 合入。 |

远端必须同时存在 `dev` 和 `master`。仓库默认分支为 `master`，默认开发分支为 `dev`。

## 状态流转

1. `feature/*` 通过 PR 合入 `dev`，PR 必须通过 CI。
2. `dev` push 后，auto-release workflow 比较 `master` 与该次 dev commit 的文件树。
3. 存在未发布文件变化时，workflow 通过 CI 后把该 commit 镜像到
   `release/auto-release`，并创建或更新指向 `master` 的 release PR。
4. 测试环境只允许部署 `release/auto-release`。
5. release PR 经人工确认后合入 `master`；release workflow 再次通过 CI 后执行
   semantic-release。正式环境只允许部署 `master`。
6. Release workflow 成功后，sync workflow 把 `master` 合入 `dev`。
7. 如果回同步后的 `dev` 与 `master` 文件树相同，auto-release workflow 必须跳过，
   不得更新 `release/auto-release`。如果同步期间已有新功能进入 `dev`，其文件树仍然
   不同，此时继续生成下一轮待发布内容是预期行为。

## PR 合并约束

### Release PR

semantic-release 必须能够分析 `dev` 中的原始 Conventional Commit 类型。release PR
不得 squash 成固定的 `chore(release):` 标题；必须使用 merge commit 或 rebase merge
保留原始提交。

版本规则如下：

- commit body/footer 包含 `BREAKING CHANGE:` 或 `BREAKING CHANGES:`：major。
- `feat:`：minor。
- `fix:`、`perf:`：patch。
- `docs:`、`test:`、`refactor:`、`chore:`、`build:`、`ci:`、`style:`：不发布。
- `type!:` header 不作为 breaking release 依据，并由 CI 拒绝。

### Change 终态 commit 可达性

采用 open change 且拟议目标树会删除对应 change 时，feature PR 必须使用 merge commit
或 rebase merge，使写入 `Status: adopted` 的终态 commit 在目标分支保持可达，不得 squash。
如果必须 squash，则最终树必须保留含终态 README 的必要 archive，不得同时删除 change。
Change 状态的生效与关闭语义由根目录 `AGENTS.md` 拥有；本文只拥有保持 Git commit
可达性的 PR 合并方式。

## 部署边界

GitHub workflow 负责确定经过 CI 的部署源和发布顺序。测试服务器由
`release/auto-release` 更新产生的 repository push webhook 触发；正式服务器部署尚未
实现。推送分支、Webhook HTTP 成功、SSH 成功或进程存在都不能单独表示部署成功。

### 当前设计阶段

项目当前实现代码晋级、发布编排和测试服务器执行入口，但仓库不拥有腾讯云上的 Webhook
接收器安装状态。以下事件只表示对应阶段完成：

- `release/auto-release` 更新成功：表示经过 CI 的测试部署源已准备好。
- 测试部署脚本返回 `0`：表示目标 SHA 已原子切换，且部署后身份健康检查通过。
- release PR 合入 `master`：表示正式部署源已获批准。
- semantic-release 成功：表示版本号、tag、changelog 和 GitHub Release 已生成。

外部 Webhook 接收器只有在持久化接纳合法 delivery 后才能返回成功；接纳成功不表示部署
成功。接收器必须单独记录部署脚本的终态。正式部署入口和部署后验证没有落地，因此任何
workflow 或外部系统都不得产生“正式环境部署成功”的状态。

### 测试服务器部署契约

#### 触发、认证与 secret owner

- Webhook 接收器必须校验原始 body 的 `X-Hub-Signature-256`，并精确校验 repository、
  `push` event 和 `refs/heads/release/auto-release`。`X-GitHub-Delivery` 是 `RUN_ID`，
  payload 的完整 `after` SHA 是 `DEPLOY_SHA`；两者必须传给
  `scripts/deploy_local_machine.sh`。
- 接收器必须先持久化 delivery 再异步执行部署，重复 delivery 由 `RUN_ID` 去重。脚本非零
  退出必须记录为部署失败，不得转换为 Webhook 成功状态。
- 腾讯云跳板机与测试服务器通过 Tailscale 网络通信，并使用普通 OpenSSH。跳板机执行账号
  和测试服务器部署账号必须由维护者显式配置，不得由仓库推断；测试服务由该 SSH 部署账号
  运行。`TRAINING_MACHINE` 必须配置为包含目标用户的 SSH alias 或 `user@host`，不得来自
  Webhook payload。
- SSH 私钥、SSH config 和固定 host key 文件由跳板机执行账号拥有，只允许该账号读取；
  host key 必须通过独立可信渠道预置，脚本固定启用
  `StrictHostKeyChecking=yes`。Webhook secret 由接收器进程的 secret store 拥有。以上
  secret、Tailscale auth key 和应用业务凭证都不得进入仓库或部署日志。
- 测试部署身份不得用于生产。生产身份、审批人和 secret owner 尚未确定。

接收器异步 worker 的稳定调用形式为：

```text
RUN_ID=<X-GitHub-Delivery> \
DEPLOY_SHA=<40-character-after-sha> \
TRAINING_MACHINE=<target-user>@<tailscale-ssh-alias> \
SSH_KNOWN_HOSTS=<absolute-pinned-known-hosts-path> \
REMOTE_SCRIPT=<deploy-root>/deploy/deploy_release.sh \
<relay-dir>/deploy_local_machine.sh
```

#### 目标路径与运行时

- 跳板机入口可以安装在任意 `<relay-dir>/deploy_local_machine.sh`；`DEPLOY_DIR` 未设置时取
  脚本自身目录。目标机入口安装为 `<deploy-root>/deploy/deploy_release.sh`；`DEPLOY_ROOT`
  未设置时取该脚本目录的父目录。账号名和 home 路径都不属于部署契约。
- 目标机 Git source repository 默认为 `<deploy-root>/app/code`；不可变 release 位于
  `<deploy-root>/app/releases/<commit-sha>`；内容寻址的 Python 依赖环境位于
  `<deploy-root>/app/environments/<runtime-id>`；`<deploy-root>/app/current` 只能是指向当前
  release 的 symbolic link。对应绝对路径可以通过目标机的 `DEPLOY_*` 配置覆盖。
- 测试运行配置默认为 `<deploy-root>/app/shared/.env.test`，服务日志根目录为
  `<deploy-root>/app/shared/logs`，测试数据根目录为 `<deploy-root>/data`，部署日志和当前部署
  记录位于 `<deploy-root>/deploy`。Release 目录只通过 symbolic link 引用共享配置和日志。
- Python 固定为项目要求的 3.13。uv 必须可由目标机非交互部署进程的 `PATH` 发现，或由
  `MINQUANT_UV_BIN` 指定；目标机还必须提供 uv 可发现的 Python 3.13，部署不得自动下载
  解释器。服务使用 tmux session `minquant_api`，以 `start.sh`、`kill.sh` 和 `status.sh`
  作为唯一服务管理入口。
- 跳板机必须提供 Bash、OpenSSH 和 `flock`；测试服务器必须提供 Bash、Git worktree、
  GNU `mv`、`realpath`、`flock`、curl、tmux、`mktemp` 和 uv。缺少任一必要命令时部署
  失败。
- 部署必须先执行 `uv lock --check`，再以
  `uv sync --locked --no-dev --no-install-project --no-python-downloads` 准备依赖环境。
  依赖构建、下载或校验失败必须发生在停服前并使部署失败。
- `runtime-id` 必须绑定 `uv.lock` 原始内容、Python 精确身份和固定安装 profile。环境必须
  先在同文件系统临时目录构建成功，再原子发布到 `environments/<runtime-id>`；已存在环境
  只能执行只读同步检查，不得就地修改。不同 release 在 runtime identity 相同时复用该
  环境，并各自以 `.venv` symbolic link 引用；uv 下载缓存可以共享。
- 部署允许中断当前 API 和内存中的 Job。Job 不跨服务重启恢复；接收器和操作者不得把被
  中断 Job 声称为成功或继续执行。

#### 部署单元、顺序与并发

1. 接收器必须提供完整 40 位 `DEPLOY_SHA`。目标机只 fetch
   `release/auto-release`，并要求 fetch 后的远端 tip 与该 SHA 完全相同；过期或乱序
   delivery 必须失败，不能部署其他 commit。
2. 目标机以 detached Git worktree 创建 `<deploy-root>/app/releases/<commit-sha>`，已存在
   release 必须具有相同 HEAD 且无 tracked 修改。
3. `.env.test` 与 `logs` symbolic link 注入完成、入口文件存在且可执行后，必须检查 lock、
   准备或验证目标 runtime environment，并把 release 的 `.venv` 链接到该环境。该阶段的
   任意失败不得停止当前服务。
4. 必须再次 fetch 并校验远端 tip，仍等于目标 SHA 才允许停止当前服务。
5. 服务停止后，通过同目录临时 symbolic link 和原子 rename 切换 `current`，再启动候选。
6. 跳板机和目标机都必须持有部署锁；同一层一次只能执行一个 deployment。
7. 成功后必须原子更新 `<deploy-root>/deploy/current-test-release`，记录 `RUN_ID`、environment、
   release ref、commit SHA、release directory、runtime ID、runtime directory 和部署时间。
   日志必须使用相同 `RUN_ID` 和 commit SHA 关联两台机器。

#### 健康检查与成功语义

- `GET http://127.0.0.1:5050/health` 必须返回 `200`，JSON 必须且只包含 `ok=true`、
  `environment=test`、`release_ref=release/auto-release` 和目标 `commit_sha`。
- `status.sh` 必须同时确认 tmux session 存在、HTTP 请求在 2 秒内完成、响应 schema 与
  上述部署身份完全一致。
- 部署最多检查 30 次，间隔 2 秒，必须连续成功 2 次。隔离自动测试可通过显式环境变量
  缩短次数，实际测试服务器使用这些默认值。
- FTP、Tushare 和历史数据可用性不属于 API readiness；它们在具体 Job 首次消费时按各自
  owner 失败，不得使一个尚未接收 Job 的健康服务被判为部署失败。
- 只有候选健康检查通过并写入部署记录后，部署脚本才能返回 `0`。任何输入、SSH、fetch、
  worktree、停服、启动、健康或记录失败都必须返回非零。

#### 测试环境离线数据定时任务

- 离线数据 cron 只安装在测试服务器的部署账号下，并只操作测试环境。第一次测试部署成功
  后，以稳定的 `<deploy-root>/app/current` 作为 `MINQUANT_PROJECT_ROOT`、
  `<deploy-root>/data` 作为 `ZERO_STORAGE_ROOT` 执行
  `scripts/install_offline_data_cron.sh`。账号名、home 路径和部署根目录不得由脚本推断。
- 安装时必须显式提供五段 `MINQUANT_OFFLINE_DATA_CRON_SCHEDULE`。仓库不拥有上游数据就绪
  时间或服务器 cron daemon 的时区配置，因此不提供默认执行时刻；维护者必须让所选时刻
  位于当日数据源就绪之后，并确认 cron daemon 对该表达式使用的时区。安装器只替换自己
  marker 内的条目，保留该账号的其他 crontab 内容。
- `scripts/uninstall_offline_data_cron.sh` 只删除同一部署账号 crontab 中上述 marker 管理的
  完整区块，并保留其他条目；区块不存在或该账号没有 crontab 时幂等成功。marker 不平衡、
  crontab 读取失败或写回失败必须返回非零且不得声称卸载成功。卸载只阻止后续触发，不取消
  已开始的运行或 Job，也不停止 API、删除日志或删除数据。
- cron 不得固化业务日期。`scripts/run_offline_data_jobs.sh` 每次触发时使用
  `DateTimeUtils.today()` 得到 `Asia/Shanghai` 当日；仅人工单次补跑可以通过
  `MINQUANT_OFFLINE_DATA_DATE=YYYY-MM-DD` 覆盖，安装器不得把该变量写入 crontab。
- 一次运行必须先提交并等待以下单日 Standard Job 到达终态，再提交 Level-2 Job：

  ```json
  {"kind":"data-standard","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}
  {"kind":"data-level2","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}
  ```

  两个请求分别对应一个完整 workflow；不得使用旧项目的 `pipeline`、`group` 或 `date`
  字段。Standard 失败后仍必须尝试 Level-2，只有两个 Job 都到达 `SUCCESS` 时运行脚本才
  返回 `0`；`SKIPPED`、`FAILED`、`CANCELLED`、无效响应或 HTTP 失败都使对应任务失败。
- 运行器以共享 `flock` 防止相邻 cron 重叠；锁冲突固定返回 `75`，不提交 Job。日志默认
  追加到 `<deploy-root>/app/current/logs/cron/offline_data_jobs.log`，锁位于同一共享日志树，
  因而 release 切换不会产生第二把锁。
- 提交前必须用 `status.sh` 校验 API 的 `environment=test`、
  `release_ref=release/auto-release` 与 `MINQUANT_PROJECT_ROOT` 当前 HEAD。API session 不存在
  时，运行器通过 `start.sh` 注入上述身份、当前完整 SHA 与 `ZERO_STORAGE_ROOT` 后启动；
  session 已存在但 readiness 或身份不匹配时必须失败，不得停止或接管身份不明的进程。
- Job 只存在于 API 内存中。部署或服务重启可以中断正在等待的 cron 运行；后续状态请求
  失败必须使本次运行失败，不得把该 Job 重提或声称成功。

#### 回滚、保留与人工接管

- 候选启动或健康失败时，脚本必须停止候选、把 `current` 原子切回部署开始时解析的上一
  managed release、重新启动并执行相同身份健康检查。候选部署仍返回非零，不能因回滚
  成功改写为成功。
- 第一次 managed deployment 没有上一 managed release，无法自动回滚；失败时服务保持
  停止并删除失败的 `current` link，由测试环境维护者接管。第一次成功后必须始终保留
  current 与至少一个上一 release。
- 脚本不自动删除 release worktree 或 runtime environment。磁盘清理由测试环境维护者执行，
  且不得删除 current、正在回滚或仍需保留的上一 release，也不得删除这些 release 的
  `.venv` 所引用的 runtime environment。
- 部署不执行数据库、Parquet schema、metadata 或其他持久化 migration。任何会使旧代码
  无法继续读取既有状态的变更不得依赖本自动回滚契约，必须先形成独立的兼容迁移决策。
- 回滚健康仍失败时，目标脚本必须返回非零并在目标日志留下 candidate 与 previous SHA；
  管理腾讯云接收器、跳板机和测试服务器账号的测试环境维护者负责报警和人工恢复。

### 正式服务器部署待确定

正式环境仍只允许部署 `master` 或本次 semantic-release tag。新增正式部署入口前必须由
用户明确采用并补齐：

- 生产认证身份、审批者、secret owner 和吊销流程。
- 生产目标、路径、运行账号、数据、日志、配置和端口隔离。
- 不可变 artifact 与依赖构建方式，不得引入跨运行时身份共享的可变 Python 环境。
- 服务无损或有损切换、持久化 migration、健康检查、报警和人工接管。
- 可重复执行的完整回滚，以及新版本已写入持久化状态后的向后兼容边界。

在这些值落地前，不得把测试部署脚本用于生产，也不得添加静默成功的正式部署占位步骤。

## 权限与并发

- 普通 CI 只拥有 `contents: read`。
- auto-release 仅在镜像分支和维护 PR 的 job 中拥有写权限。
- release 和 sync 仅在必须写 tag、release commit 或分支的 job/workflow 中拥有写权限。
- auto-release 使用单一并发组并取消旧运行，且在推送前确认目标 SHA 仍是最新 `dev`。
- `release/auto-release` 使用 `--force-with-lease` 更新，禁止无租约的 force push。
