# 发布工作流

- **状态**：强制执行
- **适用范围**：Git 分支流转、GitHub Actions CI、测试环境与正式环境部署源、自动版本发布。
- **用途**：定义从功能开发到正式发布的唯一分支状态模型与测试部署契约。

## 分支职责

| 分支 | 职责 | 允许的主要写入方式 |
|---|---|---|
| `feature/*` | 单个功能或修复的开发分支。 | 开发者提交。 |
| `dev` | 所有开发集成的唯一目标分支。 | `feature/*` 通过 PR 合入；发布成功后由 workflow 同步 `master`。 |
| `release/auto-release` | 当前待发布内容的机器镜像，也是测试环境唯一部署源。 | auto-release workflow 从 `dev` 强制镜像；不得人工提交。 |
| `master` | 已批准的正式版本，也是正式环境唯一部署源。 | `release/auto-release` 通过 release PR 合入。 |

远端必须同时存在 `dev` 和 `master`。仓库默认分支为 `master`，默认开发分支为 `dev`。

## 状态流转

1. `feature/*` 通过 PR 合入 `dev`，PR 必须通过 CI。
2. `dev` push 后，auto-release workflow 比较 `master` 与该次 dev commit 的文件树。
3. 存在未发布文件变化时，workflow 通过 CI 后把该 commit 镜像到
   `release/auto-release`，并创建或更新指向 `master` 的 release PR。
4. GitHub 对 `release/auto-release` 的 push 触发测试环境部署。
5. release PR 经人工确认后合入 `master`；release workflow 通过 CI 后执行
   semantic-release。正式环境部署尚未实施。
6. release workflow 成功后，sync workflow 把 `master` 合入 `dev`。
7. 回同步后的 `dev` 与 `master` 文件树相同时，auto-release workflow 必须跳过；如果同步
   期间已有新功能进入 `dev`，继续生成下一轮待发布内容是预期行为。

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

### Change 终态 commit 可达性

采用 open change 且拟议目标树会删除对应 change 时，feature PR 必须使用 merge commit
或 rebase merge，使写入 `Status: adopted` 的终态 commit 在目标分支保持可达，不得 squash。
如果必须 squash，则最终树必须保留含终态 README 的必要 archive，不得同时删除 change。

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
  -> /home/wsw/app/code 精确 SHA 部署
  -> minquant-api.service
  -> 127.0.0.1:5050/health
```

腾讯云中继、公网 IP、腾讯云 TLS、SSH 部署跳转、Nginx、Docker、SQLite、外部消息队列和
cron 部署都不属于测试发布链路。Funnel 只允许映射 Webhook 接收端口，不得映射推理服务
5050。`start.sh`、`kill.sh`、`status.sh` 可以用于人工本地开发，但不是自动部署入口。

以下状态相互独立，不得互相替代：

- `release/auto-release` 更新成功：经过 CI 的测试部署源已准备好。
- Webhook 返回成功：合法 delivery 已持久化接纳，或该 delivery 已被接纳过。
- delivery result 为 `succeeded`：精确 SHA、依赖、服务启动和身份健康全部通过。
- release PR 合入 `master`：正式发布源获批准。
- semantic-release 成功：版本号、tag、changelog 和 GitHub Release 已生成。

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

- 自动部署工作树固定为 `/home/wsw/app/code`，只用于测试发布，禁止人工修改。人工开发目录
  `/home/wsw/app/dev` 独立存在，可以切换开发分支或包含未提交修改，Webhook 不得修改它。
- 共享测试配置为 `/home/wsw/app/shared/.env.test`，共享日志为
  `/home/wsw/app/shared/logs`，测试数据为 `/home/wsw/app/data`，部署锁、API 身份环境文件和
  当前成功记录位于 `/home/wsw/app/deploy`。
- `wsw` 拥有应用、部署和运行时目录。仓库 Git remote 使用只读 GitHub deploy key；固定
  identity 与 strict host key 配置属于该 repository 的 local `core.sshCommand`，不得记录
  私钥内容。
- 目标系统必须提供 Bash、Git、GNU `flock`、curl、systemd、uv 和 uv 可发现的 Python
  3.13。非交互部署必须执行 `uv python find --no-python-downloads 3.13`，不得依赖激活的
  Conda 环境，也不得自动下载 Python。
- 必须先执行 `uv lock --check`，再执行
  `uv sync --locked --no-dev --no-install-project --no-python-downloads`。依赖安装到
  `/home/wsw/app/code/.venv`，不复用旧 `/home/wsw/app/venv`。

### 顺序、并发和成功语义

1. worker 以 `flock` 获取唯一部署锁。
2. 只 fetch `release/auto-release` 到对应 remote-tracking ref。
3. fetch 后的 tip 必须与 delivery 的 `after` SHA 完全相同；旧或乱序 delivery 失败，不得
   部署其他 SHA，也不得让服务器降级。
4. 如果工作树已经是目标 SHA、没有 tracked 修改、API unit active 且身份健康完全匹配，
   worker 幂等成功，不重启服务。
5. 其他情况先停止 `minquant-api.service`，再以 detached HEAD、force checkout、hard reset
   和只清理非 ignored untracked 文件把 `/home/wsw/app/code` 精确切到目标 SHA。`.env.test`
   与 `logs` 必须重新链接到共享路径。
6. lock 校验和 uv sync 成功后，worker 再次 fetch 并确认远端 tip 仍等于目标 SHA；随后
   原子写入 API commit identity，启动 `minquant-api.service`。
7. `GET http://127.0.0.1:5050/health` 必须返回 200，JSON 必须且只包含
   `ok=true`、`environment=test`、`release_ref=release/auto-release` 和目标
   `commit_sha`。最多检查 30 次、间隔 2 秒，必须连续成功 2 次。
8. 只有健康检查通过且 `/home/wsw/app/deploy/current-test-release` 原子写入后，worker 才
   返回 0；dispatcher 随后写入 `succeeded` result。任意失败写入 `failed` result。

部署允许中断当前 API 及内存 Job。当前契约不做自动回滚：停止服务后的 checkout、依赖、
启动或健康失败时，API 可能保持停止或不健康，result 必须为失败并由维护者通过 journal 和
记录人工接管。部署不执行数据库、Parquet schema、metadata 或其他持久化 migration。

仓库内 Webhook、dispatcher、worker 和 unit 是控制面安装源；应用 SHA 部署不得覆盖
`/usr/local/libexec` 或 systemd unit。控制面更新必须经过人工审查、复制、daemon-reload 和
重启，不得由接收中的 Webhook 自更新。

## 测试环境离线数据定时任务

- 离线数据 cron 只安装在 `wsw` 下，以 `/home/wsw/app/code` 为
  `MINQUANT_PROJECT_ROOT`、`/home/wsw/app/data` 为 `ZERO_STORAGE_ROOT`。
- 安装时必须显式提供五段 `MINQUANT_OFFLINE_DATA_CRON_SCHEDULE`。仓库不拥有上游数据
  就绪时间或 cron daemon 时区，因而不提供默认时刻。
- cron 不得固化业务日期；运行器默认通过 `DateTimeUtils.today()` 得到 Asia/Shanghai
  当日，只有人工单次补跑可以用 `MINQUANT_OFFLINE_DATA_DATE=YYYY-MM-DD` 覆盖。
- 一次运行先提交并等待单日 `data-standard` Job，再提交并等待 `data-level2` Job。Standard
  失败后仍尝试 Level-2，只有二者均为 `SUCCESS` 时返回 0。
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
- release 和 sync 仅在必须写 tag、release commit 或分支的 job/workflow 中拥有写权限。
- auto-release 使用单一并发组并取消旧运行，推送前确认目标 SHA 仍是最新 `dev`。
- `release/auto-release` 使用 `--force-with-lease` 更新，禁止无租约 force push。
