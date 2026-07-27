# 技术栈决策

- **状态**：强制执行
- **适用范围**：项目日志实现、单文件 Parquet 物理写入，以及 Level-2 source-native
  `.csv.7z` raw payload 的 ingest、读取、归一化和转换流程。
- **用途**：记录已经明确选定的技术栈、生产约束及其决策依据。改变本文中的技术
  选型或约束前，必须先更新本文并补充新的验证依据。
- **规范词**：本文中的“必须”“不得”“仅”均为硬约束，不表示建议。

## 日志技术栈

Loguru 是项目明确选定的日志技术栈。项目自有日志实现必须以 Loguru 为基础；不得
在未更新本决策文档的情况下引入另一套并行的日志技术栈。

每次 Flask 服务运行只有一个 system log。服务 composition root 必须在创建 Flask app
前读取一次 `Asia/Shanghai` 启动时间，以该时间创建
`logs/system/YYYY-MM-DD-HH-MM-SS.ffffff.log`，并配置公共 `src.logs`，将 API 请求、
响应、服务启停、Job 接纳、状态转换、取消和服务内部失败写入该文件与 stderr。同一次
服务运行始终写入这个文件，不按时间或大小轮转，也不自动清理历史文件；启动文件已
存在时必须失败，不得追加。API 请求日志属于 system log，不得另建 API file sink。
System log 不得记录请求 payload、strategy、子进程 argv 或子进程 traceback。

每个 job 的日志包含文件身份首行及该 job 子进程的完整 stdout/stderr 输出。每次 job
启动时，`JobRuntime` 必须只读取一次 `Asia/Shanghai` 时间，该时间同时确定
`job.started_at` 和 `logs/jobs/YYYY-MM-DD/<job_id>.log`。`JobRuntime` 必须以独占创建
模式打开该路径，并在启动子进程前以 UTF-8 写入 `log_file=<job_id>.log` 作为独占第一
行，随后从第二行起写入该子进程的完整 stdout/stderr 输出。`JobRuntime` 拥有文件关闭、
子进程重定向和 wait/reap 的完整生命周期；该文件不是 Loguru file sink。子进程沿用
Loguru stderr 输出，由 `JobRuntime` 捕获到 job 文件；不得再让 LOG 模块或 job 子进程
打开同一个 job 文件。Job 业务 traceback 只进入 job 文件，system log 只记录带
`job_id` 的生命周期摘要；runtime 自身的文件、进程或线程启动失败属于 system log。
尚未启动即取消的 Job 不得创建 job log。历史 job log 不自动清理或回写。

CLI 不拥有 start/done 日志；offline workflow/pipeline 拥有业务运行日志。API 不提供
job log endpoint，不得扫描日志目录，也不得把内部文件路径、异常 repr 或 traceback
写入 Job JSON。

## 单文件 Parquet 物理写入

### Owner 边界

本节拥有 `src.utils.parquet_writer.write_parquet_atomic` 的 Parquet 物理写入决策。
`docs/engineering/utils_catalog.md` 只登记该 public utility 的 API 和适用范围；
`FileSystem.atomic_path` 只拥有同目录临时文件和原子发布；数据 schema、排序、路径、
metadata 和 lineage 继续由各自的业务 owner 拥有。

### 依赖基线

当前基线固定为 `pyarrow==25.0.0` 和 `numpy==2.5.1`，由 `pyproject.toml` 和
`uv.lock` 实现。`numpy==2.4.0` 已被上游以 backward compatibility bug 为由撤回，
不得继续作为依赖基线。

升级 PyArrow 时，必须先确认目标版本 `pyarrow.parquet.write_table` 的默认 row-group
语义，并重跑 Parquet writer 和 Access 读取边界的测试。升级 NumPy 时，必须至少验证
NumPy、Pandas 和 PyArrow 的导入及 Pandas-to-Arrow 转换；不得只依据 resolver 成功
宣称依赖兼容。

### 写入契约

`write_parquet_atomic` 必须接收一个完整的 `pyarrow.Table`，并使用 ZSTD compression、
dictionary encoding 和 statistics 写入单个 Parquet 文件。调用 `pyarrow.parquet.write_table`
时必须省略 `row_group_size`，使用 PyArrow 默认值；在当前 `25.0.0` 基线中，该默认值为
`min(table.num_rows, 1_048_576)`。项目不得另设 `64_000` 常量、配置项或 public 参数。

该 utility 不负责按 symbol、daily universe 或其他业务维度组织 row group，也不负责
多批增量写。既有 Parquet 文件不会因依赖或 writer 变化而自动重写。

### Row-group 决策依据

2026-07-22 使用 `SZ_Trade/trade_date=2026-05-06/SZ_Trade.csv.7z` 归一化得到的同一张
`93,547,255` 行、11 列 Arrow Table，对 `1_048_576` 和 `64_000` 两种 row-group
大小进行了同机对比。两组写入均使用 ZSTD、dictionary encoding 和 statistics：

| 指标 | `1_048_576` | `64_000` | `64_000` 相对变化 |
| --- | ---: | ---: | ---: |
| Row groups | `90` | `1,462` | `+1,372` |
| 写入耗时 | `12.458530` 秒 | `17.662317` 秒 | `+41.77%` |
| 文件大小 | `959,789,674` bytes | `1,347,972,464` bytes | `+40.44%` |

在 warm OS page cache 下，按当前 Access 等价路径读取的中位数如下，每个场景重复 5 次：

| 读取场景 | 返回行数 | `1_048_576` | `64_000` | `64_000` 相对变化 |
| --- | ---: | ---: | ---: | ---: |
| 单一 symbol `000001` | `90,549` | `0.010355` 秒 | `0.009418` 秒 | `-9.05%` |
| 固定 10 个稀疏 symbols | `592,384` | `0.085765` 秒 | `0.020406` 秒 | `-76.21%` |
| 覆盖约 90% 行的 2,073 个 symbols | `84,249,658` | `0.533800` 秒 | `0.580622` 秒 | `+8.77%` |

两种布局的 schema、总行数、抽样和各读取场景结果均一致。初版 IPC 序列化字节摘要
曾把逻辑相等的 Table 判为不同，因此不作为内容正确性的 oracle；最终使用逐场景
`Table.equals` 验证。

该基准在 Python `3.13.13`、PyArrow `24.0.0`、单台 Mac/APFS 和 warm cache 下完成；
它只覆盖一个深圳 Trade 日期和预先固定的技术场景，不代表真实 daily universe 分布，
也不是跨机器、跨日期的性能 SLA。升级到 PyArrow `25.0.0` 后已确认默认 row-group
语义仍为最多 `1_048_576` 行。

`64_000` 对固定 10-symbol 稀疏场景有明显读取收益，但单 symbol 收益较小，并同时产生
约 40% 的文件体积和写入耗时成本；高覆盖读取反而更慢。当前没有已定义的稀疏读取 SLA
要求承担该成本，因此项目采用 PyArrow 默认值，不维护自定义 row-group 策略。未来只有
在业务 owner 先定义查询分布和可证伪 SLA 后，新的证据才能改变本决策。

## Level-2 source-native `.csv.7z`

### 7z CLI 发现顺序与失败语义

读取 source-native `.csv.7z` raw payload 的低层工具必须按以下顺序发现 7z-compatible
CLI：

1. `7zz`
2. `7za`
3. `7z`

`7zz` 是主链路首选，`7za` 和 `7z` 仅作为 fallback。未发现可用命令时，归一化或
转换流程必须失败，不得写入空的 processed 数据，也不得提交 processed `meta.json`。

source-native archive 必须是名称严格以 `.csv.7z` 结尾的普通文件；允许解析后指向普通
文件的 symbolic link，拒绝目录、断链 symbolic link 和其他非普通文件。该 archive 的
有效 payload member 名称必须等于 archive basename 去掉最后一个 `.7z`；例如
`SZ_Trade.csv.7z` 的有效 member 是 `SZ_Trade.csv`。读取只提取该精确 member，忽略
archive 中的其他 member；有效 member 不存在时必须按缺少 CSV header 失败，不得选择
第一个 member、拼接多个 member 或根据内容猜测身份。

生产读取必须使用参数列表执行：

```text
<executable> x -so -spd -bd -bb0 -bso0 -bsp0 -bse2 -- <archive> <member>
```

进程必须使用 `stdin=DEVNULL`、`stdout=PIPE`，并继承当前进程 stderr。低层 reader 不得
接收 logger、捕获或丢弃 7z stderr，也不得自行记录异常。只有退出码 `0` 表示完整成功；
任意非零退出码都必须使读取失败。

### 禁止生产代码执行完整 archive 校验

生产代码不得执行 `7zz t`、`7za t`、`7z t` 等完整 archive 校验。

raw ingest 只提交已存在 payload 的对象级 meta，不通过 7z CLI 预先证明 archive 可以
完整解压。后续流式读取发生解压、读取或子进程失败时，流程必须直接失败。

### Reader API、资源与错误边界

`src.utils.csv7z_batch_source.open_csv7z_batches` 是唯一 public reader。它必须返回只能在
其 context manager 作用域内单次消费的 `pyarrow.RecordBatch` iterator，并拥有 7z
子进程、stdout 和 Arrow reader 的完整生命周期。自然耗尽必须在返回 `StopIteration`
前等待并校验 7z 退出码；提前结束是正常 abort，必须直接 kill 并回收子进程。该作用域
必须在正常退出、`Exception`、`KeyboardInterrupt`、`SystemExit` 和 `GeneratorExit`
路径释放资源，并原样传播读取方异常和 traceback；内部异常翻译只捕获明确的
`Exception` 子类。

存在读取方主异常时，cleanup failure 必须按发生顺序逐个作为 note 附加，不得替换主
异常。没有主异常时，任意 cleanup failure 必须产生一个 `RuntimeError`，以第一个
cleanup failure 作为 cause，并把全部 cleanup failure 按发生顺序写入 note。EOF 或 kill
后的进程回收最多等待 5 秒，该限制不约束 archive 解压时间，也不是性能 SLA。

API 类型错误使用 `TypeError`；后缀、header、BOM 位置无效或 PyArrow 拒绝 CSV body
时使用 `ValueError`；文件不存在或不是普通文件使用 `FileNotFoundError`；CLI 缺失、
进程启动、读取、退出或资源回收失败使用 `RuntimeError` 并保留原始 cause。该 reader
不定义或校验业务字段语义。异常类型、原始 cause 和失败类别属于 API 契约；异常消息与
note 的精确文字不属于契约，但必须指出失败对象和原因。

普通单元测试必须使用可控 process double 验证项目拥有的输入、命令、解析配置、退出码
和资源生命周期。真实 `7zz` 提取属于显式标记的 contract test；其环境必须提供 `7zz`，
并验证精确 member、流式读取和退出码。普通单元测试不得根据宿主环境有无 `7zz` 改变
覆盖范围。

### FTP transport 与下载进度

Level-2 FTP transport 由 `src.data_system.brokers.level2.Level2Broker` 拥有，包括 endpoint、
session、远端目录和文件选择、`.part` 续传、大小校验、staging 发布及资源释放。
`src.utils.download_utils.DownloadProgress` 只累计字节并记录进度，不发起网络请求、不拥有
文件、不执行重试或发布。调用方必须显式提供 logger；monotonic clock 可以注入以支持
可重复测试。

当前唯一 backend 是 Python 标准库 `ftplib`，配置必须显式选择 `ftplib`。一次 fetch 使用
两个独立 session：15 秒 control probe 用于翻译本机 route、TUN、proxy 或 firewall 导致的
连接超时；1500 秒下载 session 执行 login、目录查询、size 和 `retrbinary()`。两个 session
在成功、no-data 和失败路径都必须关闭，连接或登录失败时也不得泄漏创建中的 session。

下载目标必须先由 `PathManager.staging_payload(...)` 生成，并使用同目录
`<payload>.part -> <payload>`：

- 远端 size 必须是正整数；缺失、非整数或空文件必须失败；
- 已有 staging 与远端同尺寸时直接复用；已有 staging 小于远端且不存在 `.part` 时，移动
  为 `.part` 后续传；
- `.part` 与远端同尺寸时直接发布；大于远端时删除并重新下载；
- `retrbinary(..., rest=offset)` 从 `.part` 大小继续，block size 固定为 1 MiB；
- 每个 chunk 必须先写入 `.part`，再更新 `DownloadProgress`；
- 完整 payload 必须 flush、fsync、严格校验 size、原子替换 staging，并 fsync staging 目录；
- 下载失败时保留非空 `.part`，清理空 `.part`；
- 只有全部预期字节已经落地时，FTP control response 收尾 timeout 才能在 fsync 和严格
  size 校验后视为完成。

staging 到 raw 的复制不属于 FTP transport；`Level2Broker` 在下载完成后调用
`FileSystem.copy_file_atomic(staging, raw)`。只有 source adapter 可以把远端日期目录 FTP
`550`、空目录或期望文件不存在翻译为 `None`。认证、连接、timeout、size、同名多匹配、
写入或最终 size mismatch 必须失败。

FTP transport 和进度日志分别使用 `[Level2Broker]`、`[FTP]` 或 `[DownloadProgress]`
prefix，可以记录日期、文件名、backend、size 和进度，不得记录密码、token 或完整凭证。
Broker 测试拥有文件选择、续传、staging/raw 发布、no-data、失败和 session 释放边界；
`DownloadProgress` 测试拥有间隔、百分比、速度、ETA、未知总量、单位和参数拒绝边界。

### Normalize 的物理输入选择

Normalize 的正式输入身份和 lineage 始终来自已提交的 raw Meta。为避免 Level-2 大文件
从较慢介质重复读取，normalize 可以构造同 broker、source、trade date 和 payload
basename 的 staging candidate；仅当 candidate 是普通文件且字节数与正式 raw payload
完全相同时，才读取 staging。Candidate 不存在或字节数不同时必须读取正式 raw。

该选择只比较 size，不执行 hash、逐字节或 archive 内容校验，也不把 staging 写入
lineage。实际读取、解压或解析失败必须继续传播，不得回退后把损坏输入伪装成成功。

### Arrow codec 的适用边界

Apache Arrow 官方 cookbook 中的以下路径只适用于 Arrow 支持的压缩 codec，例如
`gzip`、`bz2`、`brotli`、`lz4` 和 `zstd`：

```python
pa.CompressedInputStream(pa.OSFile(...), "gzip") -> pa.csv.read_csv(...)
```

Level-2 source-native payload 是 `.csv.7z`。Arrow `CompressedInputStream` 不支持
`7z`，`pyarrow.csv.read_csv("*.csv.7z")` 也不会自动解压 7z。因此，`.csv.7z`
主链路必须使用 7z-compatible CLI 将内容流式解压到 stdout，再交给 Arrow CSV reader。

不得为了套用 Arrow gzip 示例而先把 `.csv.7z` 解压为中间 CSV，再由 Level-2
normalize 读取。

### Source-native CSV 解析契约

7z stdout 中的整个 CSV 必须使用 UTF-8，且只允许文件开头的 UTF-8 BOM。header 必须
是第一个物理 CSV 行，必须恰好包含一行非空、无首尾 whitespace 且互不重复的列名，
不得在 quoted column name 中包含 CR 或 LF。header 的 1 MiB 上限包含行终止符；超过
上限、缺失、编码无效或结构无效时读取必须失败。

source-native reader 必须保留所有列，并在该边界统一解析为 Arrow string。
项目接受当前配置下 PyArrow 对 CSV body 的宽松 quoting 解析，不额外预校验。
CSV body 必须直接交给 `pyarrow.csv.open_csv`，使用 comma delimiter、double quote
quoting、`double_quote=True`、禁用 escape character、`newlines_in_values=False` 和
`ignore_empty_lines=False`。该 reader 也不得在 PyArrow 前另行校验 record grammar 或
空物理行；PyArrow 在上述配置下接受、修正或拒绝 body 的行为就是该边界的解析语义。
只有 header、没有任何 body byte 时，低层 reader 返回空 iterator，由 normalize 决定
是否提交 processed 数据。

以下 token 精确表示 null，quoted token 也使用同一规则：

```text
<empty string>
<one ASCII space>
NULL
N/A
nan
```

除上述精确 token 外，不得在该低层 reader 中自行 trim、大小写折叠、数值转换或新增
其他 null 别名。业务字段类型、范围和跨字段约束必须由下游 source adapter 的正式
schema owner 校验；低层 reader 不得根据样本内容推断业务类型。

## 选择 `7zz` 作为主链路 CLI 的依据

当前 Level-2 大文件流式解压实测结果如下，单位为秒：

| 工具 | SSD 真实落盘 | HDD 真实落盘 | 判断 |
| --- | ---: | ---: | --- |
| `7zz` | `22.53` | `158.98` | 主链路最佳 |
| `7za` | `24.18` | `157.12` | 可 fallback |
| `7z` | `24.67` | `158.64` | 可 fallback |
| `py7zr` | `116.62` | `198.01` | 不适合主链路 |

决策依据如下：

- `7zz` 在 SSD 真实落盘场景最快。HDD 真实落盘场景中，`7zz`、`7za` 和 `7z`
  差异很小，主要瓶颈是机械盘写入，而不是解压器本身。
- `7zz` 支持 `x -so` 流式输出，Python 可以通过 `subprocess` 稳定接管 stdout，
  再交给 Arrow streaming CSV reader 处理。
- `py7zr` 在 SSD 和 HDD 真实落盘场景中均明显更慢，不适合作为每日 Level-2
  大文件生产链路的主工具。
- `7za` 和 `7z` 仅用于运行环境没有 `7zz`、但仍具备 7z-compatible CLI 的场景。

## 流式读取与中间 CSV 落盘对比

实测样本：

| 样本 | 压缩文件 | 解压后 CSV | 行数 |
| --- | ---: | ---: | ---: |
| `/home/wsw/data/raw/level2_ftp/sz_trade/trade_date=2026-04-30/SZ_Trade.csv.7z` | `1,550,591,104` bytes | `14,002,076,302` bytes | `148,942,781` |

对比结果：

| 路径 | 耗时 | 判断 |
| --- | ---: | --- |
| `7za x -so -> pyarrow.csv.open_csv` | `52.998` 秒 | 当前主链路，直接流式读取 |
| `7za x` 解压 CSV 落盘 | `14.633` 秒 | 只生成中间 CSV |
| `pyarrow.csv.open_csv` 读取已落盘 CSV | `45.182` 秒 | 只读取中间 CSV |
| 先落盘再读取合计 | `59.815` 秒 | 比流式路径慢 `6.817` 秒，约慢 `12.9%` |

该结果只覆盖输入解压与 CSV parse 环节，不覆盖后续 Level-2 parse、normalize、排序、
enrich 或 Parquet 写入。该基准是当前技术选型的实测依据，不是跨环境的性能 SLA。

结论是：即使在 SSD 上，中间 CSV 落盘也会增加额外写入、磁盘占用和清理风险；在
HDD 或跨 filesystem 场景下，该成本会进一步放大。因此，Level-2 normalize 必须直接
流式读取 `.csv.7z`，不得先生成中间 CSV。
