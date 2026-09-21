# Job API 契约

- **状态**：正式 owner
- **适用范围**：进程内 Job 身份、HTTP 接口、请求字段、队列、状态机、取消、CLI
  子进程边界和按需 Tushare 可用性检测。
- **CLI owner**：[`docs/engineering/cli_contract.md`](cli_contract.md)
- **日志 owner**：[`docs/engineering/technology_stack_decisions.md`](technology_stack_decisions.md)

## Endpoint 集合

服务只提供：

```text
POST /jobs
GET  /jobs/<job_id>
POST /jobs/<job_id>/cancel
GET  /health
POST /checks/tushare
```

不提供 Job 列表、批次对象、pending clear、kill、job log 下载或其他 Job endpoint。
Job、队列和状态都只存在于当前服务进程；服务重启不恢复历史 Job。服务必须以单个
Flask 进程运行，多个 worker 进程会形成彼此独立且不一致的队列，因此不受支持。

服务入口在启动时读取进程变量 `MINQUANT_API_HOST` 和 `MINQUANT_API_PORT`。变量缺失时
分别使用 `0.0.0.0` 和 `5051`；host 必须非空白，port 必须可解析为 `1..65535` 的整数，
否则必须在创建 `JobRuntime` 前启动失败。同一服务器上的人工开发入口使用 `5051`，测试部署
必须显式配置 `5050`；环境差异不得通过修改 Python 源码中的端口实现。

`GET /jobs/<job_id>` 对已知 Job 返回当前 Job object 和 `200`。`GET /health` 固定返回
`200` 和以下精确字段：

```json
{
  "ok": true,
  "environment": "test",
  "release_ref": "release/auto-release",
  "commit_sha": "0123456789abcdef0123456789abcdef01234567"
}
```

`environment`、`release_ref` 和 `commit_sha` 在 Flask app 创建时分别读取进程变量 `ENV`、
`MINQUANT_RELEASE_REF` 和 `MINQUANT_COMMIT_SHA`，并在该进程生命周期内保持不变。未提供时
分别使用 `dev`、`workspace` 和 `workspace`，只表示本地工作区运行；测试部署必须显式注入
并校验上例对应的环境、release ref 和目标完整 SHA。Health 只证明当前 API 进程可响应及
其 release identity，不探测 FTP、Tushare、数据对象或尚未提交的 Job 依赖。

## POST `/checks/tushare`

该接口无请求参数，同步检测服务当前配置的 Tushare 可用性，不创建 Job。每次请求通过
`AppConfig.load()` 重新加载配置，使用其中的 `TUSHARE_TOKEN` 和 `TUSHARE_GATEWAY`；
配置加载语义由 [`cli_contract.md`](cli_contract.md#appconfig) 拥有。配置文件更新后，
下一次检测使用新配置，不缓存 token 或检测结果，也不写入 SDK 的本地 token 文件。

调用时必须明确目标环境，不能默认选择开发服务。同机访问示例：

```bash
# 测试环境：服务进程 ENV=test，端口 5050
curl -i -X POST http://127.0.0.1:5050/checks/tushare

# 开发环境：服务进程 ENV=dev，端口 5051
curl -i -X POST http://127.0.0.1:5051/checks/tushare
```

检测使用目标服务进程 `ENV` 对应的配置文件；客户端选择端口不会修改服务的 `ENV`。
Postman 通过 `baseUrl` 选择目标服务，异机访问时同时替换为目标服务器地址。

每次检测只查询一次 `daily`，唯一业务参数为请求时 `Asia/Shanghai` 当天的
`trade_date=YYYYMMDD`，`fields` 为空。请求地址沿用当前锁定 Tushare SDK 的规则，
在配置的 gateway 或 SDK 默认地址后追加 `/daily`。连接和读取超时均为 30 秒，不重试、
不跟随重定向、不查询交易日历或其他日期，也不物化查询结果。

上游 HTTP 状态为 `2xx`，且 JSON object 的 `code` 为整数 `0` 时，固定返回 `200` 和
`{"ok": true}`。不以数据行数判断成功，非交易日或当天数据尚未更新时的空结果也通过。
配置加载失败、HTTP 失败、超时、响应解析失败、缺失或无效 `code`、业务 `code != 0`
均固定返回 `503`：

```json
{"error": {"code": "tushare_check_failed", "message": "Tushare check failed"}}
```

失败只表示本次检测未通过，不区分 token 无效、权限、限流或网络原因，不自动更换 token。
响应和日志不得包含 token、gateway、上游原始响应、异常详情或 traceback。检测沿用 API
请求与响应状态日志，不增加检测历史、定时任务或告警渠道；`GET /health` 的契约保持不变。

## POST `/jobs`

请求 body 必须是 JSON object，`kind` 是唯一判别字段，额外字段必须拒绝：

CLI-only 的 `data-calendar`、`data-standard-bootstrap`、`data-level2-minute-backfill`、
`data-stock-1430-backfill`、`data-stock-1430-fusion-backfill` 与 `data-feature-backfill`
不属于 Job kind；`POST /jobs` 必须将
它们作为不支持的 kind 拒绝。

- `data-standard`、`data-level2`
  - 必须且只允许提供 `start` 与 `end`。
  - 日期必须是规范 `YYYY-MM-DD`，且 `start <= end`。
  - 完整闭区间是一个 workflow 执行单位，因此只创建一个 Job。单日使用
    `start == end`。
- `train`
  - 必须且只允许提供 `start` 与 `end`。
  - 完整范围是一个 workflow 执行单位，因此只创建一个 Job。
- `backtest`
  - 必须且只允许提供 `mode`、`start`、`end`、`model_experiment` 和 `strategy`。
  - 字段业务语义与 CLI 同名参数一致。
  - 完整范围是一个 workflow 执行单位，因此只创建一个 Job。

一个 Job 对应一个完整 workflow 执行单位；每个有效请求只创建一个 Job。请求必须先完成
字段和业务参数构造，再加入 FIFO；任一字段无效时不得创建任何 Job、ID、日志或子进程。

成功固定返回：

```json
{"jobs": [/* one or more Job objects */]}
```

状态码为 `201`。响应不返回 `count`、batch ID、parent ID 或 `Location`。

## 请求构造与 CLI 子进程

HTTP 和 CLI 必须共同使用 `src.jobs.requests` 构造完整、可直接消费的 data、training
或 backtest submission；HTTP 层只拥有 JSON object 形状、`kind` allowlist 和每种 kind
的字段 allowlist，不得复制日期、mode、model experiment 或 strategy 的业务校验。

Job 子进程必须使用当前服务的 Python 解释器执行 `-m src.cli`，不得调用 PATH 中的
`python`。Data Job 的 UUID 只标识 Job；training 和 backtest 必须将同一个完整 Job UUID
同时作为 `job_id` 和 CLI `experiment_id`。HTTP 不接受客户端指定的 experiment ID。

## Job 公开对象

Job JSON 必须且只包含：

```text
job_id, kind, scope, status, submitted_at, started_at, finished_at
```

`scope` 只表达该 Job 的完整执行单位：

```json
{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
```

三个时间字段使用带 `Asia/Shanghai` UTC offset、包含微秒的 ISO 8601 字符串。
`started_at` 和 `finished_at` 在尚未发生对应事件时为 `null`。不得公开 command、PID、
日志路径、退出码、duration、错误详情、strategy、配置、异常 repr 或 traceback。

## FIFO、并发和状态机

当前进程拥有一个不设总容量上限的 FIFO pending 队列；API 不定义总接纳容量，不因已有
pending Job 返回容量错误，也不提供 `QUEUE_CAPACITY` 或其他运行时安全上限。实际执行
并发固定为 `2`，`RUNNING + CANCELLING` 共同占用执行槽。一次到达十个有效请求时全部
接收，最多两个执行，其余按接纳顺序等待。

公开状态只有：

```text
PENDING
RUNNING
CANCELLING
SUCCESS
SKIPPED
FAILED
CANCELLED
```

状态转换为：

- 接纳后先进入 `PENDING`。
- 只有 job log、子进程和 wait/reap 生命周期都已建立后，才从 `PENDING` 进入
  `RUNNING`。
- 子进程启动前的 runtime failure 进入 `FAILED`，`started_at` 保持 `null`，
  `finished_at` 被设置。
- 子进程退出码 `0`、`75` 和其他值分别产生 `SUCCESS`、`SKIPPED` 和 `FAILED`。
- Standard 或 Level-2 Data 的任一正式交易日 fact 缺失时，workflow 失败，CLI 子进程退出
  非零，Job 因而进入 `FAILED`；Data workflow 不产生退出码 `75`。
- Training 没有可执行 window 或 backtest 没有相邻 timing 时，workflow 以
  `ValueError` 失败，CLI 子进程退出非零，Job 因而进入 `FAILED`；不得改成 `SKIPPED`。
- `PENDING` 取消直接进入 `CANCELLED`，不创建 job log 或子进程。
- `RUNNING` 取消进入 `CANCELLING`；只有进程组退出并完成 wait/reap 后才进入
  `CANCELLED`。
- `SUCCESS`、`SKIPPED`、`FAILED` 和 `CANCELLED` 是不可变终态。

Job 完成或取消并释放执行槽后，runtime 必须继续调度 FIFO 中最早的 `PENDING` Job。

## POST `/jobs/<job_id>/cancel`

- `PENDING`：从 FIFO 移除并返回 `CANCELLED`，状态码 `200`。
- `RUNNING`：向当前 `Popen` 拥有的进程组发送 `SIGTERM`，返回 `CANCELLING`，状态码
  `202`。
- `CANCELLING`：不重复发送信号或重置等待期，返回当前对象，状态码 `202`。
- `CANCELLED`：幂等返回当前对象，状态码 `200`。
- `SUCCESS`、`SKIPPED` 或 `FAILED`：返回 `409 job_not_cancellable`。
- 未知 ID：返回 `404 job_not_found`。

子进程必须以独立 session 启动。发送 `SIGTERM` 后 10 秒仍未退出时，runtime 向同一
进程组发送 `SIGKILL`；`CANCELLING` 在 wait/reap 完成前始终占用并发槽。不得保存并
重新解释裸 PID，也不得在进程尚未回收时提前声称 `CANCELLED`。

服务正常关闭时必须先停止接纳请求，取消并回收正在执行的子进程；尚未执行的内存队列
随进程结束丢失，不建立恢复或持久化语义。

## 错误响应

所有 HTTP 错误都必须返回 JSON，格式为：

```json
{"error": {"code": "error_code", "message": "safe message"}}
```

字段错误在能够归属单个字段时增加 `"field"`。已定义错误为：

- 请求 JSON、字段或业务参数无效：`400 invalid_job_request`；不得分配 Job 身份。
- Job 不存在：`404 job_not_found`；不得在 body 回显客户端提供的 ID。
- Job 已处于不可取消终态：`409 job_not_cancellable`。
- Tushare 可用性检测未通过：`503 tushare_check_failed`。
- 未预期服务错误：`500 internal_error`，不得暴露内部异常。

框架产生的其他 HTTP 错误也必须使用同一 JSON envelope，不得返回 Werkzeug HTML。
