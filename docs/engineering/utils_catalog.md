# Utils 工具注册表

- **状态**：强制执行
- **适用范围**：仓库中被允许作为跨模块复用入口的 public 工具。
- **用途**：为编码前的复用判断提供唯一注册入口，避免重复实现等价逻辑。

## 登记规则

- 仅登记 public 工具，不登记 private helper。
- 以模块自行定义、可直接 import 的顶层 public symbol 为登记粒度；模块声明
  `__all__` 时以 `__all__` 为准。工具类的 public 方法统一列在该类的使用范围中，
  不再拆成重复条目。
- 每个工具必须写明完整 import 路径、使用范围、不适用范围和最小调用示例。
- 新增、删除、重命名或改变 public 工具的使用范围时，必须同步更新本表。
- 表中存在适用工具时，调用方必须优先复用。

`src.utils.table_ops` 的精确检测语义由
`docs/engineering/table_ops.md` 拥有；本表只登记其 import 和复用边界。

## 工具目录

| 工具 | Import 路径 | 使用范围 | 不适用范围 | 最小调用示例 |
|---|---|---|---|---|
| `open_csv7z_batches` | `from src.utils.csv7z_batch_source import open_csv7z_batches` | 按技术 owner 校验并流式读取 source-native `.csv.7z` 的精确同名 CSV member，在 context manager 内单次产出 `pyarrow.RecordBatch`；自然耗尽时校验 7z 退出码，提前结束或异常时回收子进程。 | 普通 CSV、非 `.csv.7z` 压缩包、context 外消费 iterator、写入 Parquet、业务字段转换、排序或聚合。 | `with open_csv7z_batches(Path("ticks.csv.7z")) as batches: ...` |
| `require_columns` | `from src.utils import table_ops` | 对 `pyarrow.Table` 或 `pandas.DataFrame` 要求指定列名各自恰好出现一次。 | 精确 schema、额外列、列顺序、字段业务含义、scalar 参数或输入转换。 | `table_ops.require_columns(data, ("symbol", "close"), who="daily_bar")` |
| `require_nonempty` | `from src.utils import table_ops` | 对 `pyarrow.Table` 或 `pandas.DataFrame` 要求至少存在一行。 | 决定具体业务中的空表是否合法、检查列或填充缺失数据。 | `table_ops.require_nonempty(data, who="daily_bar")` |
| `require_non_null` | `from src.utils import table_ops` | 对 `pyarrow.Table` 或 `pandas.DataFrame` 的指定列拒绝逻辑缺失值，包括浮点 NaN。 | 字符串、数值域、唯一性、缺失值填充或输入转换。 | `table_ops.require_non_null(data, ("symbol",), who="daily_bar")` |
| `require_nonempty_strings` | `from src.utils import table_ops` | 对 `pyarrow.Table` 或 `pandas.DataFrame` 的指定列要求所有逻辑值均为非空真实字符串。 | trim、大小写或 Unicode 规范化、symbol 格式等字段业务规则或输入转换。 | `table_ops.require_nonempty_strings(data, ("symbol",), who="daily_bar")` |
| `require_unique` | `from src.utils import table_ops` | 对 `pyarrow.Table` 或 `pandas.DataFrame` 要求指定列形成逻辑唯一复合 key；所有逻辑缺失值相等。 | 决定数据集业务 key、要求 key 非 null、排序、去重或输入转换。 | `table_ops.require_unique(data, ("symbol", "trade_date"), who="daily_bar")` |
| `require_finite` | `from src.utils import table_ops` | 对 `pyarrow.Table` 或 `pandas.DataFrame` 的指定列要求所有值均为非 boolean 的有限整数或浮点数。 | Decimal、数字字符串、数值解析、范围规则或输入转换。 | `table_ops.require_finite(data, ("close",), who="daily_bar")` |
| `require_positive` | `from src.utils import table_ops` | 对 `pyarrow.Table` 或 `pandas.DataFrame` 的指定列要求所有值均为严格大于零的有限整数或浮点数。 | Decimal、数字字符串、非负规则、业务量纲或输入转换。 | `table_ops.require_positive(data, ("close",), who="daily_bar")` |
| `DateTimeUtils` | `from src.utils.datetime_utils import DateTimeUtils` | 严格系统日期校验与转换（`require_system_date`、`normalize_source_date`、`to_compact_date`、`days_before`、`date_range`）；UTC epoch microseconds 与 aware datetime 转换（`from_utc_epoch_us`、`to_local`、`local_time_to_utc_epoch_us`）；以 `Asia/Shanghai` 为默认市场时区获取当前时间（`now_utc`、`now`、`today`）。 | `trade_date` 业务语义、source-native datetime 字符串解析、交易时段、交易日历、自动推断 epoch 单位、模块级时间状态或隐藏时区转换。 | `trade_date = DateTimeUtils.require_system_date("2026-07-15", field_name="trade_date")` |
| `DownloadProgress` | `from src.utils.download_utils import DownloadProgress` | 对已知或未知总字节数的下载过程累计字节、按间隔记录速度/百分比/ETA，并通过调用方显式传入的 `ProcessLogger` 记录聚合状态；支持注入 monotonic clock 以便可重复测试。 | 发起下载、重试、断点续传、校验文件完整性、记录原始 payload 或拥有 logger 生命周期。 | `progress = DownloadProgress(total_bytes, "data.7z", logger=process_logger); progress.update(len(chunk)); progress.finish()` |
| `FileSystem` | `from src.utils.filesystem import FileSystem` | 可发现的本地文件操作词汇表：目录创建；文件存在性与必需文件大小查询；二进制单位格式化；通过唯一同目录临时文件提供 `atomic_path`、bytes 原子写入和跨文件系统安全原子复制；文件/目录删除；必需目录的单层扫描和递归大小统计；调用方拥有目录中的临时文件清理。 | 正式路径构造、内容或对象身份判断、metadata/lineage、下载恢复状态、远程/对象存储、多文件事务、并发写协调、断电后的目录项持久性。 | `with FileSystem.atomic_path(output_path) as temporary_path: writer.write(temporary_path)` |
| `logs` | `from src import logs` | 普通业务模块统一使用的项目 Loguru logger；使用 f-string 生成包含稳定 `key=value` 上下文的 debug/info/warning/error 消息，并仅在恢复或任务终止边界记录 exception。Flask 服务配置后写 system file 和 stderr；job 子进程的 stderr 由 `JobRuntime` 捕获。 | 在业务模块中调用 `remove()`/`add()`、记录敏感值、重复记录同一异常堆栈或拥有 sink 和 job 文件生命周期。 | `logs.info(f"request completed; request_id={request_id}")` |
| `ProcessLogger` | `from src.utils.logger import ProcessLogger` | 为公共 logger 和测试替身提供只接收已格式化 info message 的最小类型契约，供需要显式 logger 依赖的底层组件标注。 | 直接实例化、配置 sink、拥有 logger 生命周期或替代普通业务模块的 `from src import logs`。 | `progress = DownloadProgress(total_bytes, filename, logger=logs)` |
| `configure_system_logging` | `from src.utils.logger import configure_system_logging` | 仅由 Flask 服务 composition root 调用一次，清除 Loguru 默认 sink，并把公共 `src.logs` 路由到本次运行的新 system log file 和 stderr；文件不轮转。 | 普通业务模块调用、配置 job 文件、创建第二个 API log、按事件改变 sink 或承担 shutdown。 | `configure_system_logging(Path("logs/system/2026-07-22-09-15-32.123456.log"))` |
| `ParallelExecutor` | `from src.utils.parallel import ParallelExecutor` | 对任意 item 集合执行同类型 handler；`max_workers=1` 时顺序执行，否则使用 `ProcessPoolExecutor`；空输入返回空列表。 | 依赖输入顺序的并行结果（多 worker 时按完成顺序返回）、不可 pickle 的 handler/参数、I/O 协程、失败重试、持久化任务状态。 | `results = ParallelExecutor.run(items=paths, handler=parse_file, max_workers=4)` |
| `write_parquet_atomic` | `from src.utils.parquet_writer import write_parquet_atomic` | 按 `docs/engineering/technology_stack_decisions.md` 定义的物理写入决策，将一个完整 `pyarrow.Table` 使用 PyArrow 默认 row-group 大小写入单个 Parquet 文件，并通过 `FileSystem.atomic_path` 原子发布。 | 自定义 row-group 大小、多批增量写、schema 演化、排序/拼接/去重、dataset/partition 管理、metadata/lineage、业务路径决策或并发写协调。 | `write_parquet_atomic(output_file=path, table=table)` |
| `PathManager` | `from src.utils.path import PathManager` | 绑定调用方提供的已存在绝对 `pathlib.Path` root，通过只读 `storage_root` 属性暴露该规范 root，创建 raw、staging、processed、features、labels、experiments 六个固定顶层目录，并从完整正式身份返回单日或年度规范路径；`require_safe_basename` 校验单一路径段，`require_experiment_id` 校验 CLI/Job experiment ID。 | 环境变量读取、创建 storage root 或业务分区、文件 I/O、数据可用性、schema、metadata/lineage、任意路径拼接、未定义 experiment artifact。 | `pm = PathManager(storage_root); calendar = pm.processed_year_data(dataset_name="trade_calendar", version="v1", calendar_year=2026)` |
| `apply_asof_price_adjustment` | `from src.utils.price_utils import apply_asof_price_adjustment` | 按 `docs/data/price_adjustment_contract.md` 对 DataFrame 返回不修改原对象的 `raw`、前复权（`qfq`）或后复权（`hfq`）价格视图；`qfq`/`hfq` 调整价格只包含有限正数或 null，可覆盖返回副本中的价格列或写入带前缀的新列。 | 文件 I/O、复权因子获取、跨 DataFrame 因子拼接、停牌/除权业务推断；`qfq` 需要输入包含 as-of 日每个 symbol 的唯一 factor。 | `adjusted = apply_asof_price_adjustment(df, adjustment="qfq", asof_date="2026-07-15")` |
| `RetryPolicy` | `from src.utils.retry import RetryPolicy` | 以不可变配置统一校验具体瞬时异常子类、最大尝试次数、初始延迟秒数、退避倍数和 jitter 比例；不允许把宽泛的 `Exception` 作为重试条件。 | 幂等性判断、超时、熔断、日志、任务持久化或隐式创建随机源。 | `policy = RetryPolicy(exceptions=(OSError,), max_attempts=3)` |
| `Retry` | `from src.utils.retry import Retry` | 对同步零参数 operation 直接重试或以装饰器保持原函数签名；时间等待和 jitter 随机源可显式注入。 | async callable、带未绑定参数的 operation、幂等性保障、超时/熔断、日志或跨进程恢复。 | `result = Retry.run(fetch, policy=policy)` |
| `AsyncRetry` | `from src.utils.retry import AsyncRetry` | 对异步零参数 operation 直接重试或以 async 装饰器保持原函数签名；异步等待和 jitter 随机源可显式注入。 | 同步 callable、带未绑定参数的 operation、幂等性保障、超时/熔断、日志或跨进程恢复。 | `result = await AsyncRetry.run(fetch_async, policy=policy)` |
`src/__init__.py` 只导出普通业务代码使用的 `logs`；`src/utils/__init__.py` 当前未导出
public symbol。`configure_system_logging` 只供 Flask 服务 composition root 使用；普通
模块不得直接配置 Loguru sinks。
