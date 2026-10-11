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
Postman 通过 Collection 中对应环境的地址变量选择目标服务，异机访问时替换为目标服务器地址。

### Postman 导入与环境切换

在 Postman 的 Import 中导入
[完整 Job API Collection](../../postman/minquant-jobs.postman_collection.json) 一个文件即可。
Collection 内含“测试环境 · 5050”和“训练环境 · 5051”两个文件夹，分别使用
`http://192.168.50.227:5050` 和 `http://192.168.50.227:5051`，无需额外导入或选择
Postman Environment。先发送对应文件夹的 Health，再调用其他接口；异机访问时修改
Collection Variables 中的 `testBaseUrl` 或 `trainingBaseUrl`。训练文件夹访问人工开发服务，
其 Health 的 `environment` 为 `dev`；“训练”只表示客户端文件夹用途。这些客户端预设不改变上述正式部署端口、
服务启动配置或服务端 `ENV`，目标地址必须已有服务监听或端口映射。

Collection 覆盖全部 5 个路由，包含 Standard 数据、Level-2 数据、训练、独立信号及固定风控
回测请求示例，每个环境 9 个请求，共 18 个请求。数据与信号日期范围，以及
训练的 `trainingStart`、`trainingEnd`、`trainingEvalRangeJson`、`trainingModel`、
`trainingFeatureGroup`、`trainingLabelGroup` 在 Collection Variables 中设置；评价范围
可填 JSON object 或 null，日期须落在目标环境实际可用的数据范围内。创建成功后，
Job ID 自动写入 Collection 的 `testJobId` 或 `trainingJobId`，查询和取消读取对应环境的 ID。
回测前从训练 schedule.json 及具体已发布模型文件取得绝对 ref，手动填写 `testModelRef`
或 `trainingModelRef`；固定风控填写对应环境的 RiskCaseRef 及 riskRulesJson，
示例风险阈值只用于演示，发送前按任务确认。Job ID 只标识任务，不能推导模型引用；计划中的 ref 也不证明发布成功。
服务重启后须重新填写或创建 Job ID。单独发送所需请求；批量运行整个 Collection
会创建全部示例 Job，并执行取消请求。

每次检测只查询一次 `daily`，唯一业务参数为请求时 `Asia/Shanghai` 当天的
`trade_date=YYYYMMDD`，`fields` 为空。请求地址沿用当前锁定 Tushare SDK 的规则，
在配置的 gateway 或 SDK 默认地址后追加 `/daily`。连接和读取超时均为 30 秒，不重试、
不跟随重定向、不查询交易日历或其他日期，也不物化查询结果。

上游 HTTP 状态为 `2xx`，且 JSON object 的 `code` 为整数 `0` 时，固定返回 `200` 和
`{"ok": true}`。不以数据行数判断成功，非交易日或当天数据尚未更新时的空结果也通过。
配置加载失败、HTTP 失败、超时、响应解析失败、缺失或无效 `code`、业务 `code != 0`
均返回 `503`，错误码固定为 `tushare_check_failed`。默认响应为：

```json
{"error": {"code": "tushare_check_failed", "message": "Tushare check failed"}}
```

当上游 HTTP 为 `2xx`、JSON object 的 `code` 为非零整数，且 `msg` 精确等于
`抱歉，数据暂不可用，请稍后重试` 时，`error.message` 返回这一明确获准公开的业务提示：

```json
{"error": {"code": "tushare_check_failed", "message": "抱歉，数据暂不可用，请稍后重试"}}
```

其他消息、缺失或类型无效的 `msg`、包含额外文本的消息，以及配置、HTTP、超时、解析
失败仍返回默认提示，不透传任意上游 `msg` 或异常文本。未定义其他可公开提示，不凭错误码
猜测原因，不自动更换 token。响应除上述获准提示外不得包含 token、gateway、上游原始响应、
异常详情或 traceback；日志仍不得记录上游消息及这些信息。检测沿用 API
请求与响应状态日志，不增加检测历史、定时任务或告警渠道；`GET /health` 的契约保持不变。

## POST `/jobs`

请求 body 必须是 JSON object，`kind` 是唯一判别字段，额外字段必须拒绝：

CLI-only 的 `data-calendar`、`data-standard-bootstrap`、`data-level2-minute-backfill`、
`data-stock-1430-backfill`、`data-stock-1430-fusion-backfill`、`data-previous-day-backfill` 与 `data-feature-backfill`
不属于 Job kind；`POST /jobs` 必须将
它们作为不支持的 kind 拒绝。

- `data-standard`、`data-level2`
  - 必须且只允许提供 `start` 与 `end`。
  - 日期必须是规范 `YYYY-MM-DD`，且 `start <= end`。
  - 完整闭区间是一个 workflow 执行单位，因此只创建一个 Job。单日使用
    `start == end`。
- `train`
  - 必须提供 `train_range`、`model` 和 `dataset`，可选 `eval_range`、
    `validation_sessions` 和 `eval_gap_sessions`；不允许其他字段。一次 POST 始终只创建一个训练 Job，
    不接受 `recipe` 或 `combinations`，不按模型对照拆成多个 Job。
  - train_range 和非 null 的 eval_range 必须各自只包含规范日期 `start`、`end`，
    且 start <= end；dataset 必须且只包含 `feature_group` 和 `label_group`。
  - model 与两个组名必须是已支持名称；选项含义及允许组合由
    [training workflow](../offline_workflow_contract.md#training-workflow) 拥有。
    未知名称、跨决策网格组合及嵌套额外字段在创建 Job 前拒绝。
  - eval_range 省略或为 null 时不请求最终评估。整个区间使用同一个模型，不接受
    retrain、eval_ranges 或月度重训配置。
  - validation_sessions 为非负整数，默认0；大于0时把 train_range 末尾 N 个正式交易日
    留作内部验证。实际拟合样本、标签边界隔离及分开保存指标由 workflow 拥有。
  - eval_gap_sessions 为非负整数，默认0，表示 train_range 最后交易日与首个最终评估日之间至少跳过的
    正式交易日数，不含两端；周五训练结束且周一开市时，0允许周一、1最早周二评价。
    标签成熟约束始终有效。参数不改写显式评价日期，精确选择与失败由 workflow 拥有。
  - 日期两端可以是休市日，正式日历解析、空区间、成熟时序及数据完整性在
    workflow 中核验，违反时 Job 为 FAILED，不静默缩短区间。
  - 只创建一个 Job；公开 scope.start/end 是原始训练及评价闭区间的整体包络，
    不使用过滤后的交易日期重新计算。不接受 windows、逐日日期或技术时间参数。
- `backtest`
  - mode 显式选择 signal_eval、strategy_eval、risk_rule_eval、execution_eval 或 full_backtest。
  - body 为 kind、mode 加所选模式的字段，完整 schema 复用
    [CLI Backtest](cli_contract.md#backtest) 的输入表；不把 CLI 的 input-json 字符串嵌入 HTTP。
    不接受其他模式的额外字段。
  - signal 使用 start/end/signal；risk 使用 case_refs/risk；execution 使用
    start/end/order_refs/initial_account/execution；strategy_eval 使用
    start/end/signal/strategy/initial_account/execution；full 另须 risk。
    signal、strategy_eval 和 full 还必须在 body 顶层提供 min_listing_calendar_days，
    构造约束沿用上述 CLI schema；risk/execution 不接受该字段。
    strategy_eval 的成交假设也沿用该 schema，通过 execution.slippage_bp 与 strategy.quote
    成对选择，不增加 mode 或额外 HTTP 参数。
  - 有日期时使用规范 YYYY-MM-DD 且 start<=end；risk 按全部案例输入定义完整任务，不虚构日期。
  - 每个请求只创建一个 Job；路径、数值和嵌套结构先由共享构造边界校验。

一个 Job 对应一个完整 workflow 执行单位；每个有效请求只创建一个 Job。请求必须先完成
字段和业务参数构造，再加入 FIFO；任一字段无效时不得创建任何 Job、ID、日志或子进程。

完整训练示例（日期数据需由目标环境提供）：

```json
{
  "kind": "train",
  "train_range": {"start": "2026-07-01", "end": "2026-07-31"},
  "eval_range": {"start": "2026-08-10", "end": "2026-08-31"},
  "model": "sgd_regression",
  "dataset": {
    "feature_group": "daily_basic_selected_v1",
    "label_group": "daily_close_return_rank_d1/v1"
  }
}
```

独立信号评价示例（预测路径与日期须替换为实际输入）：

```json
{
  "kind": "backtest",
  "mode": "signal_eval",
  "start": "2026-07-03",
  "end": "2026-07-03",
  "min_listing_calendar_days": 120,
  "signal": {"type": "recorded", "prediction_refs": ["/data/prediction.parquet"]}
}
```

已有预测、策略及价量约束模拟成交的示例（不运行业务风控）。日期、路径、初始账户与
行情覆盖须替换为实际输入；所有数值是演示参数。此例的起止时刻为上海时区
2026-07-03 14:30 与 15:00，零滑点仍检查报价和可分配成交量：

```json
{
  "kind": "backtest",
  "mode": "strategy_eval",
  "start": "2026-07-03",
  "end": "2026-07-03",
  "min_listing_calendar_days": 120,
  "signal": {"type": "recorded", "prediction_refs": ["/data/prediction.parquet"]},
  "strategy": {
    "type": "threshold",
    "params": {"threshold": 0.5, "target_quantity": 1000},
    "order_window_minutes": 2,
    "quote": {"buy_offset_bp": 0, "sell_offset_bp": 0}
  },
  "initial_account": {
    "ts_us": 1783060200000000, "trade_date": "2026-07-03", "cash": "20000",
    "positions": {}, "sellable": {}, "average_cost": {}, "peak_equity": 20000
  },
  "execution": {
    "market_ref": "/data/market.json", "end_ts_us": 1783062000000000,
    "quantity_rules": {
      "600000": {
        "buy_min_qty": 100, "buy_step_qty": 100, "sell_min_qty": 1,
        "sell_step_qty": 1, "fill_step_qty": 1, "price_tick": "0.01", "enforce_t1": true
      }
    },
    "fees": {"commission_rate": 0, "stamp_tax_rate": 0, "transfer_rate": 0, "min_commission": 0},
    "annual_factor": 252,
    "slippage_bp": 0
  }
}
```

仅将此例的 strategy.quote 和 execution.slippage_bp 同时移除或设为 null，即选择参考成交。

固定风控案例示例（0.2 是演示参数，实际任务明确选择）：

```json
{
  "kind": "backtest",
  "mode": "risk_rule_eval",
  "case_refs": ["/data/risk-cases.json"],
  "risk": {"rules": [{"type": "max_single_name", "max_ratio": 0.2}]}
}
```

成功固定返回：

```json
{"jobs": [/* one or more Job objects */]}
```

状态码为 `201`。响应不返回 `count`、batch ID、parent ID 或 `Location`。

## 请求构造与 CLI 子进程

HTTP 和 CLI 必须共同使用 `src.jobs.requests` 构造完整、可直接消费的 data、training
或 backtest submission；HTTP 层只拥有 JSON object 形状、`kind` allowlist 和每种 kind
的字段 allowlist，不得复制日期、mode、model ref 或 strategy 的业务校验。

Backtest 的训练记录由 workflow 从指定模型的同目录 `schedule.json` 读取，模型关联和
训练标签成熟时间约束由 [Backtest workflow](../offline_workflow_contract.md#backtest-workflow)
拥有，失败为 FAILED。HTTP 不接收 `model_use`、`training_schedule_ref`、
`effective_ts_us` 或 `availability_evidence_ref`，也不要求用户填写对应 Postman 变量。
这些信息不能由调用者任意声明来绕过时序核验。build_cli_command 将所选任务已校验的
嵌套字段编码为单个 --input-json 参数，并传递 mode 及服务器生成的 experiment ID。

Job 子进程必须使用当前服务的 Python 解释器执行 `-m src.cli`，不得调用 PATH 中的
`python`。Data Job 的 UUID 只标识 Job；training 和 backtest 必须将同一个完整 Job UUID
同时作为 `job_id` 和 CLI `experiment_id`。HTTP 不接受客户端指定的 experiment ID。

## Job 公开对象

Job JSON 必须且只包含：

```text
job_id, kind, scope, status, submitted_at, started_at, finished_at
```

`scope` 只表达该 Job 的完整执行单位。日期任务使用：

```json
{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
```

risk_rule_eval 使用 `{"case_refs":["/absolute/risk-cases.json"]}`，不读取案例文件来伪造提交时
的日期范围。两种 scope 均只包含各自的字段，不附加空 start/end。

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
- Training 所选日期违反 workflow 前置条件或 backtest 没有相邻 timing 时，workflow 以
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
