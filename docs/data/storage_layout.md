# 正式存储布局契约

- **状态**：强制执行
- **适用范围**：本地正式存储根目录，以及 raw、staging、processed、features、
  labels、experiments 六个顶层命名空间的路径布局。
- **Owner 边界**：本文拥有路径身份、固定文件名、路径段校验、object-side Meta
  schema、payload identity 和直接 upstream 关系；特定数据集的 index、数据转换和
  数据质量规则由各自 owner 拥有。

## 存储根目录

`storage_root` 必须由 composition root 以 `pathlib.Path` 显式提供，必须是已存在的
绝对目录。composition root 可以从 `ZERO_STORAGE_ROOT` 读取并转换该值；
`PathManager` 不读取环境变量，也不得从仓库位置、用户目录或当前工作目录推导 root。

`PathManager` 构造时对 root 执行一次 `resolve(strict=True)`，后续路径全部绑定该物理
目录。构造同时确保以下固定顶层目录存在：

```text
raw/
staging/
processed/
features/
labels/
experiments/
```

构造不创建 `storage_root` 本身，也不创建数据分区、experiment 或 artifact 子目录。

## 路径段与日期分区

调用方提供的 broker、source、dataset、feature set、label set、version、experiment 和
payload filename 必须是非空、无首尾空格的单一 basename，不得是 `.` 或 `..`，不得
包含 `/`、`\` 或 NUL。除此之外通用 basename 校验不限制字符集。CLI 和 Job API 使用的
experiment ID 另须匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`。

交易日期必须是合法且规范化的 `YYYY-MM-DD`，分区名固定为：

```text
trade_date=YYYY-MM-DD
```

`trade_calendar` 是唯一年度分区对象。日历年份必须是 `1..9999` 的整数，分区名固定为：

```text
year=YYYY
```

## Raw 与 staging

```text
raw/<broker>/<source_name>/trade_date=<date>/<payload_file>
raw/<broker>/<source_name>/trade_date=<date>/meta.json

staging/<broker>/<source_name>/trade_date=<date>/<payload_file>
```

`trade_calendar` 不使用单日 raw 或 staging 分区，固定为：

```text
raw/tushare/trade_calendar/year=<YYYY>/data.parquet
raw/tushare/trade_calendar/year=<YYYY>/meta.json
```

`payload_file` 是 source-native 身份的一部分，调用方必须显式提供，PathManager 不提供
默认文件名。每个单日 `(broker, source_name, trade_date)` 或年度
`(broker, source_name, year)` raw partition 只有一个由同目录 `meta.json` 描述的正式
payload。除下述 Level-2 网盘记录外，Meta 的 `payload` basename 必须指向该同目录文件。

`staging` 是 raw ingest 的操作性暂存层，不进入正式 lineage。Normalize 可以在同名
staging payload 是普通文件且字节数与正式 raw payload 相同时读取 staging；不存在或
字节数不同时读取正式 raw。该选择不改变 raw 身份或 lineage。Level-2 采用下述网盘存储。

### Level-2 网盘 raw 与本地缓存

`raw/level2_ftp/<source_name>/trade_date=<date>/meta.json` 保留既有 source identity，
迁移后只记录一份压缩 raw 的网盘位置，不要求同目录存在 payload。schema 精确为：

```json
{
  "payload": "SZ_Trade.csv.7z",
  "size_bytes": 1774433210,
  "remote_path": "/wsw/level2_data/2026-06-08/SZ_Trade.csv.7z"
}
```

`remote_path` 是规范绝对 POSIX 路径，basename 必须等于 `payload`；`size_bytes` 是正整数。
此 schema 仅允许上述 Level-2 单日 raw 分区。同 broker、source、日期、文件名和字节数
相同即视为同一内容；不增加内容摘要、版本库或备份前置条件。Processed 的直接 upstream
仍指向同一 raw Meta，读回时只校验网盘记录的 schema 和已记录大小，不查网盘或本地 cache。
Tushare 和所有其他本地对象继续要求其实际 payload 存在且大小匹配。

本地压缩 cache 唯一位置是既有 staging 分区；`.download/` 保存客户端断点状态。
同一 storage root 的 Level-2 下载、标准化、迁移和清理通过
`staging/level2_ftp/.cache.lock` 的进程锁串行；锁忙直接失败。Processed 只读不取锁。
缓存默认保留最近 5 个已完成所选逐笔输出的交易日期，日期数由配置调整。清理只针对窗口
外已完成且已有网盘 Meta 的完整 cache；完成检查包括逐笔 Meta 指向同日、同 source 的
raw lineage。每次删除前重新确认网盘同路径、同大小；失败、
未完成和未迁移的文件保留。日期完成后即可清理，不等整个历史区间结束。

`scripts/migrate_level2_archive.py` 默认仅预检；`--apply` 把已找到同尺寸网盘文件的本地
raw Meta 转为上述记录，在 `staging/level2_ftp/.migration-meta/<source>/<partition>/meta.json`
保留原始 Meta 备份，既有 processed payload/Meta 均不改写。
`--cleanup` 必须同时指定 `--apply`，才清理已归档 raw 重复文件及超出保留窗口的 cache；
近期有输出的 raw 按需移至 cache，跨文件系统用原子复制后删除源文件。`SZ_Order` 无本地
输出，归档确认后无需缓存。网盘缺失、尺寸不匹配或无对应配置的分区只报告并保留，可在
网盘补齐后重跑。2026-10-02 用户确认历史逐日文件目前主要覆盖九月，其他日期将补齐；
不能将整月 ZIP 推断为逐日 archive 的替代证据。

切换顺序：先按发布 owner 部署兼容本地和网盘两类 raw Meta 的代码，确认所有读取此根的
服务、定时任务和人工入口均已切换，旧 ingest writer 已停止；再独立执行预检、Meta 迁移
和清理。普通代码部署不自动执行迁移。回滚旧代码前，必须把所需 raw 从网盘恢复为同目录
文件，并用备份还原本地 Meta；不能直接让不支持 `remote_path` 的旧程序读取已迁移分区。

## Processed、features 与 labels

```text
processed/<dataset_name>/<version>/trade_date=<date>/data.parquet
processed/<dataset_name>/<version>/trade_date=<date>/meta.json

features/<feature_set>/<version>/trade_date=<date>/data.parquet
features/<feature_set>/<version>/trade_date=<date>/meta.json

labels/<label_set>/<version>/trade_date=<date>/data.parquet
labels/<label_set>/<version>/trade_date=<date>/meta.json
```

`trade_calendar` 不使用单日 processed 分区，固定为：

```text
processed/trade_calendar/v1/year=<YYYY>/data.parquet
processed/trade_calendar/v1/year=<YYYY>/meta.json
```

PathManager 只返回完整路径，不读取 Parquet、不检查列、不判断数据质量，也不创建业务
分区。正式对象由 payload 和同目录有效 `meta.json` 共同构成；正式消费者必须先通过
Meta `require()` 取得已校验的 payload，不得因规范位置单独存在 `data.parquet` 而直接
消费。
processed 市场数据由 Access 集中执行该读取；feature 和 label 消费者直接使用同一个
Meta API，不建立第二套对象校验。

`processed/<dataset_name>/<version>/` 是实际分区扫描所需的正式目录身份，由
`PathManager.processed_version_dir()` 返回。调用方不得从 processed root 手工拼接该路径。
规范文件名固定的 processed、feature 和 label 对象由 `PathManager` 一次返回不可变的
`ObjectPaths(payload_path, meta_path)`；调用方不得以两次路径调用分别重建同一对象身份。
对应入口为 `processed_object()`、`processed_year_object()`、`feature_object()` 和
`label_object()`。Raw payload 的 source-native basename 必须由来源显式提供，因此 raw
payload 与 Meta 继续分别由 `raw_payload()`/`raw_meta()` 或
`raw_year_payload()`/`raw_year_meta()` 返回。

## Object-side Meta

每个 `meta.json` 只描述同目录的一个 payload。顶层 schema 精确为：

```json
{
  "payload": "data.parquet",
  "size_bytes": 123,
  "upstream": {
    "meta_path": "raw/broker/source/trade_date=2026-07-15/meta.json",
    "size_bytes": 456
  },
  "symbol_slices": {
    "600000": {
      "start": 0,
      "end": 123
    }
  }
}
```

`payload` 和 `size_bytes` 必须存在；`upstream` 和 `symbol_slices` 只在适用时存在；
不得出现其他字段（Level-2 网盘 raw 使用上一节的独立 schema）。`payload` 必须是安全
basename，并且对应同目录普通文件。
`size_bytes` 必须是非负整数且不得是布尔值。当前 payload identity 只比较文件字节数：
实际字节数等于 `size_bytes` 即视为 payload 未变，同尺寸内容替换仍可复用。

`upstream` 只表示一个直接输入，schema 精确为 `meta_path` 和 `size_bytes`。
`meta_path` 必须是 `storage_root` 下 `meta.json` 的 POSIX 相对路径；读取方校验该直接
Meta 的 schema、payload 和实际字节数（网盘 raw 比较已记录大小），并与记录的
`size_bytes` 比较，但不得递归校验
更上游。Level-2 股票分钟事实按其领域 owner 记录同交易所同日逐笔对象的一个直接 upstream；
Feature 和 label 当前没有 upstream，不写该字段。

`symbol_slices` 只用于 `sh_trade` 与 `sz_trade` 两个 Level-2 Normalize processed payload，
其 schema 和不变量由 `docs/data/level2_normalization.md` 拥有。Level-2 股票分钟事实和其他
payload 不写该字段；分钟对象的 lineage 由
[`docs/data/level2_minute_contract.md`](level2_minute_contract.md) 拥有。

只有 `meta.json` 不存在表示对象尚未产出。Meta 已存在但 JSON、schema、payload、
payload 字节数、直接 upstream 或 symbol slice 无效时必须失败，不得降级为未产出。
producer 必须先发布 payload，再原子写入 `meta.json`；该顺序不构成多文件事务，也不
定义并发写协调。

`src/access/meta.py` 公开本地对象的 `MetaRecord`、`find()`、`require()`、`commit()`，
以及 Level-2 来源的 `RemoteRawRecord`、`find_level2_raw()`、`commit_remote_raw()`。
`find_level2_raw()` 在唯一允许的 Level-2 raw 路径读取网盘记录或待迁移的本地记录；
后者仍校验本地文件。`commit_remote_raw()` 原子写网盘记录，不执行网络 I/O。
`find()`、`require()` 不把远端记录当成本地 payload 返回。
`MetaRecord` 表示一个完整的 object-side Meta 记录，不另建表示加载阶段的结果类型。
`find()` 只供 producer 探测可选输出：`meta.json` 不存在时返回 `None`，存在时校验上述
契约。`require()` 供 consumer 取得必要对象：Meta 不存在时直接以
`FileNotFoundError` 失败。两者返回的记录包含 resolved payload path、已记录字节数、
可选直接 upstream 和可选 symbol slices。`commit()` 在 payload 已完成后原子发布同目录
`meta.json`。三个操作都接收已经绑定正式 storage root 的 `PathManager`，不建立第二个
root 身份。Meta 不记录日志。

## Raw Meta 一次性迁移

随代码版本交付的 `scripts/migrate_raw_meta.py` 是旧 raw Meta 到当前 Meta schema 的
一次性人工迁移入口，不属于 `python -m src.cli`、HTTP Job API、定时任务或日常 offline
data workflow。调用方必须显式提供已存在的绝对 `storage_root`；默认只执行完整预检，
只有显式 `--apply` 才允许发布：

```text
python -m scripts.migrate_raw_meta --storage-root /absolute/storage/root
python -m scripts.migrate_raw_meta --storage-root /absolute/storage/root --apply
```

迁移只扫描 `raw/` 下已经存在 `meta.json` 的单日分区和 `trade_calendar` 年度分区。
扫描单日分区包括旧项目遗留的 `trade_calendar/trade_date=YYYY-MM-DD`；迁移这种 Meta
只统一其 object-side schema，不改变 payload 或分区，也不使该旧布局成为当前正式日历
对象。Meta 缺失仍表示对象尚未产出，脚本不得根据孤立 payload 创建 Meta。当前
`require()` 已通过的对象必须跳过。当前校验失败的对象只有同时满足以下条件才是
migratable：

- 旧 Meta 仍是无重复 key 的 JSON object，`version` 精确为 `V1.0`，`upstreams` 是空
  array，且不包含 raw 不适用的当前字段 `upstream` 或 `symbol_slices`；
- `output` 是 object，包含 string `path`；`output.fingerprint` 是 object，包含非负
  integer `size`；
- `output.path` 是规范化的绝对 POSIX path。旧 storage root 前缀可以与当前 root 不同，
  但从 `raw/` 开始的完整相对路径必须与当前 Meta 分区和 payload basename 完全一致；
- 该 payload 是旧 Meta 同目录下唯一的非 Meta sibling，是非 symlink 普通文件，实际
  文件字节数与 `output.fingerprint.size` 完全一致。

旧 Meta 可以包含不参与 identity 的其他字段；迁移只使用上述 payload identity，不得把
旧字段带入当前 Meta。JSON 损坏、identity 字段缺失或无效、payload 变化、额外 sibling、
symlink 或非上述 raw 路径都属于 blocked，不得被重建掩盖。预检存在任一 blocked 对象
时，`--apply` 不写任何 Meta 并以非零退出。

Apply 对每个 migratable payload 调用当前 `meta.commit()` 原子替换同目录 Meta，并立即以
`meta.require(expected_payload_path=...)` 终验；脚本不修改 payload。单个 Meta 原子发布，
整批迁移不是多文件事务；执行期间必须停止会写 raw 的 producer。脚本可重复运行，已经
完成的对象在后续运行中作为 current 跳过；运行输出必须分别汇总 current、migratable、
migrated 和 blocked 数量。该入口不自动接入 release 或 deploy，旧版本能否读取迁移后的
Meta 必须在实际部署迁移前单独确认。

## Experiments

`experiments/` 保存一次研究运行的当前正式现场。当前只有彼此独立的 training experiment
和 backtest experiment，不定义 train-to-backtest 组合 experiment。

```text
experiments/<experiment_name>/
├── training/
├── backtest/
└── report/
```

子目录按实际产物创建，不要求空目录。Experiment name 的生成规则由
`docs/offline_workflow_contract.md` 拥有；PathManager 只校验生成后的名称并返回完整路径。

当前 training experiment 的具体 artifact 为：

```text
experiments/<experiment_name>/
├── training/
│   ├── schedule.json
│   ├── windows/
│   │   ├── 0000/
│   │   │   ├── model.joblib
│   │   │   └── predictions.parquet  # 此窗口选择评价时才创建
│   │   └── 0001/ ...
│   └── metrics.json                # 实验选择评价时才创建
└── report/
    └── training_report.html        # 实验选择评价时才创建
```

Window index 是显式 schedule 中的零起始位置，目录使用至少四位十进制表示；每次训练
独立保存自己的模型。PathManager.experiment_training_model 返回该次绝对 ref，预测文件
固定在同一个 window 目录。模型的业务内容、预测返回与评价 schema 只由
[training workflow owner](../offline_workflow_contract.md#training-workflow)定义。
Schedule 保存任务所选日期、训练信息截止、可用依据、可选评价时序和每个输出 ref；
它是运行安排，不是成功发布确认，不是完整配置、代码或环境归档。

保存边界为 `src.training.artifact.save(ref: Path, model: InferenceModel) -> None`，加载边界
为 `load(ref: Path) -> InferenceModel`；两者都拒绝相对路径。ref 由训练运行选择，消费侧
使用同一个具体引用，实验名称不能代替模型身份。不另建 registry、latest 指针或外层字典。
Joblib 单个文件直接保存完整就绪对象，不存在独立 model/preprocess/params 文件。

Save 创建必要父目录，在目标同目录创建唯一临时文件，完成序列化并关闭文件后，通过
`os.link` 不覆盖发布，再删除临时文件。目标已存在时原样 FileExistsError，不覆盖、不选择
另一文件名；并发发布只有一个成功。FileSystem.atomic_path 的 replace 发布不满足此处的
不可覆盖身份保证，不用于模型。序列化、文件和依赖错误原样传播；恢复内容类型错误是
TypeError，不重新校验已由训练建立的来源字段。失败清理临时文件，清理错误不能隐藏原始
序列化或发布错误；发布后临时清理失败则向调用方报错，已发布的完整文件保留，不回滚或
自动重试。文件已经存在与 save 成功返回是不同事实。

每份 predictions.parquet 保留原始完整样本键，以及 int64 decision_ts_us、字符串
model_ref 和 nullable float64 prediction；输入顺序和行数不因 drop 或缺标签改变。
Parquet schema metadata 的 prediction_inputs 保存 model_ref、model_sha256、
feature_sha256、trade_date、decision_ts_us。模型摘要对完整文件字节计算；输入摘要对
实际同序 Feature 表去除 schema metadata 后的 Arrow IPC stream 计算，包含完整键、
列序、数值类型和内容。它检测变化，不归档输入。预测记录由使用它的运行选择路径，
模型存放目录不自动拥有后续所有预测；本轮每窗口训练布局保持上图。
Metrics 与 HTML 在模型交付后分别原子写入，不组成多文件事务。它们缺失或失败不表示
模型不可恢复；模型可加载也不表示评价、报告或整个 workflow 成功。

当前 backtest experiment 保留：

```text
experiments/<experiment_name>/
├── backtest/
│   ├── binding.json
│   ├── predictions/
│   │   ├── YYYY-MM-DD.parquet
│   │   └── ...
│   └── metrics.json
└── report/
    └── backtest_report.html
```

binding.json 仅在指定模型加载并通过使用时序核验后写入，保存具体 ref、模型内容摘要、
model_use、对应训练选择、首个决策时点、本次 mode、strategy 和静态 backtest_config。
实际事件时间沿用运行日志，effective_ts_us 只表示该任务的生效决策边界，不是发布时间。
每个 signal_date 拥有独立预测文件；不会写回 training 的 predictions.parquet，也不会
以最后一天覆盖前一天。预测文件与训练采用相同 schema；metrics.model_evaluation
保存模型目标评价，原 signal_eval 保留 T+1 raw-close 诊断。
Experiment 不另写 run_meta.json、输入副本或通用 replay metadata，不声称已归档完整
resolved config、代码版本、运行环境或历史输入修订事实。

PathManager 为已定义的 experiment、模型、schedule、metrics 与 report 提供精确方法；
window 内预测文件名由本节固定。不接受任意 experiment 文件名，也不
根据文件存在性判断 experiment 是否成功。

Training 或 backtest 在 artifact persist、metrics persist 或 report 阶段失败时，已经创建的
experiment 目录和制品保留为失败现场，不自动删除、回滚、续跑或恢复。残留目录不表示
experiment 成功，但会继续触发同名 experiment 的禁止覆盖规则。
