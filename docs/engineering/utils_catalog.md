# Utils 工具注册表

- **状态**：强制执行
- **适用范围**：`src/utils` 中允许被其他模块调用的 public 工具。
- **用途**：为编码前的复用判断提供唯一注册入口，避免重复实现等价逻辑。

## 登记规则

- 仅登记 public 工具，不登记 private helper。
- 以模块自行定义、可直接 import 的顶层 public symbol 为登记粒度；模块声明
  `__all__` 时以 `__all__` 为准。工具类的 public 方法统一列在该类的使用范围中，
  不再拆成重复条目。
- 每个工具必须写明完整 import 路径、使用范围、不适用范围和最小调用示例。
- 新增、删除、重命名或改变 public 工具的使用范围时，必须同步更新本表。
- 表中存在适用工具时，调用方必须优先复用。

## 工具目录

| 工具 | Import 路径 | 使用范围 | 不适用范围 | 最小调用示例 |
|---|---|---|---|---|
| `Csv7zBatchSource` | `from src.utils.csv7z_batch_source import Csv7zBatchSource` | 校验并流式读取 source-native `.csv.7z`，逐批产出 `pyarrow.RecordBatch`；自然读完时校验 7z 退出码，提前结束或异常时终止并释放子进程。 | 普通 CSV、非 `.csv.7z` 压缩包、写入 Parquet、业务字段转换、排序或聚合。 | `for batch in Csv7zBatchSource(Path("ticks.csv.7z")): ...` |
| `TradingSession` | `from src.utils.datetime_utils import TradingSession` | 以不可变、已校验的本地开闭时间表达一个 inclusive 交易时段，供交易时间判断显式注入。 | 跨午夜时段、节假日规则、时区、交易日历持久化或自行提供市场默认时段。 | `morning = TradingSession(opens_at=time(9, 30), closes_at=time(11, 30))` |
| `DateTimeUtils` | `from src.utils.datetime_utils import DateTimeUtils` | 严格系统日期校验与转换（`require_system_date`、`require_trade_date`、`to_compact_date`、`days_before`、`date_range`）；source date 规范化；UTC epoch 解析和显式本地时区转换；交易时间解析、组合；使用调用方注入的 `TradingSession`/交易日集合判断交易时间与交易日；获取当前 UTC/上海时间。 | 节假日数据下载、自动推断未知时区、非 UTC epoch 的隐式解释、业务交易日历持久化或模块级交易日状态。 | `trade_date = DateTimeUtils.require_trade_date("2026-07-15")` |
| `DownloadProgress` | `from src.utils.download_utils import DownloadProgress` | 对已知或未知总字节数的下载过程累计字节、按间隔记录速度/百分比/ETA，并通过调用方显式传入的 `ProcessLogger` 记录聚合状态；支持注入 monotonic clock 以便可重复测试。 | 发起下载、重试、断点续传、校验文件完整性、记录原始 payload 或拥有 logger 生命周期。 | `progress = DownloadProgress(total_bytes, "data.7z", logger=process_logger); progress.update(len(chunk)); progress.finish()` |
| `FileSystem` | `from src.utils.filesystem import FileSystem` | 目录创建；文件存在性判断与必需文件大小查询；非空文件同尺寸谓词（`files_have_same_nonzero_size`）；可读大小格式化；bytes 原子写入；跨文件系统安全原子复制；消费同目录 staged file 并原子发布（`publish_file_atomic`）；文件/目录删除；必需目录的单层扫描和大小统计；临时文件清理。 | 内容哈希或逐字节一致性校验（同尺寸谓词不比较内容）、远程/对象存储、事务式多文件提交、跨目录 staged file 发布、递归目录扫描筛选。 | `FileSystem.publish_file_atomic(staged_path, output_path)` |
| `logs` | `from src import logs` | 普通业务模块统一使用的项目 Loguru logger；使用 `{}` 占位符延迟格式化包含稳定 `key=value` 上下文的 debug/info/warning/error 消息，并仅在恢复或任务终止边界记录 exception。进程启动前未配置时沿用 Loguru 默认控制台行为，配置后写入该进程唯一 scope 的 sinks。 | 在业务模块中调用 `remove()`/`add()`/`configure()`、使用 f-string 或字符串拼接、记录敏感值、重复记录同一异常堆栈或拥有 sink 生命周期。 | `logs.info("request completed; request_id={}", request_id)` |
| `LogLevel` | `from src.utils.logger import LogLevel` | 为进程 logger 选择 `DEBUG`、`INFO`、`WARNING`、`ERROR` 或 `CRITICAL` 的显式日志级别。 | 自定义数值级别、按事件动态改变级别或绕过 `LoggingSettings` 直接配置 sink。 | `settings = LoggingSettings(log_root=log_root, level=LogLevel.INFO)` |
| `LoggingSettings` | `from src.utils.logger import LoggingSettings` | 校验进程级 Loguru 配置使用的绝对 `log_root`、日志级别和按日轮转保留天数；配置对象不可变。 | 从环境变量读取配置、创建目录、选择 API/system/job 路径或拥有 sink 生命周期。 | `settings = LoggingSettings(log_root=Path("/var/log/min_quant"))` |
| `JobLogContext` | `from src.utils.logger import JobLogContext` | 校验 job ID 的文件名安全字符和长度，并显式携带 job 日志日期分区。 | 从环境或当前时间隐式推导 job 上下文、表达业务状态或允许路径片段。 | `context = JobLogContext(job_id="job-01", run_date=date(2026, 7, 15))` |
| `ProcessLogger` | `from src.utils.logger import ProcessLogger` | 为 `src.logs` 和测试替身提供最小参数化日志类型契约，供需要显式 logger 依赖的底层组件标注；使用 Loguru `{}` 占位符。 | 直接实例化、配置 sinks、拥有 logger 生命周期或替代普通业务模块的 `from src import logs`。 | `def report(logger: ProcessLogger, row_count: int) -> None: logger.info("rows={}", row_count)` |
| `LoggingSession` | `from src.utils.logger import LoggingSession` | 作为 composition root 配置进程唯一日志 scope 后返回的生命周期类型契约；支持 `with` 和可重试的 `close()`，实际 owner 由 `configure_*_logging` 创建。 | 直接实例化、普通业务模块持有、同一进程同时存在多个 session、记录日志或由被依赖组件关闭。 | `with configure_api_logging(settings): run_api()` |
| `configure_api_logging` | `from src.utils.logger import configure_api_logging` | 仅在 API 进程 composition root 调用一次，将公共 `src.logs` 路由到 `api/api.current.log` 和控制台，并返回 sink owner。 | 普通业务模块调用、job/system 日志、同一进程重复配置或忽略 session 生命周期。 | `with configure_api_logging(settings): run_api()` |
| `configure_system_logging` | `from src.utils.logger import configure_system_logging` | 仅在 system 进程 composition root 调用一次，将公共 `src.logs` 路由到 `system.log` 和控制台，并返回 sink owner。 | 普通业务模块调用、API/job 日志、同一进程重复配置或忽略 session 生命周期。 | `with configure_system_logging(settings): run_system()` |
| `configure_job_logging` | `from src.utils.logger import configure_job_logging` | 仅在 job 进程 composition root 调用一次，使用已校验 `JobLogContext` 将公共 `src.logs` 路由到 file-only 的 `jobs/YYYY-MM-DD/<job_id>.log`，并返回 sink owner。 | 普通业务模块调用、读取环境或当前日期、接受未校验路径、输出到 API/system 日志或忽略 session 生命周期。 | `with configure_job_logging(settings, context): run_job()` |
| `ParallelExecutor` | `from src.utils.parallel import ParallelExecutor` | 对任意 item 集合执行同类型 handler；`max_workers=1` 时顺序执行，否则使用 `ProcessPoolExecutor`；空输入返回空列表。 | 依赖输入顺序的并行结果（多 worker 时按完成顺序返回）、不可 pickle 的 handler/参数、I/O 协程、失败重试、持久化任务状态。 | `results = ParallelExecutor.run(items=paths, handler=parse_file, max_workers=4)` |
| `ParquetAppendWriter` | `from src.utils.parquet_writer import ParquetAppendWriter` | 将一个或多个同 schema 的 `pyarrow.Table` 顺序写入单个 Parquet；支持输入 table 分块和 row-group 大小；通过临时文件替换实现单文件原子落盘；`rows` 返回累计行数；context manager 在成功时发布、异常时清理未发布临时文件。 | schema 演化、排序/拼接/去重、dataset/partition 管理、metadata/lineage、业务路径决策、并发写同一文件。 | `with ParquetAppendWriter(output_file=path) as writer: writer.write(table)` |
| `write_arrow_table_parquet` | `from src.utils.parquet_writer import write_arrow_table_parquet` | 使用 `ParquetAppendWriter` 一次性把一个 Arrow Table 写到一个 Parquet 文件，并支持显式 schema、输入分块和 row-group 配置。 | 多批增量调用、dataset/partition 管理、业务 schema 和路径生成；这些由调用方准备。 | `output = write_arrow_table_parquet(output_file=path, table=table)` |
| `PathManager` | `from src.utils.path import PathManager` | 按 `docs/data/storage_layout.md` 从显式绝对 `storage_root` 或 `ZERO_STORAGE_ROOT` 构造正式存储门面；生成 raw、staging、processed、features、labels、experiments、registry 的规范目录/文件路径；读取必需的 processed/feature/label Parquet。 | 自动推导 repo/home 路径、创建深层业务目录、selector 解析、artifact/data 业务语义、写文件、metadata/lineage 生成、任意文件名拼接。 | `pm = PathManager.from_env(); path = pm.processed_data("bars", "v1", "2026-07-15")` |
| `apply_asof_price_adjustment` | `from src.utils.price_utils import apply_asof_price_adjustment` | 按 `docs/data/price_adjustment_contract.md` 对 DataFrame 返回不修改原对象的 `raw`、前复权（`qfq`）或后复权（`hfq`）价格视图；可覆盖返回副本中的价格列或写入带前缀的新列。 | 文件 I/O、复权因子获取、跨 DataFrame 因子拼接、停牌/除权业务推断；`qfq` 需要输入包含 as-of 日每个 symbol 的唯一 factor。 | `adjusted = apply_asof_price_adjustment(df, adjustment="qfq", asof_date="2026-07-15")` |
| `RetryPolicy` | `from src.utils.retry import RetryPolicy` | 以不可变配置统一校验具体瞬时异常子类、最大尝试次数、初始延迟秒数、退避倍数和 jitter 比例；不允许把宽泛的 `Exception` 作为重试条件。 | 幂等性判断、超时、熔断、日志、任务持久化或隐式创建随机源。 | `policy = RetryPolicy(exceptions=(OSError,), max_attempts=3)` |
| `Retry` | `from src.utils.retry import Retry` | 对同步零参数 operation 直接重试或以装饰器保持原函数签名；时间等待和 jitter 随机源可显式注入。 | async callable、带未绑定参数的 operation、幂等性保障、超时/熔断、日志或跨进程恢复。 | `result = Retry.run(fetch, policy=policy)` |
| `AsyncRetry` | `from src.utils.retry import AsyncRetry` | 对异步零参数 operation 直接重试或以 async 装饰器保持原函数签名；异步等待和 jitter 随机源可显式注入。 | 同步 callable、带未绑定参数的 operation、幂等性保障、超时/熔断、日志或跨进程恢复。 | `result = await AsyncRetry.run(fetch_async, policy=policy)` |
| `SEVEN_ZIP_CANDIDATES` | `from src.utils.seven_zip import SEVEN_ZIP_CANDIDATES` | 只读查看 7z-compatible CLI 的首选探测顺序：`7zz`、`7za`、`7z`。 | 运行时修改候选项、表达已安装程序或为业务代码自行选择可执行文件。 | `candidates = SEVEN_ZIP_CANDIDATES` |
| `resolve_7z_executable` | `from src.utils.seven_zip import resolve_7z_executable` | 按 `SEVEN_ZIP_CANDIDATES` 顺序查找并返回首个已安装的 7z-compatible CLI；支持注入 `which` 以便隔离测试。 | 安装 7z、版本/能力校验、执行解压、shell 命令字符串拼接。 | `executable = resolve_7z_executable()` |
| `open_extract_stdout` | `from src.utils.seven_zip import open_extract_stdout` | 以参数列表启动 `7z x -so` compatible 子进程，将单个 archive 的解压内容流式暴露到 stdout；支持注入进程依赖以便隔离测试。 | 解析 CSV、检查子进程退出码、管理进程完整生命周期、写入解压文件或处理多成员归档语义；调用方负责关闭/终止进程并处理错误。 | `proc = open_extract_stdout(path); header = proc.stdout.readline(); proc.kill()` |

`src/__init__.py` 只导出普通业务代码使用的 `logs`；`src/utils/__init__.py` 当前未导出
public symbol。以下划线开头的 helper 和 class（例如 `_Csv7zReader`、
`_PopenFactory`、`_LoguruSinkRegistry`）不属于可复用 API。`logger.py` 的配置对象、
session owner 和三个 scope-specific 配置函数只供 composition root 或显式依赖类型标注
使用；普通模块不得直接配置 Loguru sinks。
