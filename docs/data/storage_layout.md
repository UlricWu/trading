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

## Raw 与 staging

```text
raw/<broker>/<source_name>/trade_date=<date>/<payload_file>
raw/<broker>/<source_name>/trade_date=<date>/meta.json

staging/<broker>/<source_name>/trade_date=<date>/<payload_file>
```

`payload_file` 是 source-native 身份的一部分，调用方必须显式提供，PathManager 不提供
默认文件名。每个 `(broker, source_name, trade_date)` raw partition 只有一个由同目录
`meta.json` 描述的正式 payload。Meta 的 `payload` basename 必须指向该同目录文件。

`staging` 是 raw ingest 的操作性暂存层，不进入正式 lineage。Normalize 可以在同名
staging payload 是普通文件且字节数与正式 raw payload 相同时读取 staging；不存在或
字节数不同时读取正式 raw。该选择不改变 raw 身份或 lineage。

## Processed、features 与 labels

```text
processed/<dataset_name>/<version>/trade_date=<date>/data.parquet
processed/<dataset_name>/<version>/trade_date=<date>/meta.json

features/<feature_set>/<version>/trade_date=<date>/data.parquet
features/<feature_set>/<version>/trade_date=<date>/meta.json

labels/<label_set>/<version>/trade_date=<date>/data.parquet
labels/<label_set>/<version>/trade_date=<date>/meta.json
```

PathManager 只返回完整路径，不读取 Parquet、不检查列、不判断数据质量，也不创建业务
分区。正式对象由 payload 和同目录有效 `meta.json` 共同构成；正式消费者必须先通过
Meta `require()` 取得已校验的 payload，不得因规范位置单独存在 `data.parquet` 而直接
消费。
processed 市场数据由 Access 集中执行该读取；feature 和 label 消费者直接使用同一个
Meta API，不建立第二套对象校验。

`processed/<dataset_name>/<version>/` 是实际分区扫描所需的正式目录身份，由
`PathManager.processed_version_dir()` 返回。调用方不得从 processed root 手工拼接该路径。

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
不得出现其他字段。`payload` 必须是安全 basename，并且对应同目录普通文件。
`size_bytes` 必须是非负整数且不得是布尔值。当前 payload identity 只比较文件字节数：
实际字节数等于 `size_bytes` 即视为 payload 未变，同尺寸内容替换仍可复用。

`upstream` 只表示一个直接输入，schema 精确为 `meta_path` 和 `size_bytes`。
`meta_path` 必须是 `storage_root` 下 `meta.json` 的 POSIX 相对路径；读取方校验该直接
Meta 的 schema、payload 和实际字节数，并与记录的 `size_bytes` 比较，但不得递归校验
更上游。Feature 和 label 当前没有 upstream，不写该字段。

`symbol_slices` 只用于 Level-2 processed payload，其 schema 和不变量由
`docs/data/level2_normalization.md` 拥有。其他 payload 不写该字段。

只有 `meta.json` 不存在表示对象尚未产出。Meta 已存在但 JSON、schema、payload、
payload 字节数、直接 upstream 或 symbol slice 无效时必须失败，不得降级为未产出。
producer 必须先发布 payload，再原子写入 `meta.json`；该顺序不构成多文件事务，也不
定义并发写协调。

`src/access/meta.py` 只公开 `MetaRecord`、`find()`、`require()` 和 `commit()`。
`MetaRecord` 表示一个完整的 object-side Meta 记录，不另建表示加载阶段的结果类型。
`find()` 只供 producer 探测可选输出：`meta.json` 不存在时返回 `None`，存在时校验上述
契约。`require()` 供 consumer 取得必要对象：Meta 不存在时直接以
`FileNotFoundError` 失败。两者返回的记录包含 resolved payload path、已记录字节数、
可选直接 upstream 和可选 symbol slices。`commit()` 在 payload 已完成后原子发布同目录
`meta.json`。三个操作都接收已经绑定正式 storage root 的 `PathManager`，不建立第二个
root 身份。Meta 不记录日志。

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
│   ├── preprocess.pkl
│   ├── model.pkl
│   ├── params.json
│   └── metrics.json
└── report/
    └── training_report.html
```

`model.pkl` 与 `preprocess.pkl` 必须属于同一次 training experiment；模型文件单独不构成
完整推理资产。

当前 backtest experiment 暂时只正式保留：

```text
experiments/<experiment_name>/
├── backtest/
│   └── metrics.json
└── report/
    └── backtest_report.html
```

当前不定义其他 backtest artifact。Backtest 运行时通过 `model_experiment` 选择 training
experiment，但当前不持久化该引用的 lineage。Experiment 不写 `run_meta.json`、`inputs/` 或
`predictions.parquet`，不得声称已保存完整 resolved config、代码版本、运行环境、随机性
策略或通用 replay metadata。

PathManager 为每个已定义 artifact 提供精确方法，不接受任意 experiment 文件名，也不
根据文件存在性判断 experiment 是否成功。
