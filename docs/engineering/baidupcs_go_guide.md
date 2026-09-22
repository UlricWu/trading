# BaiduPCS-Go：Linux 服务器登录、下载与速度排查

- **Owner 边界**：本文拥有 Linux 服务器上人工使用 BaiduPCS-Go 的安装定位、登录、
  SVIP 下载配置与排障操作约定。
- **适用环境**：`wsw` 用户、Linux x86_64、zsh、`qjfoidnh/BaiduPCS-Go v4.0.2`。
- **核验日期**：2026-09-22。

BaiduPCS-Go 是独立的人工下载工具。下载完成的文件仍需遵循
[数据源契约](../data/source_contract.md)与[存储契约](../data/storage_layout.md)，
才能成为正式数据对象。本文不增加 broker、运行入口、自动入库行为或生产依赖。

## 安装版本与命令定位

本机使用上游 [v4.0.2 Release](https://github.com/qjfoidnh/BaiduPCS-Go/releases/tag/v4.0.2)
提供的 `BaiduPCS-Go-v4.0.2-linux-amd64.zip`，解压后安装到用户可执行目录。
安装时已校验下载包 SHA-256，并通过版本和帮助命令验证。

| 项目 | 已核验状态 |
| --- | --- |
| 当前使用的可执行文件 | `/home/wsw/.local/bin/BaiduPCS-Go`，v4.0.2 |
| 当前用户配置文件 | `/home/wsw/.config/BaiduPCS-Go/pcs_config.json`，权限 `0600` |
| 当前下载保存根目录 | `/home/wsw/Downloads`；实际文件路径以下载输出为准 |

已校验的下载包 SHA-256：

```text
b5f51388b510433668ca22fa5a1cb8840fb4d9725c5edc830554645010a62339
```

已安装 v4.0.2 可执行文件 SHA-256：

```text
c687ef202e467ea1dfb1bb938a4c0550173d277651e3fbc8a26f37cad2a35359
```

后续下载统一使用上述 v4.0.2。本次安装所用 shell 的 PATH 包含
`/home/wsw/.local/bin`；既有 SSH 会话可以先刷新 zsh 的命令路径缓存，再核对解析结果：

```zsh
rehash
command -v BaiduPCS-Go
BaiduPCS-Go --version
/home/wsw/.local/bin/BaiduPCS-Go --version
```

`command -v` 应指向 `/home/wsw/.local/bin/BaiduPCS-Go`，两条版本命令都应输出
`BaiduPCS-Go version v4.0.2`。解析结果不符时检查当前 shell 的 PATH，或直接使用绝对路径。

## 服务器没有浏览器时登录

浏览器在本地电脑运行，BaiduPCS-Go 在 Linux 服务器运行；两者无需在同一台机器。
手机号与短信验证码登录在浏览器中完成，再使用网页会话的 Cookie 登录命令行客户端。
上游推荐 Cookie 登录，旧的用户名、密码交互登录已长期不维护。

1. 在本地电脑的 Chrome 或 Edge 打开[百度网盘](https://pan.baidu.com/)，用手机号与
   验证码登录，进入个人网盘文件列表。
2. 打开开发者工具，进入 **Network → All**，清空过滤条件并刷新页面。
3. 选择 Request URL 以 `https://pan.baidu.com/` 开头的普通页面或接口请求。
4. 在 **Headers → Request Headers → Cookie** 复制完整字段值，不包含 `Cookie:` 前缀。
5. 在服务器的 zsh 终端执行下面的命令，按提示粘贴原始 Cookie。不要在 BaiduPCS-Go
   自身的交互提示符中执行这些 shell 命令。

```zsh
read -rs 'baidu_cookie?粘贴 Cookie 后按回车：'
echo
/home/wsw/.local/bin/BaiduPCS-Go login -cookies="$baidu_cookie"
unset baidu_cookie
```

隐藏输入避免将 Cookie 本身写入 shell 命令历史；Cookie 仍会交给客户端，并保存在其
用户配置中。真实凭证不得复制到文档、聊天、截图或日志，也不要分享完整配置文件。
凭证误发时应撤销对应会话并重新获取；登录失效时重新执行上述流程。

登录后使用只读命令确认可以访问网盘：

```zsh
/home/wsw/.local/bin/BaiduPCS-Go ls /
```

如果需要按字段登录，可以在 **Application → Storage → Cookies →
`https://pan.baidu.com`** 中按 Name 查找并复制 Value：

| 字段 | 用途与参数 |
| --- | --- |
| `BDUSS` | 主要登录凭证，对应 `login -bduss`；不是 `BDUSS_BFESS`。 |
| `STOKEN` | 分享文件转存需要，对应 `login -stoken`；应来自已登录的网盘页面，不是 `bdstoken` 或 `csrfToken`。 |

`wss://webpush.pan.baidu.com/...` 是 WebSocket 请求。`101 Switching Protocols` 表示
连接升级，`sec-websocket-key` 和 `sec-websocket-accept` 都不是网盘登录 Cookie。

## 已采用的 SVIP 全局配置

本机账号由用户确认是超级会员（SVIP），并已明确采用以下配置：

| 配置项 | 值 | 含义 |
| --- | ---: | --- |
| `max_parallel` | `20` | 下载总连接并发上限。 |
| `max_download_load` | `1` | 同时下载一个文件。 |
| `max_download_rate` | `0` | 客户端不额外设置下载速率上限。 |

本次实际只将 `max_parallel` 从 `1` 改为 `20`，其余配置保持原值；修改后已读取配置文件
确认。复用该设置的命令如下：

```zsh
/home/wsw/.local/bin/BaiduPCS-Go config set -max_parallel 20
/home/wsw/.local/bin/BaiduPCS-Go config
```

登录 SVIP 不会自动提高 `max_parallel`。上游建议 SVIP 使用 10～20 个连接、同时下载
1～2 个文件；本机选择 20 个连接、1 个文件。普通账号不套用该设置，应按上游建议将
`max_parallel` 与 `max_download_load` 都设为 `1`。客户端不提供超出账号权限的下载提速。

后续新任务可以直接使用全局值：

```zsh
/home/wsw/.local/bin/BaiduPCS-Go d --status /2026-06-08
```

也可以仅对一次任务明确指定参数：

```zsh
/home/wsw/.local/bin/BaiduPCS-Go d -p 20 -l 1 --status /2026-06-08
```

`-p 20`、`-l 1` 只覆盖本次命令，不持久化到全局配置；它们表示总连接上限与同时下载
文件数，不表示同时下载 20 个文件。已经开始的下载任务不会因其他进程修改全局配置而
热更新并发。重新执行原下载命令时，保持原保存位置并保留断点文件，以使用断点续传。

## 速度慢与暂停的排查顺序

1. **核对版本和并发。** 本次慢速任务实际使用 v4.0.1，命令没有 `-p` 覆盖，
   全局 `max_parallel=1`。仅安装新版或恢复旧进程，都不会把旧任务改成 20 连接。
2. **区分速度与进程暂停。** `Ctrl+Z` 会挂起前台任务；`ps` 中的 `T` 表示停止状态。
   在启动任务的同一个 SSH shell 中用 `jobs` 查看作业，再用 `fg %作业号` 恢复指定作业。
   `continued` 表示原进程恢复，不表示参数或版本发生变化。需要换参数时，恢复后按
   `Ctrl+C` 停止旧任务，再启动新命令，避免两个任务同时写同一目标文件。
3. **使用同条件比较网络。** 本次官方客户端在同一局域网的另一台电脑上达到约
   50 MB/s，服务器网卡协商为 1 Gbps、全双工。约 6 MB/s 不能直接证明服务器只有
   50 Mbps 带宽；50 MB/s 约等于 400 Mbps，且网卡协商速率不等于端到端下载速度。
4. **持续为零时查看线程状态。** `--status` 用于观察各下载线程；分享诊断信息时仅提供
   去除凭证的状态和报错。出现在 `^Z` 之前的 `0B/s`，不能由后来的挂起动作解释。

## 2026-09-22 排障验证记录

这是一份当次运行证据，不是跨环境性能承诺，也不改变正式数据源的选择。

- **程序**：上述 SHA-256 对应的 v4.0.2；三组使用同一版本，未对新旧版本做速度对照。
- **环境**：Linux `6.8.0-139-generic`、x86_64；网卡 `enp5s0` 协商 1000 Mbps、全双工。
- **账号与输入**：同一已登录账号，会员类型由用户确认为 SVIP；远端文件为
  `/2026-06-08/SH_Bond_OrderTrade.csv.7z`，程序显示大小 `585.57 MB`。
- **固定参数**：`--test -l 1 --mode locate --retry 0`；缓存 `65536` bytes，
  `max_download_rate=0`，HTTPS 开启，未配置客户端代理；UA 沿用当时配置。
- **执行方式**：依次使用 `-p 1`、`-p 10`、`-p 20`，每组单次运行最多 15 秒，
  到时通过 `SIGINT` 终止。原下载任务在测试期间保持挂起。测试使用临时目录中的配置副本，
  不保存下载数据，未改动原下载文件；测试前后原配置内容摘要一致。
- **统计口径**：从进度行提取瞬时速度，计算全部采样后半段的中位数和全部采样峰值。
  下表沿用程序显示的 `MB/s` 单位；统计换算使用 `1024²` bytes，即 MiB/s。

| 连接数 | 进度采样数 | 后半段速度中位数 | 峰值 |
| --- | ---: | ---: | ---: |
| 1 | 13 | 14.15 MB/s | 16.16 MB/s |
| 10 | 13 | 27.69 MB/s | 43.08 MB/s |
| 20 | 14 | 38.21 MB/s | 64.72 MB/s |

三组均在时间窗口结束时主动终止，未完成整个文件的下载或内容校验。未归档完整原始日志、
远端文件内容摘要及完整无敏感配置快照，因此这些数值只用于记录当次排障观察，不能作为
可精确恢复的性能基准。短时测试不包含真实磁盘写入，也没有测量长期稳定吞吐。

用户随后使用 20 连接进行实际下载，并回报速度恢复到约 50 MB/s；该数值是用户现场反馈，
不是上述短时测试的均值。本次验证支持“增加并发能明显改善吞吐量”，但未进一步区分
单连接速度受限的服务端策略、TCP 或链路因素，也未证明升级版本本身带来了提速。

用户在确认下载恢复后明确要求将 `max_parallel=20` 持久化，本机已完成设置和读取验证。

## 上游参考

- [BaiduPCS-Go 项目说明：登录与下载配置](https://github.com/qjfoidnh/BaiduPCS-Go)
- [BaiduPCS-Go v4.0.2 Release](https://github.com/qjfoidnh/BaiduPCS-Go/releases/tag/v4.0.2)
- [zsh Jobs & Signals：暂停与恢复作业](https://zsh.sourceforge.io/Doc/Release/Jobs-_0026-Signals.html)
