# 测试环境自动部署：从零安装、原理与排障指南

> **文档性质**：操作指南，不是发布语义 owner。

> **正式契约**：以 [发布工作流](release_workflow.md) 为准；如两者冲突，应先修订本指南。

> **适用范围**：Ubuntu 测试服务器上的 GitHub → Tailscale Funnel → Webhook →
> systemd → 精确 SHA 部署。

> **不适用范围**：正式生产部署、腾讯云中继、推理服务公网发布。

> **已验证环境**：Ubuntu 24.04.3、systemd 255、Tailscale 1.92.5、uv 0.11.7、
> Python 3.13。

这份文档不只给出命令，还解释每个工具和关键参数为什么存在。目标是让维护者知道：

1. 哪些操作只在首次安装时执行；
2. 哪些动作会在每次发布时自动执行；
3. 删除某一层后会失去什么保证；
4. 出错时应该检查哪一层，而不是重新安装全部组件。

文中的密钥和业务凭证都使用占位符。任何真实 Secret、Token、密码或 SSH 私钥都不得进入
仓库、命令历史、Webhook payload 或日志。

建议按目标阅读：

- 只想理解架构：读第 1、2、4 节；
- 第一次安装服务器：按第 3 节顺序执行；
- 日常发布：只需第 5 节；
- 出现故障：从第 6 节对应症状开始；

## 1. 先理解：真正发生了什么

### 1.1 完整链路

~~~text
开发者
  │
  │ feature/* PR 合入 dev
  ▼
GitHub dev
  │
  │ CI 通过后由 workflow 镜像
  ▼
GitHub release/auto-release
  │
  │ Push Webhook：只发送仓库、ref、after SHA 等 JSON
  ▼
Tailscale Funnel：公网 HTTPS 443、域名、TLS 终止
  │
  │ 反向代理
  ▼
127.0.0.1:9000 Webhook 接收器
  │
  │ HMAC、event、repo、ref、SHA、delivery ID 校验
  │ delivery 持久化并立即响应 GitHub
  ▼
/var/lib/minquant-webhook/queue
  │
  │ user systemd path 发现非空目录
  ▼
dispatcher
  │
  │ 固定调用部署 worker，只传校验后的 delivery ID 与 SHA
  ▼
部署 worker
  │
  │ flock → fetch → tip 等于 SHA → checkout → uv sync
  ▼
minquant-api.service
  │
  │ 127.0.0.1:5050/health 精确身份检查
  ▼
succeeded / failed result
~~~

这里最容易产生的误解是：**Webhook 不传代码**。GitHub 只发送一个经过签名的通知，
其中的 <code>after</code> 表示应该部署的 commit。真正的代码仍由 Ubuntu 使用只读 deploy
key 从 GitHub 执行 <code>git fetch</code> 获取。

### 1.2 为什么看起来比一段 git pull 脚本复杂

最短的原型确实可以只有：

~~~text
收到 HTTP 请求 → git pull → 重启进程
~~~

但它回答不了“谁触发的、部署哪个 commit、请求重复怎么办、两个请求乱序怎么办、进程
退出怎么办、重启后怎么办、部署后运行的究竟是不是目标版本”等问题。当前结构把这些问题
分别交给一个边界负责：

| 机制 | 解决的问题 | 删除后失去的保证 |
|---|---|---|
| GitHub Actions | 只有通过 CI 的 <code>dev</code> 才镜像为测试发布源。 | 未通过 CI 的代码也可能进入自动部署分支。 |
| Tailscale Funnel | 提供公网域名、443、TLS 和到回环端口的代理。 | 必须自己处理公网 IP、端口、DNS、证书和续期。 |
| HMAC Secret | 证明请求 body 是持有 Secret 的 GitHub 发出的，且传输后未被修改。 | 任意公网访问者都能伪造部署请求。 |
| 仓库和 ref allowlist | 即使签名正确，也只允许指定仓库的测试发布分支。 | 同仓库其他分支 push 也可能部署。 |
| delivery 原子记录 | 同一个 GitHub delivery 只接纳一次。 | GitHub 重试会造成重复部署。 |
| 磁盘 queue | HTTP 接收与耗时部署解耦；接收器崩溃后任务仍在。 | 请求返回前要等待部署，或进程退出时丢任务。 |
| systemd | 托管接收器、dispatcher 和 API，提供启动、重启、日志与开机恢复。 | 需要自行实现守护、日志、重启和注销 SSH 后存活。 |
| 精确 SHA 与远端 tip 相等 | 旧消息和乱序消息不能让服务器降级。 | 一次晚到的旧 Webhook 可能部署旧版本。 |
| flock | 同一时刻只有一个 worker 修改工作树和服务。 | 并发 checkout、依赖同步和重启会互相破坏。 |
| uv lock 校验与 locked sync | 服务器依赖必须与仓库锁文件一致。 | 相同 commit 可能因解析出不同依赖而行为不同。 |
| 身份健康检查 | 不只检查“端口活着”，还确认服务报告目标环境、分支和 SHA。 | 旧进程或错误环境也可能被误判为成功。 |
| 独立低权限接收用户 | 公网接收器不能写应用目录，也不能获得 <code>wsw</code> Shell。 | 接收器漏洞会直接扩大为部署用户权限。 |

因此，复杂度不是为了“让部署高级”，而是为了得到四个可验证结果：

1. **来源可信**：请求确实来自持有 Secret 的 GitHub。
2. **版本精确**：部署且仅部署该次 release branch 的最新完整 SHA。
3. **过程可恢复**：接收器或 dispatcher 短暂退出不会静默丢失已接纳任务。
4. **结果可证明**：磁盘 result、systemd 日志和健康身份共同说明成功或失败。

### 1.3 为什么当前测试链路不走腾讯云

这不是“腾讯云不好”的判断，而是针对当前测试环境做删除测试：

> 删除腾讯云中继后，GitHub 是否仍能安全地通知 Ubuntu、Ubuntu 是否仍能取得代码、精确
> 部署并留下结果证据？

答案是可以。Funnel 已经解决唯一需要公网组件解决的问题——让 GitHub 通过公网 HTTPS
到达 Ubuntu 的回环 Webhook；Git fetch、部署、重启和健康检查本来就在 Ubuntu 本机完成。
因此腾讯云在当前链路中没有独立、不可替代的职责。

两种链路对比：

~~~text
当前链路
GitHub
  → Tailscale Funnel
  → Ubuntu Webhook
  → Ubuntu 本地 queue、部署与健康检查

腾讯云中继链路
GitHub
  → 腾讯云公网接收器
  → 腾讯云校验、持久化和重试
  → SSH、Tailscale 或另一个认证通道
  → Ubuntu 部署与健康检查
~~~

#### 腾讯云中继不能消除哪些工作

| 问题 | 直接 Funnel | 加腾讯云中继后 |
|---|---|---|
| GitHub 公网 HTTPS 入口 | Funnel 已提供 <code>*.ts.net:443</code>、TLS 和代理。 | 改为自己维护云端入口；并没有消除入口，只是换了位置。 |
| Webhook HMAC | Ubuntu 接收器校验一次。 | 云端必须校验；若 Ubuntu 仍校验就是两层，若不校验则必须设计云端到 Ubuntu 的新信任边界。 |
| 去重与持久化 | delivery、queue、result 全在最终执行主机。 | 云端要么再实现一套 queue/retry，要么只是无状态转发且没有新增可靠性。 |
| 精确 SHA 部署 | Ubuntu 本机 fetch 并验证 remote tip。 | 仍必须在 Ubuntu 做；腾讯云不能替代目标工作树和本机服务健康。 |
| GitHub 代码获取 | Ubuntu 使用只读 deploy key fetch。 | 仍由 Ubuntu fetch；Webhook 中继不会把代码变成更近的 artifact。 |
| Ubuntu 进程管理 | systemd 本机管理。 | 仍然需要；云主机不能管理 Ubuntu 上已经退出的本地进程。 |

换句话说，腾讯云中继有两种可能：

1. **只做反向代理**：它没有增加当前所需能力，但多了一台主机和一个网络跳点；
2. **承担校验、队列和调度**：它确实增加能力，但也必须新增第二套持久状态、重试语义、
   凭证、日志、监控和故障恢复。

当前只需要单台 Ubuntu 的测试发布，因此第二种能力没有业务需求，第一种又是重复实体。

#### 减少的安全与运维面

直接 Funnel 后不再需要让腾讯云持有或管理：

- GitHub Webhook Secret 的另一份副本；
- 到训练 Ubuntu 的 SSH 部署私钥或 forced-command；
- 云端 Webhook 接收、转发和重试程序；
- Nginx 或同类公网反向代理；
- 云主机安全组中的 Webhook 公开端口；
- 自有域名方案下的 DNS、TLS 证书申请、部署和续期；
- 中继主机的系统补丁、systemd、磁盘、日志和报警。

最小权限边界也更清楚：

~~~text
公网不可信请求
  → Ubuntu 上的 minquant-webhook
  → 只能写 spool
  → wsw 才能部署
~~~

如果经过腾讯云，必须再回答“腾讯云被攻破后能否登录 Ubuntu”“云端转发请求如何认证”
“SSH key 可以执行什么”“云端已返回 2xx 但转发失败怎么办”等问题。它们都可以被正确
设计，但在当前单机测试目标下没有必要先创造这些问题再解决。

#### 为什么中继反而会增加状态一致性问题

直接链路只有一个 durable acceptance 点：Ubuntu 写入 delivery 后才向 GitHub 返回 202。
GitHub 收到 202 后，任务和执行目标已经在同一台机器。

加入腾讯云后至少要定义：

~~~text
GitHub 收到的 2xx 表示：
  A. 腾讯云收到了？
  B. 腾讯云已经持久化？
  C. Ubuntu 已经收到？
  D. Ubuntu 已经部署成功？
~~~

如果选择 A 或 B，腾讯云必须负责到 Ubuntu 的持久重试和去重；如果选择 C，云端请求会等待
跨主机转发；如果错误地选择 D，GitHub Webhook 会被一次长部署阻塞。还要处理“云端成功、
Ubuntu 失败”“云端重试、Ubuntu 已执行”“两个主机时钟和日志如何关联”等分布式状态。

当前把 delivery、queue、worker 和 result 放在目标 Ubuntu 上，避免了这套跨主机一致性
协议。

#### 性能不是采用腾讯云中继的理由

Webhook body 很小，只包含事件元数据；大代码和依赖不会经过 Webhook。真正的数据路径是
Ubuntu 自己从 GitHub fetch 和由 uv 同步依赖。因此增加腾讯云中继：

- 不会加速 Git fetch；
- 不会减少 Ubuntu 的依赖安装时间；
- 不会缩短 API 重启或健康检查；
- 只会给很小的 HTTPS POST 增加一个转发跳点。

当前选择主要基于安全边界和运维实体数量，不是为了节省这一跳的网络延迟。

#### Funnel 方案自身的代价

直接链路并非“没有外部依赖”。它依赖：

- Tailscale 账户、tailnet 和 Funnel 功能可用；
- Tailscale 公网 edge、MagicDNS/DNS 与证书服务；
- 训练 Ubuntu 上的 <code>tailscaled</code> 持续在线；
- 所在网络允许 Tailscale 建立连接。

也就是说，我们把公网域名、TLS 和 NAT 穿透的运维责任交给 Tailscale，而不是自己在腾讯云
维护。对当前测试环境，这是更小的操作面；它不自动成为生产 SLA 或长期供应商选择。

#### 哪些条件出现时应重新评估腾讯云

出现以下任一真实需求时，可以重新设计，而不是机械坚持 Funnel：

1. 组织安全或合规策略不允许 Funnel 或 <code>*.ts.net</code> 公网入口；
2. 必须使用自有域名、WAF、固定出口/入口策略或集中审计；
3. 一个公网接入层需要调度多台训练机、多个环境或多个仓库；
4. 需要跨主机高可用 queue、告警、重试 SLA 或灾难恢复；
5. 实测证明目标网络无法稳定连接 Tailscale Funnel；
6. 正式生产环境定义了独立网关、审批、artifact、回滚和隔离要求。

届时腾讯云不能只作为“临时转发脚本”加入。新的正式设计至少要明确：

- 云端和 Ubuntu 各自的认证身份与最小权限；
- Secret 和 SSH/机器凭证的存放、轮换和撤销；
- delivery 的唯一 owner、2xx 含义、重试、去重和终态；
- 精确 SHA 或不可变 artifact 如何跨边界绑定；
- TLS、域名、网络访问控制、监控、保留期限和故障恢复；
- 云端成功但 Ubuntu 失败时由谁接管；
- 与 Funnel 方案的切换和回退步骤。

在这些需求尚不存在时，当前结论是：

~~~text
Funnel 已覆盖公网入口
+ Ubuntu 本机已覆盖可信部署
= 腾讯云中继没有剩余的必要职责
~~~

腾讯云新实例和历史目录可以保留，但它们不进入当前测试发布链路，也不因本决策自动删除。

### 1.4 哪些是一次性操作，哪些是日常操作

| 时机 | 人工动作 |
|---|---|
| 首次安装 | 安装依赖、创建身份和目录、配置 deploy key、复制控制面、创建 Secret、启用 systemd、启用 Funnel、配置 GitHub Webhook。 |
| 每次测试发布 | 开发者只需把 feature PR 合入 <code>dev</code>；其余步骤自动执行。 |
| 控制面脚本或 unit 改变 | 人工审查并重新复制到 <code>/usr/local/libexec</code> 或 systemd 目录。应用自动部署不会自更新控制面。 |
| 部署失败 | 查看 result、三类 journal 和当前 release identity；当前契约不自动回滚。 |

## 2. 固定边界与最终目录

### 2.1 身份

| 身份 | 用途 | 为什么不用同一个用户 |
|---|---|---|
| <code>minquant-webhook</code> | 运行公网 Webhook 接收器；system user、<code>nologin</code>。 | 接收器只需写入 spool，不应该获得应用代码写权限或交互 Shell。 |
| <code>wsw</code> | 运行 dispatcher、部署 worker 和测试 API。 | 它拥有应用目录，但不直接处理未验证的公网 body。 |
| <code>minquant-deploy</code> group | 让两个身份只通过 spool 交换最小记录。 | 不给接收器加入 <code>wsw</code> 身份，也不需要 sudo 或 SSH 跳板。 |
| root | 安装控制面、保存 Webhook Secret、管理 system service。 | 防止自动部署的应用 commit 改写接收器或部署程序。 |

Webhook 使用 system service，是因为它以独立 system user 运行并接收 root credential；
dispatcher 和 API 使用 <code>wsw</code> user service，是为了让应用进程保持普通用户权限，
直接使用 <code>wsw</code> 拥有的工作树和数据目录。把三者都做成 root system service
虽然少一次 linger 配置，却会不必要地扩大部署脚本和应用的权限。

### 2.2 目录

~~~text
/home/wsw/app/
├── code/
│   └── trading/          自动部署工作树，只部署 release/auto-release
├── dev/
│   └── trading/          可选的人工开发工作树，Webhook 永不修改
├── data/                 共享测试数据根，位于 SSD
│   ├── raw/              bind mount → HDD /home/wsw/cold/raw
│   ├── staging/          可重建
│   ├── processed/        可重建
│   ├── features/         可重建
│   ├── labels/           可重建
│   └── experiments/      可重建
├── shared/
│   └── trading/
│       ├── .env.test     测试凭证，0600，不入 Git
│       └── logs/         跨 checkout 保留的日志目录
└── deploy/
    └── trading/
        ├── api-release.env   API 进程的目标 commit identity
        ├── current-test-release
        └── test-release.lock

/var/lib/minquant-webhook/
├── staging/              接收器原子写入的临时区
├── deliveries/           已接纳的不可重复 delivery 记录
├── queue/                尚未形成终态 result 的任务标记
└── results/              succeeded 或 failed 终态记录

/usr/local/libexec/
├── minquant-webhook-receiver
├── minquant-deploy-dispatcher
└── minquant-deploy
~~~

应用依赖固定安装到 <code>/home/wsw/app/code/trading/.venv</code>，由自动部署工作树独占管理，
并在每次 release 时按当前 lock 同步。

<code>/home/wsw/app/data</code> 是测试服务器共享数据根，不增加 <code>trading</code>
子目录。Raw payload 必须保持源端身份；只有 staging、processed、features、labels 和
experiments 可以在存储契约变化后按受影响范围人工清理并重建。普通代码部署不得自动删除或
迁移这些数据。

逻辑数据根仍然只有一个：<code>ZERO_STORAGE_ROOT=/home/wsw/app/data</code>。物理上仅
<code>raw/</code> 从 HDD bind mount，其他五个命名空间留在 SSD；应用代码不得知道
<code>/home/wsw/cold</code>。bind mount 缺失时不得运行数据 Job，否则普通 mountpoint 目录会
让 raw 错误写入 SSD。

### 2.3 GitHub 仓库侧的前置条件

Ubuntu 安装不是发布链路的起点。仓库必须已经包含并启用：

~~~text
.github/workflows/ci.yaml
.github/workflows/auto_release_pr.yml

feature/* → dev → release/auto-release
~~~

在开发机确认远端分支：

~~~bash
git fetch origin dev master release/auto-release

git ls-remote \
  --heads \
  origin \
  refs/heads/dev \
  refs/heads/master \
  refs/heads/release/auto-release
~~~

这些 workflow 中几个看起来复杂的参数各自有明确目的：

| 机制 | 为什么 |
|---|---|
| CI job 只有 <code>contents: read</code> | 普通测试不需要写仓库，减少 GitHub token 权限。 |
| auto-release 写 job 才有 <code>contents: write</code> | 只有镜像 release branch 的最小 job 获得写权限。 |
| <code>fetch-depth: 0</code> | workflow 要比较完整 branch 和 commit 历史，浅 clone 不足。 |
| 比较 <code>master</code> 与本次 dev commit 的文件树 | 没有未发布文件变化时不制造空 release。 |
| <code>concurrency</code> 加 <code>cancel-in-progress</code> | 新 dev push 到来后取消旧镜像任务，减少旧任务竞争。 |
| push 前再次确认远端 dev 仍等于本次 SHA | 即使旧 job 尚未取消完成，也不能发布已经过期的 dev。 |
| <code>--force-with-lease</code> | release branch 是 dev 的机器镜像，需要移动指针；lease 保证远端与预期不同时拒绝覆盖。 |

<code>--force-with-lease</code> 和 Ubuntu worker 的“远端 tip 必须等于 Webhook SHA”是两侧
不同的责任：前者保护 GitHub 分支更新，后者保护服务器不接受旧或乱序通知，不能互相替代。

## 3. 从零安装

以下命令默认在 Ubuntu 上以 <code>wsw</code> 登录执行。以 <code>sudo</code> 开头的命令
才需要 root 权限。命令提示符不属于命令，不要复制类似 <code>(dev) %</code> 的部分。

### 3.1 记录基线

~~~bash
id
lsb_release -a
systemctl --version
git --version
curl --version
flock --version
sudo ss -ltnp
~~~

为什么先做：

- <code>id</code> 记录当前用户和补充组，后续可判断权限变化是否真的生效。
- <code>systemctl --version</code> 确认系统使用 systemd，user unit 和
  <code>LoadCredential</code> 才有基础。
- <code>ss -ltnp</code> 确认 <code>127.0.0.1:9000</code> 与
  <code>127.0.0.1:5050</code> 没被其他服务占用。
- 此处查看的 shell <code>python3</code> 可能来自 Conda；控制面稍后明确使用
  <code>/usr/bin/python3</code>，应用明确使用 uv 管理的 Python，因此不靠当前 shell 激活状态。

安装基础包：

~~~bash
sudo apt-get update
sudo apt-get install --yes \
  ca-certificates \
  curl \
  git \
  openssh-client \
  openssl \
  util-linux
~~~

关键参数：

| 参数 | 含义 |
|---|---|
| <code>apt-get update</code> | 只刷新软件索引，不升级整台机器。 |
| <code>--yes</code> | 对已列出的安装确认自动回答 yes，适合可复现步骤。 |
| <code>util-linux</code> | 提供 GNU <code>flock</code>，部署互斥锁依赖它。 |
| <code>openssl</code> | 生成高熵 Webhook Secret。 |
| <code>openssh-client</code> | GitHub SSH deploy key 和 <code>git fetch</code> 依赖它。 |
| <code>ca-certificates</code> | curl、Git 和 Tailscale 验证 HTTPS 证书链需要它。 |

不安装 Nginx、Docker、SQLite 或消息队列，因为当前链路没有由它们独占解决的问题：
Funnel 已完成 TLS 和代理，systemd 已完成进程托管，文件 spool 已完成单机持久队列。

### 3.2 安装 uv 与独立 Python 3.13

项目的 <code>pyproject.toml</code> 要求 Python <code>>=3.13,<3.14</code>。Ubuntu 24.04
系统 Python 通常是 3.12，不能拿它运行应用；但接收器和 dispatcher 只使用标准库，可以安全
使用系统 Python。

如果 uv 尚未安装，使用其官方安装器：

~~~bash
curl -LsSf https://astral.sh/uv/install.sh |
  env UV_NO_MODIFY_PATH=1 sh

sudo install \
  -o root \
  -g root \
  -m 0755 \
  /home/wsw/.local/bin/uv \
  /usr/local/bin/uv

/usr/local/bin/uv --version
~~~

这里各参数的原因：

| 参数 | 原因 |
|---|---|
| <code>curl -L</code> | 跟随官方安装地址的重定向。 |
| <code>-sS</code> | 正常时安静，失败时仍打印错误。 |
| <code>-f</code> | HTTP 4xx/5xx 时返回失败，不把错误页面交给 shell。 |
| <code>UV_NO_MODIFY_PATH=1</code> | 不让安装器修改交互 shell 配置；systemd 不读取这些配置。 |
| <code>install -o root -g root</code> | 固定入口不能被部署用户替换。 |
| <code>-m 0755</code> | root 可写，所有用户可执行，不包含 world write。 |
| <code>/usr/local/bin/uv</code> | unit 和 worker 使用不依赖 shell 的绝对路径。 |

把远程脚本直接交给 shell 前，应按组织安全要求审查安装器；命令来源见文末官方资料。

安装 uv 管理的最新 Python 3.13 patch：

~~~bash
env -u CONDA_PREFIX -u VIRTUAL_ENV \
  /usr/local/bin/uv python install 3.13

env -i \
  HOME=/home/wsw \
  USER=wsw \
  LOGNAME=wsw \
  PATH=/usr/local/bin:/usr/bin:/bin \
  /usr/local/bin/uv python find --no-python-downloads 3.13
~~~

为什么这样写：

- <code>env -u CONDA_PREFIX -u VIRTUAL_ENV</code> 临时删除两个激活环境标记，避免 uv 把
  当前 Conda/venv 当成部署解释器。
- <code>python install 3.13</code> 安装符合项目范围的最新 3.13 patch，而不是改变系统
  <code>/usr/bin/python3</code>。
- 第二条命令用 <code>env -i</code> 构造接近 systemd 的干净验证环境。
- <code>--no-python-downloads</code> 在验证和部署时只允许使用已安装解释器。服务器不会在
  一次 Webhook 部署中悄悄下载另一个 Python。
- <code>HOME=/home/wsw</code> 很重要，因为 uv managed Python 保存在该用户的数据目录。

本次真实安装得到的是：

~~~text
/home/wsw/.local/share/uv/python/cpython-3.13-linux-x86_64-gnu/bin/python3.13
Python 3.13.13
~~~

patch 版本会随安装时间变化，不要把 <code>3.13.13</code> 写死到 worker；正式约束是 3.13
minor 版本。

### 3.3 安装并登录 Tailscale

尚未安装时，按 Tailscale 的 Ubuntu 官方步骤安装：

~~~bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=minquant-test
sudo tailscale status
~~~

参数含义：

| 参数 | 原因 |
|---|---|
| <code>tailscale up</code> | 启动 tailscaled 的网络身份并完成首次登录授权。 |
| <code>--hostname=minquant-test</code> | 给这台测试节点稳定、可识别的 tailnet 名称。 |
| <code>status</code> | 确认节点已在线；它不代表 Funnel 已开启。 |

首次执行 <code>tailscale up</code> 会给出登录 URL，需要在浏览器中授权。此时**不要先开
Funnel**：必须先让本机 <code>127.0.0.1:9000</code> 的接收器正常工作，否则公网入口只会
代理到一个空端口。

Funnel 是 Tailscale 的公网入口功能，不等同于普通 tailnet 访问。它只负责域名、TLS 和
代理，不负责 HMAC、分支判断或部署。

### 3.4 创建最小权限身份

~~~bash
sudo groupadd minquant-deploy

sudo useradd \
  --system \
  --gid minquant-deploy \
  --home-dir /nonexistent \
  --no-create-home \
  --shell /usr/sbin/nologin \
  minquant-webhook

sudo usermod \
  --append \
  --groups minquant-deploy \
  wsw

sudo loginctl enable-linger wsw
~~~

参数逐项说明：

| 参数 | 原因 |
|---|---|
| <code>--system</code> | 创建服务身份，不是普通登录账户。 |
| <code>--gid minquant-deploy</code> | 接收器创建的 spool 文件继承部署协作组。 |
| <code>--home-dir /nonexistent</code> | 明确该服务不依赖 home。 |
| <code>--no-create-home</code> | 不产生没有用途的可写目录。 |
| <code>--shell /usr/sbin/nologin</code> | 禁止该身份获得交互 shell。 |
| <code>usermod --append --groups</code> | 把组追加给 <code>wsw</code>，不覆盖它现有的 sudo 等组。 |
| <code>enable-linger</code> | 让 <code>wsw</code> 的 user systemd 在 SSH 注销后仍运行，并可在开机后启动。 |

执行后确认账户数据库：

~~~bash
getent group minquant-deploy
getent passwd minquant-webhook
id wsw
loginctl show-user wsw --property=Linger
~~~

预期 <code>id wsw</code> 包含 <code>minquant-deploy</code>，Linger 为
<code>yes</code>。

#### 一个容易忽略但真实发生过的问题：user manager 的组不会自动刷新

<code>usermod</code> 只修改账户数据库。已经运行的 <code>wsw</code> user systemd manager
仍可能保留旧的补充组，因此它启动的 dispatcher 看得到 queue 路径，却没有权限读取文件。
新开一个 SSH shell 不足以刷新早已存在的 user manager。

在安装 user unit 之前主动刷新它：

~~~bash
WSW_UID="$(id -u wsw)"
sudo systemctl restart "user@$WSW_UID.service"

USER_MANAGER_PID="$(
  sudo systemctl show \
    "user@$WSW_UID.service" \
    --property=MainPID \
    --value
)"

DEPLOY_GID="$(
  getent group minquant-deploy |
    cut -d: -f3
)"

printf 'user_manager_pid=%s\n' "$USER_MANAGER_PID"
printf 'minquant_deploy_gid=%s\n' "$DEPLOY_GID"
sudo awk '/^Groups:/ {print}' "/proc/$USER_MANAGER_PID/status"
~~~

最后一行的 Groups 必须包含打印出的 group GID。这里重启的是 user systemd manager，不是
整台服务器；初装阶段尚未托管 API，因此此时做影响最小。以后如果变更 <code>wsw</code>
补充组，也要先评估并重启其 user manager。

### 3.5 创建 spool 和应用目录

~~~bash
sudo install -d \
  -o root \
  -g minquant-deploy \
  -m 0750 \
  /var/lib/minquant-webhook

sudo install -d \
  -o minquant-webhook \
  -g minquant-deploy \
  -m 0700 \
  /var/lib/minquant-webhook/staging

sudo install -d \
  -o minquant-webhook \
  -g minquant-deploy \
  -m 2750 \
  /var/lib/minquant-webhook/deliveries

sudo install -d \
  -o minquant-webhook \
  -g minquant-deploy \
  -m 2770 \
  /var/lib/minquant-webhook/queue

sudo install -d \
  -o wsw \
  -g minquant-deploy \
  -m 2750 \
  /var/lib/minquant-webhook/results

sudo install -d \
  -o wsw \
  -g wsw \
  -m 0750 \
  /home/wsw/app \
  /home/wsw/app/code \
  /home/wsw/app/dev \
  /home/wsw/app/data \
  /home/wsw/app/data/staging \
  /home/wsw/app/data/processed \
  /home/wsw/app/data/features \
  /home/wsw/app/data/labels \
  /home/wsw/app/data/experiments \
  /home/wsw/app/shared \
  /home/wsw/app/shared/trading \
  /home/wsw/app/shared/trading/logs \
  /home/wsw/app/deploy/trading
~~~

为什么使用 <code>install -d</code> 而不是一串 <code>mkdir</code> 和
<code>chown</code>：它能在同一条命令中创建目录并设置 owner、group、mode，减少目录短暂
处于错误权限的窗口。

mode 中每一位的含义：

| mode | 用途 |
|---|---|
| <code>0750</code> | owner 可读写进入，group 可读进入，其他用户无权限。 |
| <code>0700</code> | staging 只允许接收器访问。 |
| <code>2750</code> | 前面的 2 是 setgid；新文件继承目录 group，避免协作组丢失。 |
| <code>2770</code> | queue 的 owner 和 group 都可写；接收器创建，dispatcher 完成后删除。 |

确认最终边界：

~~~bash
sudo stat -c '%A %U:%G %n' \
  /var/lib/minquant-webhook \
  /var/lib/minquant-webhook/staging \
  /var/lib/minquant-webhook/deliveries \
  /var/lib/minquant-webhook/queue \
  /var/lib/minquant-webhook/results \
  /home/wsw/app/code \
  /home/wsw/app/dev \
  /home/wsw/app/data \
  /home/wsw/app/data/staging \
  /home/wsw/app/data/processed \
  /home/wsw/app/data/features \
  /home/wsw/app/data/labels \
  /home/wsw/app/data/experiments \
  /home/wsw/app/shared/trading \
  /home/wsw/app/deploy/trading
~~~

<code>stat -c</code> 的格式只打印权限、owner:group 和路径，适合与上表逐项比较。

#### 3.5.1 把 raw 单独挂载到 HDD

本节假定 HDD 已经格式化为 ext4；格式化会销毁数据，不属于本指南。先用 UUID 确认 HDD，
不得依赖可能随启动顺序变化的 `/dev/sdX` 名称：

~~~bash
sudo blkid
mountpoint --quiet /home/wsw/cold
findmnt -no SOURCE,FSTYPE,TARGET /home/wsw/cold
~~~

`mountpoint` 必须返回 0，`findmnt` 的 source 与 `blkid` 中计划使用的 HDD UUID 必须对应；否则
停止，不能让后续 `install -d /home/wsw/cold/raw` 在 SSD 上创建一个看似正确的源目录。

`/etc/fstab` 使用实际 HDD UUID，并固定包含以下两个职责不同的 mount：

~~~fstab
UUID=<hdd-uuid>  /home/wsw/cold  ext4  defaults,noatime  0 2
/home/wsw/cold/raw /home/wsw/app/data/raw none bind,x-systemd.requires-mounts-for=/home/wsw/cold/raw 0 0
~~~

第一行挂载 HDD 文件系统，第二行只把 HDD 上的 raw 目录投影到逻辑数据根。创建源目录和
mountpoint 后验证 fstab，再挂载精确目标：

~~~bash
sudo install -d -o wsw -g wsw -m 0750 \
  /home/wsw/cold/raw \
  /home/wsw/app/data/raw

sudo findmnt --verify --verbose
sudo systemctl daemon-reload
sudo mount /home/wsw/app/data/raw

mountpoint --quiet /home/wsw/app/data/raw
findmnt -T /home/wsw/app/data/raw
findmnt -T /home/wsw/app/data/processed

stat -c '%d %n' \
  /home/wsw/app/data/raw \
  /home/wsw/app/data/processed
~~~

<code>mountpoint</code> 必须返回 0；raw 必须显示 HDD source，processed 必须显示 SSD
source，最后两个设备号必须不同。fstab 不得为 HDD 增加 `nofail`；静默跳过 raw mount 会让
数据落入 SSD。API unit、部署 worker 和离线 runner 还会在各自写入边界前执行 fail-closed
mountpoint 检查。人工卸载 raw 前必须先停止 API 和所有 data Job，并在重新挂载与设备身份
验证完成前保持停止。

### 3.6 创建只读 GitHub deploy key

以 <code>wsw</code> 执行：

~~~bash
install -d -m 0700 /home/wsw/.ssh

ssh-keygen \
  -t ed25519 \
  -N '' \
  -C 'minquant-test release deploy' \
  -f /home/wsw/.ssh/trading_release_deploy_key

chmod 0600 /home/wsw/.ssh/trading_release_deploy_key
chmod 0644 /home/wsw/.ssh/trading_release_deploy_key.pub
cat /home/wsw/.ssh/trading_release_deploy_key.pub
~~~

参数原因：

| 参数 | 原因 |
|---|---|
| <code>-t ed25519</code> | 使用现代、短小的 SSH key 类型。 |
| <code>-N ''</code> | 空 passphrase；systemd 无法在无人值守部署时交互解锁。风险由只读仓库权限和 0600 私钥文件约束。 |
| <code>-C</code> | 只写入可识别备注，不参与认证。 |
| <code>-f</code> | 固定私钥路径，避免 systemd 猜测默认 identity。 |
| 私钥 <code>0600</code> | 只有 <code>wsw</code> 可读写；OpenSSH 也会拒绝过宽的私钥权限。 |

在 GitHub 仓库中打开：

~~~text
Settings
  → Deploy keys
  → Add deploy key
~~~

粘贴公钥，名称可写 <code>minquant-test release deploy</code>，**不要勾选 Allow write
access**。测试服务器只需要 fetch；给写权限不会增加部署能力，只会扩大私钥泄漏后的影响。

首次连接时验证 GitHub host key：

~~~bash
ssh \
  -i /home/wsw/.ssh/trading_release_deploy_key \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=ask \
  -T \
  git@github.com
~~~

第一次出现 host fingerprint 时，必须与 GitHub 官方公布的 fingerprint 比对后才输入
<code>yes</code>。参数含义：

- <code>-i</code> 指定 deploy key。
- <code>IdentitiesOnly=yes</code> 禁止 SSH agent 或默认 key 抢先尝试其他身份。
- <code>StrictHostKeyChecking=ask</code> 首次要求人工确认并写入
  <code>known_hosts</code>。
- <code>-T</code> 不申请远端终端；GitHub Git 认证不提供 shell。

GitHub 可能打印“authenticated, but GitHub does not provide shell access”并返回非零，
这对 <code>ssh -T</code> 是正常结果。后续自动部署使用
<code>StrictHostKeyChecking=yes</code>，如果 host key 未预先验证就直接失败，绝不静默接受。

### 3.7 克隆自动部署工作树

从零时 <code>/home/wsw/app/code/trading</code> 应不存在。以 <code>wsw</code> 执行：

~~~bash
GIT_SSH_COMMAND='ssh -i /home/wsw/.ssh/trading_release_deploy_key -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes' \
git clone \
  --branch release/auto-release \
  --single-branch \
  git@github.com:UlricWu/trading.git \
  /home/wsw/app/code/trading

git -C /home/wsw/app/code/trading config \
  --local \
  core.sshCommand \
  'ssh -i /home/wsw/.ssh/trading_release_deploy_key -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes'

GIT_TERMINAL_PROMPT=0 \
git -C /home/wsw/app/code/trading \
  ls-remote origin refs/heads/release/auto-release
~~~

关键参数：

| 参数 | 原因 |
|---|---|
| <code>GIT_SSH_COMMAND=...</code> | clone 前仓库 local config 尚不存在，因此第一次连接必须显式指定 identity。 |
| <code>--branch release/auto-release</code> | 初始 checkout 只指向测试部署源。 |
| <code>--single-branch</code> | 初始 clone 不获取无关分支历史。worker 仍会精确 fetch 目标 ref。 |
| <code>config --local core.sshCommand</code> | 把 SSH 限制只绑定到该仓库，不影响 <code>/home/wsw/app/dev/trading</code> 或其他仓库。 |
| <code>BatchMode=yes</code> | 自动任务需要密码或确认时立即失败，不能无限等待不可见提示。 |
| <code>ConnectTimeout=10</code> | 网络不可达时十秒内失败，避免 worker 长期卡在 SSH 建连。 |
| <code>StrictHostKeyChecking=yes</code> | 只信任上一步人工确认过的 host key。 |
| <code>GIT_TERMINAL_PROMPT=0</code> | 验证无人值守 fetch 不依赖用户名或密码提示。 |
| <code>ls-remote</code> | 只测试远端读取权限，不修改工作树。 |

输出必须是一条 40 位 SHA 和
<code>refs/heads/release/auto-release</code>。私钥内容永远不要输出。

自动部署最终会让 <code>code/trading</code> 处于 detached HEAD。这是刻意设计：它表示目录
绑定一个 commit，而不是让人误以为可以在服务器上提交或合并该分支。

### 3.8 写入共享测试配置

创建只有 <code>wsw</code> 可读的文件：

~~~bash
if ! sudo test -e /home/wsw/app/shared/trading/.env.test; then
  sudo install \
    -o wsw \
    -g wsw \
    -m 0600 \
    /dev/null \
    /home/wsw/app/shared/trading/.env.test
fi

sudoedit /home/wsw/app/shared/trading/.env.test
~~~

内容结构如下，等号右侧替换为真实值：

~~~dotenv
FTP_HOST=replace-me
FTP_PORT=21
FTP_USER=replace-me
FTP_PASSWORD=replace-me
TUSHARE_TOKEN=replace-me
TUSHARE_GATEWAY=
~~~

#### 3.8.1 真实文件与部署工作树符号链接

<code>/home/wsw/app/shared/trading/.env.test</code> 是测试环境凭证的唯一真实文件，是
<code>0600</code> 的普通文件，不是链接。自动部署完成后，代码工作树中的同名路径是指向它的
绝对符号链接：

~~~text
/home/wsw/app/code/trading/.env.test
  -> /home/wsw/app/shared/trading/.env.test
~~~

worker 在 detached checkout、hard reset 和 <code>git clean</code> 完成后执行等价于以下内容
的命令；这是创建引用，不是复制文件：

~~~bash
ln -sfn \
  /home/wsw/app/shared/trading/.env.test \
  /home/wsw/app/code/trading/.env.test
~~~

应用仍按配置契约打开项目根目录的 <code>.env.test</code>。操作系统跟随符号链接后读取 shared
中的真实文件，因此不存在两份需要同步的凭证。通过链接路径原地修改文件内容也会修改 shared
中的同一文件；但删除链接只会删除链接本身，删除 shared 中的真实文件则会留下断链。某些通过
“创建新文件再 rename 覆盖”实现保存的工具还可能把链接替换成普通文件，造成配置漂移。因此
运维人员必须只编辑 <code>/home/wsw/app/shared/trading/.env.test</code>，不得在
<code>/home/wsw/app/code/trading</code> 中人工创建或替换 <code>.env.test</code>。

可以在不打印任何 Secret 的情况下验证当前关系：

~~~bash
test -L /home/wsw/app/code/trading/.env.test
readlink /home/wsw/app/code/trading/.env.test
test -f /home/wsw/app/shared/trading/.env.test
~~~

采用这一结构的原因是：

- 精确 SHA 部署会重置和清理 <code>/home/wsw/app/code/trading</code>，运行凭证不能依赖该
  工作树的生命周期；
- Secret 不能进入 commit，shared 路径允许凭证独立于 Git 管理；
- 同一份测试配置必须跨 release SHA 保留，不能在每次部署时复制并产生多个可能漂移的副本；
- 应用继续使用稳定的项目根目录路径，无需感知服务器的部署目录划分。

<code>logs</code> 使用相同结构：真实目录位于
<code>/home/wsw/app/shared/trading/logs</code>，代码工作树中的 <code>logs</code> 是指向它的
目录符号链接，使日志跨 checkout 保留。

<code>ENV=test</code> 由 systemd unit 注入，<code>ZERO_STORAGE_ROOT</code> 也由 unit 注入；
它们不是 Secret，不需要在该文件中重复定义。空的 <code>TUSHARE_GATEWAY</code> 表示使用默认
网关。不要用 <code>cat</code>、<code>env</code> 或 journal 打印真实配置内容。

前面的存在性判断很重要：<code>install /dev/null destination</code> 在目标已存在时会截断
文件。首次创建可以这样做，重跑指南时不能覆盖已有凭证。

### 3.9 安装控制面程序和 systemd unit

控制面源文件保存在仓库，运行副本由 root 固定安装。先确认当前 checkout 是已经审查并通过
CI 的 release SHA：

~~~bash
git -C /home/wsw/app/code/trading status --short --branch
git -C /home/wsw/app/code/trading rev-parse HEAD
git -C /home/wsw/app/code/trading log -1 --oneline
~~~

安装三个程序：

~~~bash
sudo install -d \
  -o root \
  -g root \
  -m 0755 \
  /usr/local/libexec

sudo install \
  -o root \
  -g root \
  -m 0755 \
  /home/wsw/app/code/trading/scripts/github_webhook_receiver.py \
  /usr/local/libexec/minquant-webhook-receiver

sudo install \
  -o root \
  -g root \
  -m 0755 \
  /home/wsw/app/code/trading/scripts/deploy_dispatcher.py \
  /usr/local/libexec/minquant-deploy-dispatcher

sudo install \
  -o root \
  -g root \
  -m 0755 \
  /home/wsw/app/code/trading/scripts/deploy_release.sh \
  /usr/local/libexec/minquant-deploy
~~~

安装 system service：

~~~bash
sudo install \
  -o root \
  -g root \
  -m 0644 \
  /home/wsw/app/code/trading/deploy/systemd/system/minquant-webhook.service \
  /etc/systemd/system/minquant-webhook.service
~~~

安装 <code>wsw</code> user units：

~~~bash
install -d -m 0755 /home/wsw/.config/systemd/user

install -m 0644 \
  /home/wsw/app/code/trading/deploy/systemd/user/minquant-api.service \
  /home/wsw/.config/systemd/user/minquant-api.service

install -m 0644 \
  /home/wsw/app/code/trading/deploy/systemd/user/minquant-deploy.path \
  /home/wsw/.config/systemd/user/minquant-deploy.path

install -m 0644 \
  /home/wsw/app/code/trading/deploy/systemd/user/minquant-deploy.service \
  /home/wsw/.config/systemd/user/minquant-deploy.service

sudo systemctl daemon-reload
systemctl --user daemon-reload
~~~

这里 <code>0755</code> 用于可执行程序，<code>0644</code> 用于不可执行的 unit 文本。
<code>daemon-reload</code> 只让 systemd 重新读取 unit 定义，不启动服务。

#### 为什么控制面不能随应用 Webhook 自更新

如果接收器或 worker 可以从一个尚在处理的 commit 覆盖自身，那么“谁验证这个 commit”
会形成循环信任。root 安装副本切断了这个循环：

~~~text
仓库文件 = 待审查的安装源
/usr/local/libexec = 当前实际运行、root 拥有的控制面
~~~

所以普通应用 SHA 自动部署只更新 <code>/home/wsw/app/code/trading</code>。控制面变化必须人工执行
本节的复制、<code>daemon-reload</code> 和相应服务重启。

#### systemd 关键参数为什么存在

Webhook system service：

| unit 参数 | 作用 |
|---|---|
| <code>User=minquant-webhook</code>、<code>Group=minquant-deploy</code> | 用低权限身份运行，只通过协作组进入 spool。 |
| <code>ExecStart=/usr/bin/python3 ...</code> | 接收器只用标准库，不依赖应用 venv 或当前 shell。 |
| <code>LoadCredential=...</code> | systemd 把 root Secret 以只读 credential 交给进程，无需放宽源文件权限。 |
| <code>Restart=on-failure</code> | 异常退出时重启，正常 stop 不反复拉起。 |
| <code>NoNewPrivileges=yes</code> | 进程及子进程不能通过 setuid 等方式提升权限。 |
| <code>ProtectSystem=strict</code> | 默认把系统文件树设为只读。 |
| <code>ReadWritePaths=/var/lib/minquant-webhook</code> | 在严格只读基础上，只开放 spool 写入。 |
| <code>ProtectHome=yes</code> | 接收器看不到 <code>/home/wsw/app</code> 和 SSH 私钥。 |
| <code>PrivateTmp</code>、<code>PrivateDevices</code> 等 | 减少公网进程能看到的临时文件、设备和内核控制面。 |

三个 user units：

| unit | 关键参数与原因 |
|---|---|
| <code>minquant-deploy.path</code> | <code>DirectoryNotEmpty</code> 让 queue 非空时触发 worker；不用轮询或 cron。 |
| <code>minquant-deploy.service</code> | <code>Type=oneshot</code> 表示每次排空 queue 后退出；<code>TimeoutStartSec=infinity</code> 避免大依赖同步被 systemd 默认启动超时中断。 |
| <code>minquant-api.service</code> | 固定 WorkingDirectory、环境、数据路径和项目 venv；<code>ExecStartPre=/usr/bin/mountpoint --quiet /home/wsw/app/data/raw</code> 在 raw 未挂载时拒绝启动；<code>Restart=on-failure</code> 负责异常退出恢复。 |

### 3.10 首次 bootstrap 部署

此时 API 的项目 <code>.venv</code> 尚不存在，所以不能先启用 API。第一次直接调用已经安装的
worker，让它建立和后续 Webhook 完全相同的运行状态：

~~~bash
git -C /home/wsw/app/code/trading fetch \
  --no-tags \
  origin \
  '+refs/heads/release/auto-release:refs/remotes/origin/release/auto-release'

DEPLOY_SHA="$(
  git -C /home/wsw/app/code/trading \
    rev-parse refs/remotes/origin/release/auto-release
)"

RUN_ID="bootstrap-$(printf '%s' "$DEPLOY_SHA" | cut -c1-7)"

env \
  -u CONDA_PREFIX \
  -u VIRTUAL_ENV \
  RUN_ID="$RUN_ID" \
  DEPLOY_SHA="$DEPLOY_SHA" \
  /usr/local/libexec/minquant-deploy
~~~

参数和顺序：

- <code>--no-tags</code> 不下载部署不使用的 tag。
- 完整 refspec 前的 <code>+</code> 允许刷新机器镜像分支的 remote-tracking ref；该分支本来
  就由 workflow 使用 force-with-lease 镜像。
- <code>DEPLOY_SHA</code> 取 fetch 后 remote-tracking ref 的完整 40 位 SHA，不能使用短 SHA。
- <code>RUN_ID</code> 只作为可追踪且符合 allowlist 的人工 bootstrap 标识。
- 再次清理 Conda/venv 标记，保证手工 bootstrap 与 systemd 非交互环境一致。

worker 内部依次做以下事情：

1. 验证 <code>RUN_ID</code> 和 <code>DEPLOY_SHA</code> 格式；
2. 验证 <code>/home/wsw/app/data/raw</code> 是 mountpoint，否则在 fetch 或停止 API 前失败；
3. 使用 uv 找到已安装的 Python 3.13；
4. 用 <code>flock -w 600</code> 最多等待部署锁十分钟；
5. fetch release branch，并要求远端 tip 完全等于 <code>DEPLOY_SHA</code>；
6. 停止 API，detached checkout、hard reset，并清理非 ignored untracked 文件；
7. 为 <code>.env.test</code> 和 <code>logs</code> 重新创建指向 shared 的绝对符号链接；
8. 校验锁文件并同步依赖；
9. 再 fetch 一次，防止依赖安装期间远端已经前进；
10. 写入 API commit identity、启动服务；
11. 最多检查健康 30 次、间隔 2 秒，并要求连续成功 2 次；
12. 原子写入 <code>current-test-release</code> 后才返回成功。

uv 命令的参数含义：

| 参数 | 原因 |
|---|---|
| <code>uv lock --check</code> | 只验证 <code>pyproject.toml</code> 与 <code>uv.lock</code> 一致，不在服务器改锁。 |
| <code>uv sync --locked</code> | 必须严格使用现有锁文件；不能在部署机重新解析版本。 |
| <code>--no-dev</code> | 测试运行服务不安装 pytest 等开发依赖。 |
| <code>--no-install-project</code> | 项目通过 WorkingDirectory 与 PYTHONPATH 运行，不额外把自身打包安装。 |
| <code>--python</code> | 明确使用前面验证过的 3.13 解释器。 |
| <code>--no-python-downloads</code> | 部署时缺解释器就失败，不临时改变服务器运行时。 |

为什么检查两次 remote tip：第一次阻止已经过期的 delivery；第二次处理“依赖同步的几分钟内
又出现新 release”这个竞态。旧 worker 不会在新版本已经发布后启动旧 API。

为什么健康要连续成功两次：一次成功可能只是服务刚启动的瞬态响应；两次相邻成功以很小成本
过滤这个瞬态。健康 JSON 还必须精确等于目标环境、release ref 和 commit SHA，不能只看
HTTP 200。

bootstrap 成功后：

~~~bash
ENV=test \
ZERO_STORAGE_ROOT=/home/wsw/app/data \
PYTHONPATH=/home/wsw/app/code/trading \
/home/wsw/app/code/trading/.venv/bin/python -c '
import sys

from src.config.app_config import AppConfig

try:
    AppConfig.load()
except Exception as error:
    print(
        "configuration validation failed: "
        + type(error).__name__,
        file=sys.stderr,
    )
    raise SystemExit(1) from None
print("configuration schema valid")
'

systemctl --user enable minquant-api.service

cat /home/wsw/app/deploy/trading/current-test-release
curl -fsS http://127.0.0.1:5050/health
printf '\n'
git -C /home/wsw/app/code/trading status --short --branch
~~~

预期工作树显示 <code>HEAD (no branch)</code>，健康返回目标完整 SHA。启用 API 是为了开机
恢复；worker 已经启动了它，所以这里只需 <code>enable</code>，不必重复 restart。

前面的 Python 命令只验证 <code>.env.test</code> 能被当前配置 schema 安全加载，不打印
字段值，也不连接 FTP 或 Tushare。它是首次凭证配置检查，不属于每次部署的 health 成功
语义；health 有意只证明 API 进程和 release identity。

#### 3.10.1 旧 raw Meta 的数据任务 Gate

API health 不读取 raw Meta，因此 bootstrap 成功不能证明旧数据能被当前代码消费。如果
`/home/wsw/app/data/raw` 已包含历史 `meta.json`，在安装离线 cron 或人工提交数据 Job 前必须
先执行只读预检：

~~~bash
cd /home/wsw/app/code/trading

/home/wsw/app/code/trading/.venv/bin/python -m scripts.migrate_raw_meta \
  --storage-root /home/wsw/app/data
~~~

结果按以下状态处理：

| 摘要 | 允许动作 |
|---|---|
| <code>migratable=0 blocked=0</code> | Meta Gate 通过，可以继续数据任务验收。 |
| <code>migratable&gt;0 blocked=0</code> | 只说明具备迁移条件；停止 API 和所有 raw producer、确认回滚版本兼容后，才能在独立维护动作中使用 <code>--apply</code>。 |
| <code>blocked&gt;0</code> | 不得 apply，不得启用 cron 或提交数据 Job；逐项调查 blocked 对象，不能删除 raw payload 来制造通过。 |

执行 apply 后必须再次运行不带 <code>--apply</code> 的命令，并得到
<code>migratable=0 blocked=0</code>；只有最终复检才证明 Meta Gate 通过。该 Gate 不属于普通
release 部署，worker 不自动改写 Meta。

### 3.11 创建 Webhook Secret

~~~bash
sudo install -d \
  -o root \
  -g root \
  -m 0700 \
  /etc/minquant-webhook

if ! sudo test -e /etc/minquant-webhook/github-webhook-secret; then
  sudo /bin/sh -c '
    umask 077
    openssl rand -hex 32 > /etc/minquant-webhook/github-webhook-secret
  '
fi

sudo stat -c '%A %U:%G %n' \
  /etc/minquant-webhook/github-webhook-secret
~~~

为什么是 <code>openssl rand -hex 32</code>：

- <code>rand</code> 使用密码学安全随机源；
- <code>32</code> 表示 32 bytes，也就是 256 bits；
- <code>-hex</code> 输出 64 个易复制的 ASCII 字符；
- HMAC-SHA256 不要求 Secret 恰好 32 bytes，但这个长度有足够熵且便于管理。

<code>umask 077</code> 保证新文件从创建时就只有 owner 可访问。文件 owner 是 root；
<code>minquant-webhook</code> 不直接读取原文件，而是由 systemd
<code>LoadCredential</code> 交付运行时只读副本。

存在性判断防止重跑文档时静默生成新 Secret。直接覆盖会让服务器与 GitHub 保存的 Secret
立刻不一致，使所有真实 delivery 返回 401。Secret 轮换必须同时更新 GitHub 配置并重启
接收器，应作为单独维护动作执行。

需要粘贴到 GitHub 时，用 root shell 读取并去掉换行：

~~~bash
sudo /bin/sh -c '
  tr -d "\r\n" </etc/minquant-webhook/github-webhook-secret
  printf "\n"
'
~~~

不要写成：

~~~bash
sudo tr -d '\r\n' </etc/minquant-webhook/github-webhook-secret
~~~

因为输入重定向由当前 shell 在执行 <code>sudo</code> **之前**打开，普通 <code>wsw</code>
无权读文件，所以会得到 permission denied。把重定向放进
<code>sudo /bin/sh -c</code> 才真正由 root 打开。

### 3.12 启动接收器和 dispatcher

~~~bash
sudo systemctl enable --now minquant-webhook.service
systemctl --user enable --now minquant-deploy.path

sudo systemctl is-enabled minquant-webhook.service
sudo systemctl is-active minquant-webhook.service
systemctl --user is-enabled minquant-deploy.path
systemctl --user is-active minquant-deploy.path

sudo ss -ltnp 'sport = :9000'
~~~

参数含义：

| 参数 | 原因 |
|---|---|
| <code>enable</code> | 建立开机启动关系。 |
| <code>--now</code> | 在启用的同时立即启动，不等待重启。 |
| <code>is-enabled</code> | 检查开机策略。 |
| <code>is-active</code> | 检查此刻是否正在运行；不能用 enabled 替代 active。 |
| <code>sport = :9000</code> | 只筛选本地监听源端口 9000。 |

监听地址必须是 <code>127.0.0.1:9000</code>，不能是 <code>0.0.0.0:9000</code>。公网只能
通过 Funnel 到达它。

### 3.13 本机签名、去重和异步部署测试

先在不开 Funnel、不配置 GitHub 的情况下验证完整本机链路。以下测试读取 root Secret 计算
HMAC，但不会打印 Secret：

~~~bash
DEPLOY_SHA="$(
  git -C /home/wsw/app/code/trading \
    rev-parse refs/remotes/origin/release/auto-release
)"

DELIVERY_ID="local-test-$(date +%Y%m%d%H%M%S)"

PAYLOAD="$(
  printf \
    '{"repository":{"full_name":"UlricWu/trading"},"ref":"refs/heads/release/auto-release","after":"%s"}' \
    "$DEPLOY_SHA"
)"

SIGNATURE="$(
  printf '%s' "$PAYLOAD" |
    sudo /usr/bin/python3 -c '
import hashlib
import hmac
import sys
from pathlib import Path

body = sys.stdin.buffer.read()
secret = Path(
    "/etc/minquant-webhook/github-webhook-secret"
).read_bytes().rstrip(b"\r\n")
print("sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest())
'
)"

for ATTEMPT in 1 2; do
  curl \
    --silent \
    --show-error \
    --request POST \
    --header 'Content-Type: application/json' \
    --header 'X-GitHub-Event: push' \
    --header "X-GitHub-Delivery: $DELIVERY_ID" \
    --header "X-Hub-Signature-256: $SIGNATURE" \
    --data-binary "$PAYLOAD" \
    --write-out "\nattempt=$ATTEMPT status=%{http_code}\n" \
    http://127.0.0.1:9000/github/webhook
done
~~~

预期：

~~~text
accepted
attempt=1 status=202
duplicate
attempt=2 status=200
~~~

为什么同一请求发送两次：第一次证明合法请求能够持久化并异步接纳；第二次证明同一个
<code>X-GitHub-Delivery</code> 不会创建第二个部署任务。

curl 参数：

| 参数 | 原因 |
|---|---|
| <code>--silent --show-error</code> | 不显示进度条，但网络错误仍可见。 |
| <code>--request POST</code> | Webhook 只接受 POST。 |
| <code>--header</code> | 模拟 GitHub 的 event、delivery 和 HMAC headers。 |
| <code>--data-binary</code> | 原样发送与计算 HMAC 时完全相同的 bytes。 |
| <code>--write-out</code> | 同时显示 HTTP status，避免只凭 body 判断。 |

等待异步 result，避免在失败时用 <code>exit</code> 关闭当前 SSH：

~~~bash
RESULT_FILE="/var/lib/minquant-webhook/results/$DELIVERY_ID.json"

for ATTEMPT in $(seq 1 60); do
  if test -f "$RESULT_FILE"; then
    break
  fi
  sleep 1
done

if test -f "$RESULT_FILE"; then
  cat "$RESULT_FILE"
else
  printf 'ERROR: deployment result was not created\n'
fi
~~~

预期 result 的 <code>status</code> 为 <code>succeeded</code>、exit code 为 0、commit
等于 <code>DEPLOY_SHA</code>。因为 bootstrap 后 API 已经健康，worker 通常走幂等路径，
不会无意义地重启服务。

同时检查：

~~~bash
sudo find /var/lib/minquant-webhook/deliveries \
  -maxdepth 1 \
  -type f \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %f\n'

sudo find /var/lib/minquant-webhook/queue \
  -maxdepth 1 \
  -type f \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %f\n'

sudo find /var/lib/minquant-webhook/results \
  -maxdepth 1 \
  -type f \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %f\n'
~~~

成功后 delivery 与 result 保留，queue 为空。当前没有自动保留期限，不要自行添加删除 cron。

### 3.14 开启 Tailscale Funnel

只有本机测试通过后才执行：

~~~bash
sudo tailscale funnel \
  --bg \
  --https=443 \
  http://127.0.0.1:9000

sudo tailscale funnel status
sudo tailscale serve status --json
~~~

参数原因：

| 参数 | 原因 |
|---|---|
| <code>funnel</code> | 明确允许互联网访问；普通 <code>serve</code> 只面向 tailnet。 |
| <code>--bg</code> | 把配置交给 tailscaled 持久管理，命令退出后代理仍存在。 |
| <code>--https=443</code> | 使用标准公网 HTTPS 端口并由 Tailscale 处理证书。 |
| <code>http://127.0.0.1:9000</code> | Funnel 到本机接收器使用回环 HTTP；外部一侧仍是 HTTPS。 |

预期类似：

~~~text
https://minquant-test.tailefd506.ts.net (Funnel on)
|-- / proxy http://127.0.0.1:9000
~~~

根路径代理不表示所有 URL 都有效。接收器本身只接受
<code>POST /github/webhook</code>，其他 path 返回 404。不要添加
<code>127.0.0.1:5050</code> 映射；测试推理 API 不属于公网入口。

### 3.15 验证公网 DNS 和 TLS

Funnel 首次开启后，公开 DNS 最长可能需要数分钟传播。先得到节点完整 DNS 名：

~~~bash
FUNNEL_HOST="$(
  sudo tailscale status --json |
    /usr/bin/python3 -c '
import json
import sys

print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))
'
)"

FUNNEL_URL="https://$FUNNEL_HOST/github/webhook"

printf 'funnel_host=%s\n' "$FUNNEL_HOST"
printf 'webhook_url=%s\n' "$FUNNEL_URL"
~~~

使用公共 DoH，而不是只看本机 MagicDNS：

~~~bash
curl -sS \
  "https://cloudflare-dns.com/dns-query?name=$FUNNEL_HOST&type=A" \
  -H 'accept: application/dns-json' |
  /usr/bin/python3 -m json.tool
~~~

<code>Status: 0</code> 且 Answer 中出现公开 A 记录才表示公共 DNS 已生效。

本机和 Mac 上曾出现过两类容易误判的地址：

- <code>100.x.y.z</code>：Tailscale 内网地址，表示 MagicDNS 把本机域名解析回节点；
- <code>198.18.x.y</code>：代理软件常用的 fake-IP，不是 Funnel 公网 edge。

这两种地址上的 TLS 超时不能单独证明 Funnel 故障。可以在不连接 tailnet 的网络测试，或者
临时从 DoH 取一个公网 A 记录并用 curl 的 <code>--resolve</code> 绕过本地 DNS：

~~~bash
PUBLIC_IP="$(
  curl -sS \
    "https://cloudflare-dns.com/dns-query?name=$FUNNEL_HOST&type=A" \
    -H 'accept: application/dns-json' |
    /usr/bin/python3 -c '
import json
import sys

payload = json.load(sys.stdin)
answers = payload.get("Answer", [])
addresses = [
    item["data"]
    for item in answers
    if item.get("type") == 1
]
if not addresses:
    raise SystemExit("public A record not ready")
print(addresses[0])
'
)"

curl \
  --noproxy '*' \
  --resolve "$FUNNEL_HOST:443:$PUBLIC_IP" \
  --silent \
  --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'X-GitHub-Event: push' \
  --header 'X-GitHub-Delivery: unsigned-funnel-test' \
  --data '{}' \
  --connect-timeout 10 \
  --max-time 20 \
  --write-out "\nremote_ip=%{remote_ip}\nhttp_status=%{http_code}\n" \
  "$FUNNEL_URL"
~~~

预期 body 为 <code>invalid signature</code>、status 为 401。这是成功的安全测试：公网 DNS、
TLS、Funnel 和接收器都已到达，伪造请求被 HMAC 边界拒绝。

新增参数：

| 参数 | 原因 |
|---|---|
| <code>--noproxy '*'</code> | 本次诊断不经过 HTTP 代理。 |
| <code>--resolve host:443:IP</code> | 只为本次 curl 指定公网 edge IP，同时保留正确的 TLS SNI 和 Host；不修改系统 DNS。 |
| <code>--connect-timeout 10</code> | TCP/TLS 建连十秒未完成就失败。 |
| <code>--max-time 20</code> | 整个请求最多二十秒，避免诊断命令挂住。 |

不要把 DoH 返回的 edge IP 写入 <code>/etc/hosts</code>；它不是长期固定配置。

### 3.16 在 GitHub 创建 Webhook

进入仓库：

~~~text
UlricWu/trading
  → Settings
  → Webhooks
  → Add webhook
~~~

填写：

| GitHub 字段 | 值 | 为什么 |
|---|---|---|
| Payload URL | 第 3.15 节打印的 <code>webhook_url</code> 完整值 | 使用当前节点实际 <code>FUNNEL_HOST</code> 与接收器唯一 path，不写死某个 tailnet 域名。 |
| Content type | <code>application/json</code> | 接收器按 JSON object 解析原始 body。 |
| Secret | root Secret 文件的完整单行内容 | GitHub 用它生成 <code>X-Hub-Signature-256</code>。 |
| SSL verification | Enable SSL verification | 必须验证 Tailscale 证书，不能降低为不验证。 |
| Events | Just the push event | 只需要 push；不订阅 PR、issue 等无关事件。 |
| Active | 勾选 | 开始投递。 |

GitHub Webhook 不能在 UI 中按 branch 过滤，所以仓库所有 push 都会送来：

- <code>release/auto-release</code> 的合法 push：202 accepted；
- <code>dev</code> 或其他分支 push：422 unexpected deployment source，不入队；
- GitHub 创建 Webhook 时的 <code>ping</code>：422 unsupported event，不入队。

因此 GitHub Recent deliveries 中看到 ping 或 dev delivery 的红色 422 是当前严格契约的预期
结果。一个已签名 ping 返回 <code>unsupported event</code> 还同时证明 URL、TLS、Secret
校验都已通过，失败点只是 event allowlist。

### 3.17 真实端到端验收

不要人工 push <code>release/auto-release</code>。使用正式分支流：

~~~text
feature/* PR 合入 dev
  → dev CI
  → auto-release workflow CI
  → workflow 镜像 release/auto-release
  → GitHub push Webhook
  → Ubuntu 自动部署
~~~

在 Ubuntu 观察：

~~~bash
sudo journalctl \
  -u minquant-webhook.service \
  --since '10 minutes ago' \
  --no-pager

journalctl \
  --user \
  -u minquant-deploy.service \
  --since '10 minutes ago' \
  --no-pager

journalctl \
  --user \
  -u minquant-api.service \
  --since '10 minutes ago' \
  --no-pager
~~~

最终验收：

~~~bash
cat /home/wsw/app/deploy/trading/current-test-release

curl -fsS http://127.0.0.1:5050/health
printf '\n'

git -C /home/wsw/app/code/trading rev-parse HEAD
git -C /home/wsw/app/code/trading status --short --branch

systemctl --user is-active minquant-api.service
systemctl --user is-active minquant-deploy.path
sudo systemctl is-active minquant-webhook.service

sudo find /var/lib/minquant-webhook/queue \
  -maxdepth 1 \
  -type f \
  -printf '%f\n'

sudo find /var/lib/minquant-webhook/results \
  -maxdepth 1 \
  -type f \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %f\n'
~~~

一次真实发布只有同时满足以下条件才算完成：

1. GitHub release branch 已更新；
2. 对应 delivery result 是 <code>succeeded</code>；
3. <code>current-test-release</code> 的 SHA 等于目标 SHA；
4. Git HEAD 等于目标 SHA，且 tracked 工作树干净；
5. health 精确报告 test、release branch 和目标 SHA；
6. API、path、receiver 都 active；
7. queue 已排空。

其中任意一个结果不能替代其他结果。Webhook 的 202 只代表“任务已可靠接纳”，不代表部署
已经成功。这七项只验收发布链路；使用已有 raw 的数据任务还必须单独通过 3.10.1 的 Meta
Gate。

## 4. 部署 worker 的安全语义

### 4.1 为什么不能直接 git pull

<code>git pull</code> 等于 fetch 加 merge/rebase，它依赖当前 branch、配置和本地修改。
自动目录需要的是“精确等于一个 commit”，所以 worker 使用：

| Git 动作 | 原因 |
|---|---|
| 精确 fetch refspec | 只更新 release remote-tracking ref。 |
| tip 等于 Webhook SHA | 拒绝旧消息与乱序消息。 |
| <code>checkout --detach --force SHA</code> | 不依赖本地 branch 指针，直接绑定目标 commit。 |
| <code>reset --hard SHA</code> | tracked 文件精确等于 commit。 |
| <code>clean -fd</code> | 删除非 ignored 的人工残留；因此 <code>code/trading</code> 禁止人工修改。 |

<code>.env.test</code> 和 <code>logs</code> 已在 <code>.gitignore</code> 中，worker 又会为它们
重新创建指向 shared 的符号链接，所以运行配置和日志不随 checkout 丢失。链接只引用 shared
中的真实文件或目录，不会复制内容；具体读写语义见 3.8.1。

### 4.2 为什么 Webhook 接收器不直接执行 worker

一次依赖同步和健康检查可能持续数分钟。如果 HTTP handler 同步等待：

- GitHub 容易超时并重试；
- 接收器崩溃会同时丢失请求和执行状态；
- 一个慢部署会阻塞后续 HTTP 请求。

现在的顺序是：

~~~text
验证 → fsync delivery → 创建 queue marker → 返回 202
                                      │
                                      └→ systemd 异步执行
~~~

先持久化再返回意味着：只要 GitHub 收到 202，服务器磁盘上就已经存在接管依据。

### 4.3 为什么既有 delivery，又有 queue 和 result

| 文件 | 表达的事实 |
|---|---|
| <code>deliveries/ID.json</code> | 这个唯一 delivery 已通过接收边界；用于去重。 |
| <code>queue/ID.json</code> | 它尚未形成终态结果；崩溃后应重试。 |
| <code>results/ID.json</code> | 它已经以 succeeded 或 failed 结束。 |

只用一个文件无法同时表达“永远去重”和“崩溃后待重试”。result 先原子写入，queue 后删除，
避免出现任务已经完成却没有任何终态证据的窗口。

### 4.4 为什么 payload 不能拼进 shell

公网 JSON 是不可信输入。接收器只提取并严格校验：

- 固定仓库 <code>UlricWu/trading</code>；
- 固定 ref <code>refs/heads/release/auto-release</code>；
- 40 位小写十六进制 SHA；
- 1 到 128 位安全字符 delivery ID。

dispatcher 使用参数数组调用固定路径，并只注入重新校验后的 <code>RUN_ID</code> 和
<code>DEPLOY_SHA</code>。它不读取 payload 中的命令、路径或参数字符串，因此 shell 注入
没有入口。

### 4.5 HTTPS 和 HMAC 为什么都需要

两者保护的对象不同：

| 层 | 证明什么 |
|---|---|
| HTTPS/TLS | GitHub 连接到的是该 <code>*.ts.net</code> 服务，传输过程被加密。 |
| HMAC-SHA256 | 收到的 exact body 是持有共享 Secret 的发送方生成的，且 body 没被改动。 |

接收器必须对**原始 request bytes**计算 HMAC，不能先解析 JSON 再序列化。JSON 中空格、
字段顺序或转义形式改变后，业务对象可能相同，但 bytes 已经不同，GitHub 签名也就不同。
Header 的形式是：

~~~text
X-Hub-Signature-256: sha256=<64 个十六进制字符>
~~~

接收器用常量时间比较，而不是普通字符串提前退出比较，减少通过响应时间猜测签名的侧信道。
TLS 不能取代 HMAC，因为 Funnel 的公网 URL 对任何互联网客户端都可达；HMAC 才是应用层的
发送方授权。

## 5. 日常使用

### 5.1 从 Mac 开发

正常流程：

~~~bash
git push origin feature/your-change
~~~

在 GitHub 创建 PR 合入 <code>dev</code>。不要人工操作 Ubuntu 的
<code>/home/wsw/app/code/trading</code>。

### 5.2 可选的 Ubuntu 开发目录

如果要在训练服务器直接开发，另建完全独立的工作树：

~~~bash
git clone git@github.com:UlricWu/trading.git /home/wsw/app/dev/trading
git -C /home/wsw/app/dev/trading switch dev
~~~

该目录需要开发者自己的可写 GitHub 身份，**不要复用只读 deploy key 并给它增加写权限**。
它可以切换 feature branch、存在未提交修改。Mac 推送 dev 后需要同步时：

~~~bash
git -C /home/wsw/app/dev/trading pull --ff-only origin dev
~~~

<code>--ff-only</code> 只允许快进，不在服务器意外创建 merge commit。如果不在 Ubuntu 开发，
<code>dev/trading</code> 可以不创建；它不是部署依赖。

### 5.3 查看当前部署身份

~~~bash
cat /home/wsw/app/deploy/trading/current-test-release
curl -fsS http://127.0.0.1:5050/health
printf '\n'
git -C /home/wsw/app/code/trading rev-parse HEAD
~~~

三处 SHA 应一致：

~~~text
current-test-release commit_sha
= /health commit_sha
= git rev-parse HEAD
~~~

### 5.4 查看一次 delivery

在 GitHub Recent deliveries 找到 delivery GUID，然后：

~~~bash
DELIVERY_ID='replace-with-github-delivery-id'

sudo cat \
  "/var/lib/minquant-webhook/deliveries/$DELIVERY_ID.json"

if test -f "/var/lib/minquant-webhook/queue/$DELIVERY_ID.json"; then
  printf 'state=queued\n'
fi

if test -f "/var/lib/minquant-webhook/results/$DELIVERY_ID.json"; then
  cat "/var/lib/minquant-webhook/results/$DELIVERY_ID.json"
fi
~~~

delivery 文件只保存校验后的最小字段，不保存整个 GitHub payload。

### 5.5 控制面更新

当 release 中的以下文件改变时，应用部署不会自动安装它们：

~~~text
scripts/github_webhook_receiver.py
scripts/deploy_dispatcher.py
scripts/deploy_release.sh
deploy/systemd/**
~~~

先确认 queue 为空、当前 SHA 是已审查版本，再重复 3.9 的 <code>install</code> 命令，然后：

~~~bash
sudo systemctl daemon-reload
systemctl --user daemon-reload

sudo systemctl restart minquant-webhook.service
systemctl --user restart minquant-deploy.path
~~~

如果 API unit 本身发生变化，再单独：

~~~bash
systemctl --user restart minquant-api.service
curl -fsS http://127.0.0.1:5050/health
printf '\n'
~~~

这一步是人工控制面维护，不是一次普通应用发布。不要在 queue 正执行时替换 worker。

## 6. 排障：从哪一层开始

始终按数据流从前到后检查，不要一看到 GitHub 红色 delivery 就重装全部组件。

### 6.1 快速状态总览

~~~bash
sudo tailscale status
sudo tailscale funnel status

sudo systemctl status \
  minquant-webhook.service \
  --no-pager

systemctl --user status \
  minquant-deploy.path \
  minquant-deploy.service \
  minquant-api.service \
  --no-pager

sudo ss -ltnp 'sport = :9000'
sudo ss -ltnp 'sport = :5050'
~~~

### 6.2 HTTP 状态码

| 状态 | body | 解释与动作 |
|---|---|---|
| 404 | not found | method 或 path 不是 <code>POST /github/webhook</code>。 |
| 401 | invalid signature | 请求已到接收器，但 Secret/HMAC/body 不一致；检查 GitHub Secret 是否和 root 文件完全相同。 |
| 422 | unsupported event | 签名正确，但 event 不是 push；GitHub ping 的预期结果。 |
| 422 | unexpected deployment source | 签名正确，但 repo/ref 不是指定 release branch；dev push 的预期结果。 |
| 422 | invalid delivery id / commit sha | GitHub headers 或 payload 不符合严格格式。 |
| 202 | accepted | 新 delivery 已持久化并入队；尚不代表部署成功。 |
| 200 | duplicate | 同一 delivery 已接纳过；不会重复部署。 |
| 500 | internal error | 接收器文件权限或磁盘操作失败；查看 system journal。 |

### 6.3 202 accepted，但没有 result

先看 queue 和 user service：

~~~bash
sudo find /var/lib/minquant-webhook/queue \
  -maxdepth 1 \
  -type f \
  -printf '%M %U:%G %f\n'

systemctl --user status minquant-deploy.path --no-pager
systemctl --user status minquant-deploy.service --no-pager

journalctl \
  --user \
  -u minquant-deploy.path \
  -u minquant-deploy.service \
  --since '30 minutes ago' \
  --no-pager
~~~

如果看到 <code>PermissionError</code> 且 queue 文件属于
<code>minquant-webhook:minquant-deploy</code>，检查 user manager 的 Groups。真实安装中，
<code>wsw</code> 已加入 group，但旧 user manager 未继承 group，导致 path 被反复触发后进入
<code>unit-start-limit-hit</code>。

修复顺序：

~~~bash
WSW_UID="$(id -u wsw)"
sudo systemctl restart "user@$WSW_UID.service"

systemctl --user reset-failed \
  minquant-deploy.path \
  minquant-deploy.service

systemctl --user start minquant-deploy.path
~~~

先用 3.4 的 <code>/proc/PID/status</code> 验证新 manager 已包含 group GID。queue marker
仍在磁盘，因此 path 恢复后会自动处理，不要手工删除它。

### 6.4 result 是 failed

~~~bash
DELIVERY_ID='replace-with-delivery-id'
cat "/var/lib/minquant-webhook/results/$DELIVERY_ID.json"

journalctl \
  --user \
  -u minquant-deploy.service \
  --since '30 minutes ago' \
  --no-pager

journalctl \
  --user \
  -u minquant-api.service \
  --since '30 minutes ago' \
  --no-pager
~~~

常见 exit code：

| code | 含义 |
|---|---|
| 64 | delivery ID、SHA 或 worker 参数格式无效。 |
| 65 | 远端 tip 不等于 delivery SHA、Python minor 不符或其他数据契约失败。 |
| 66 | 仓库、配置、数据目录、raw mountpoint 或已安装解释器缺失。 |
| 70 | uv sync 后没有生成可执行的项目 Python。 |
| 75 | 等待部署 flock 超时。 |
| 127 | 命令或固定部署程序不存在，或 dispatcher 启动 worker 失败。 |
| 1 | 启动、健康检查或其他通用部署步骤失败。 |

旧或乱序 Webhook 因 tip 不等而 failed 是安全行为，不要为了让它“通过”而移除等值检查。

### 6.5 GitHub SSH fetch 失败

~~~bash
git -C /home/wsw/app/code/trading config \
  --local \
  --get core.sshCommand

GIT_TERMINAL_PROMPT=0 \
git -C /home/wsw/app/code/trading \
  ls-remote origin refs/heads/release/auto-release
~~~

检查：

1. GitHub Deploy keys 中公钥仍存在且只读；
2. 私钥路径和 mode 是 0600；
3. <code>known_hosts</code> 中有已验证的 GitHub host key；
4. local <code>core.sshCommand</code> 包含 identity、BatchMode 和 strict host key；
5. 服务器能访问 GitHub SSH。

### 6.6 uv 找到 Conda Python 或找不到 3.13

用 systemd 等价的干净环境验证：

~~~bash
env -i \
  HOME=/home/wsw \
  USER=wsw \
  LOGNAME=wsw \
  PATH=/usr/local/bin:/usr/bin:/bin \
  /usr/local/bin/uv python find --no-python-downloads 3.13
~~~

如果失败，重新以 <code>wsw</code> 执行：

~~~bash
env -u CONDA_PREFIX -u VIRTUAL_ENV \
  /usr/local/bin/uv python install 3.13
~~~

不要让 systemd 依赖 <code>conda activate</code>；它不是非交互服务的稳定契约。

### 6.7 Funnel TLS 超时

按顺序区分：

1. <code>tailscale funnel status</code> 是否显示 Funnel on；
2. 本机 9000 是否监听；
3. 公共 DoH 是否已有 A 记录；
4. curl 实际 remote IP 是公网 A、100.x 还是 198.18.x；
5. 用 <code>--resolve</code> 绕过 MagicDNS/代理 fake-IP；
6. 公网 unsigned 请求是否到达 401。

本机把自己的 <code>*.ts.net</code> 解析成 100.x 并不代表公网 GitHub 也会这么解析。GitHub
使用公共 DNS。

### 6.8 API 健康失败

~~~bash
systemctl --user status minquant-api.service --no-pager
journalctl --user -u minquant-api.service -n 200 --no-pager

cat /home/wsw/app/deploy/trading/api-release.env
curl -v http://127.0.0.1:5050/health
~~~

<code>api-release.env</code> 只含 commit identity，不含业务 Secret，可以用于核对。不要打印
<code>/home/wsw/app/shared/trading/.env.test</code>。

当前 Job 只存在 API 内存中，部署或服务重启会中断正在运行的 Job，这是已知正式边界。

### 6.9 没有自动回滚

worker 停止旧 API 后，checkout、uv 或健康步骤都可能失败；此时 API 可能保持停止或不健康。
系统会记录 failed，但不会猜测应该回滚到哪个 commit。先修复明确原因，再用当前
<code>release/auto-release</code> 最新合法 SHA 人工重跑：

~~~bash
git -C /home/wsw/app/code/trading fetch \
  --no-tags \
  origin \
  '+refs/heads/release/auto-release:refs/remotes/origin/release/auto-release'

DEPLOY_SHA="$(
  git -C /home/wsw/app/code/trading \
    rev-parse refs/remotes/origin/release/auto-release
)"

RUN_ID="manual-recovery-$(date +%Y%m%d%H%M%S)"

env \
  -u CONDA_PREFIX \
  -u VIRTUAL_ENV \
  RUN_ID="$RUN_ID" \
  DEPLOY_SHA="$DEPLOY_SHA" \
  /usr/local/libexec/minquant-deploy
~~~

直接运行 worker 会更新 release record 和服务，但不会创建 GitHub delivery result。记录这次
人工处置的 RUN_ID，并保留相关 journal。

## 7. 明确不在当前链路中的组件

以下组件可以保留在其他机器或目录，但不参与测试发布：

- 腾讯云 Webhook 中继与公网 IP；
- 腾讯云 TLS 证书；
- SSH 部署跳转和 forced-command；
- Nginx；
- Docker；
- SQLite；
- 外部消息队列；
- cron 部署；

部署健康检查只从本机 <code>127.0.0.1:5050</code> 访问，Funnel 不映射 5050。当前 Flask
实现实际绑定 <code>0.0.0.0:5050</code>，所以它能否被局域网或 tailnet 访问还取决于主机
路由和防火墙；“不经 Funnel 公网暴露”不能被误读成“只绑定 loopback”。如果未来要求
5050 只能绑定回环地址，应先修改对应运行契约和实现，不能在本指南中暗自假设。

正式生产部署尚未定义，不得把本指南直接复制到 <code>master</code> 生产链路。

## 8. 官方资料

- [uv 安装](https://docs.astral.sh/uv/getting-started/installation/)
- [uv 安装器参数](https://docs.astral.sh/uv/reference/installer/)
- [uv 管理 Python](https://docs.astral.sh/uv/guides/install-python/)
- [Tailscale Linux 安装](https://tailscale.com/docs/install/linux)
- [Tailscale Funnel 原理与排障](https://tailscale.com/docs/features/tailscale-funnel)
- [tailscale funnel CLI](https://tailscale.com/docs/reference/tailscale-cli/funnel)
- [GitHub Deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
- [验证 GitHub SSH 连接与 host fingerprint](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/testing-your-ssh-connection)
- [创建 GitHub Webhook](https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks)
- [验证 Webhook delivery](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
