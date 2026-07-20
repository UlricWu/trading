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

远端必须同时存在 `dev` 和 `master`。仓库默认分支为 `dev`。

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

GitHub workflow 只负责确定经过 CI 的部署源和发布顺序。服务器部署实现必须在具备明确
的认证方式、目标路径、回滚策略、健康检查和 secret owner 后接入：

- 测试部署 job 必须依赖 `sync-release-branch`，checkout
  `release/auto-release`，并使用 GitHub `test` Environment。
- 正式部署 job 必须位于 master 的 Release workflow 中，依赖 CI 和
  semantic-release，checkout `master` 或本次发布 tag，并使用 GitHub
  `production` Environment。
- `test` 与 `production` secrets 必须存放在各自 Environment；不得放入仓库文件或
 传给普通 CI job。

在部署契约落地前，不得添加会静默成功的占位部署步骤，也不得把推送分支本身宣称为
服务器部署成功。

### 当前设计阶段

项目当前只实现代码晋级和发布编排，不实现服务器部署。以下事件只表示部署源已经
准备好，不表示目标服务器已经运行新版本：

- `release/auto-release` 更新成功：表示经过 CI 的测试部署源已准备好。
- release PR 合入 `master`：表示正式部署源已获批准。
- semantic-release 成功：表示版本号、tag、changelog 和 GitHub Release 已生成。

在服务器部署 job、服务器端执行入口和部署后验证全部落地前，workflow 不得产生
“测试环境部署成功”或“正式环境部署成功”的状态。

### 待确定的服务器部署契约

部署实施前必须形成明确 owner 决策，并在本文或专门的 deployment owner doc 中记录
最终值。不得由 workflow 根据仓库名、分支名、runner 环境或已有 secret 名称自行推断。

#### 认证与 secret owner

必须确定 GitHub Actions 以何种身份访问部署目标，可选方式包括 SSH、云平台 OIDC、
Kubernetes ServiceAccount、部署 webhook 或位于目标网络内的 self-hosted runner。

至少需要明确：

- 测试与生产是否使用独立身份。
- 服务器地址、端口、部署用户和最小权限。
- 私钥、token、host key 或 OIDC trust policy 的 owner、轮换和吊销方式。
- GitHub `test` 与 `production` Environment 中允许保存的 secret 名称。
- 主机身份校验方式；使用 SSH 时必须固定 host key，不得关闭校验。
- 哪些人员或 GitHub team 可以批准生产 Environment deployment。

应用运行时的 FTP、Tushare、AD 等凭证不属于服务器部署凭证，不得复用为部署认证。

#### 目标路径与运行时

必须分别确定测试与生产的目标服务器、绝对部署目录、运行账号和服务管理方式，至少
包括：

- test/prod 主机或集群标识。
- release 安装目录、共享数据目录、日志目录和环境文件位置。
- Python 版本以及使用 `uv`、`pip`、Conda、wheel 或容器镜像中的哪一种运行时。
- 依赖安装是否允许访问公网，以及使用哪个 lock file 或 artifact 保证可复现。
- 服务入口、监听地址、端口、进程管理器和服务名称。
- 部署时如何处理正在运行的 API、job 和其他长生命周期进程。
- 测试与生产存储、日志、业务凭证和端口必须隔离的具体方式。

测试环境的来源分支是 `release/auto-release`，但每次部署必须解析并记录确定的 commit
SHA。生产环境必须部署确定的 master commit 或 semantic-release tag，不得在服务器上
无边界执行 `git pull` 后把移动分支状态当作已验证版本。

#### 部署单元与执行顺序

必须选择源码 checkout、wheel、归档包或容器镜像中的一种不可变部署单元，并定义：

1. 如何从通过 CI 的 SHA/tag 生成部署单元。
2. 如何校验部署单元与目标 SHA/tag 一致。
3. 如何安装到与当前版本隔离的新目录。
4. 如何注入目标 Environment 的运行时配置。
5. 如何执行部署前检查、数据/schema migration 和服务切换。
6. 如何原子切换到新版本，避免在同一目录原地覆盖造成半部署状态。
7. 如何记录实际部署的版本、时间、workflow run 和操作者。

#### 回滚

必须定义上一已知可用版本的记录位置和可重复执行的回滚命令，至少包括：

- 保留多少个历史 release 或 artifact。
- 使用 tag、commit SHA、wheel 或 image digest 中的哪一个作为回滚标识。
- 如何恢复应用目录、依赖、环境配置和进程状态。
- 部署失败时是否自动回滚，哪些失败只能人工处理。
- 回滚后必须重新执行哪些健康检查。
- 数据库、Parquet schema、metadata 或其他持久化状态变化是否向后兼容。
- 新版本已经写入数据时，旧版本是否仍能安全读取和继续运行。

只有代码可恢复、但持久化数据不可恢复时，不得把流程描述为完整自动回滚。

#### 健康检查与成功语义

必须定义部署成功的客观条件，不能以 SSH 命令退出码、进程存在或端口已打开作为唯一
依据。测试与生产至少需要分别确定：

- health/readiness URL 或等价的服务检查命令。
- 期望的状态码和响应字段。
- 响应中的 environment、version/tag 和 commit SHA 校验。
- 单次超时、最大等待时间、重试间隔和连续成功次数。
- 必需依赖与可降级依赖的判断边界。
- 健康检查失败后停止、回滚、二次检查和报警的顺序。
- 回滚后的健康检查仍失败时由谁接管。

### 开始实现部署前的准入条件

只有以下信息全部明确后，才可以新增 `deploy-test` 或 `deploy-production` job：

- 认证方式和 secret owner。
- 测试/生产目标及绝对路径。
- 不可变部署单元和构建方式。
- 服务启动、停止和切换方式。
- 环境配置注入方式。
- health/readiness 成功条件。
- 自动或人工回滚流程。
- 持久化数据兼容性判断。
- 报警和人工接管 owner。

## 权限与并发

- 普通 CI 只拥有 `contents: read`。
- auto-release 仅在镜像分支和维护 PR 的 job 中拥有写权限。
- release 和 sync 仅在必须写 tag、release commit 或分支的 job/workflow 中拥有写权限。
- auto-release 使用单一并发组并取消旧运行，且在推送前确认目标 SHA 仍是最新 `dev`。
- `release/auto-release` 使用 `--force-with-lease` 更新，禁止无租约的 force push。
