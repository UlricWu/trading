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
payload。Meta 的 `payload` basename 必须指向该同目录文件。

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
年度 raw 与 processed 路径分别由 `raw_year_payload()`、`raw_year_meta()`、
`processed_year_data()` 和 `processed_year_meta()` 返回。

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

## Raw Meta 一次性迁移

随代码版本交付的 `scripts/migrate_raw_meta.py` 是旧 raw Meta 到当前 Meta schema 的
一次性人工迁移入口，不属于 `python -m src.cli`、HTTP Job API、定时任务或日常 offline
data workflow。调用方必须显式提供已存在的绝对 `storage_root`；默认只执行完整预检，
只有显式 `--apply` 才允许发布：

```text
python -m scripts.migrate_raw_meta --storage-root /absolute/storage/root
python -m scripts.migrate_raw_meta --storage-root /absolute/storage/root --apply
```

迁移只扫描 `raw/` 下已经存在 `meta.json` 的正式单日分区和 `trade_calendar` 年度分区。
Meta 缺失仍表示对象尚未产出，脚本不得根据孤立 payload 创建 Meta。当前 `require()` 已
通过的对象必须跳过。当前校验失败的对象只有同时满足以下条件才是 migratable：

- 旧 Meta 仍是无重复 key 的 JSON object，包含 string `payload` 和非负 integer
  `size_bytes`，且不包含 raw 不适用的 `upstream` 或 `symbol_slices`；
- `payload` 是旧 Meta 同目录下唯一的非 Meta sibling，是非 symlink 普通文件；
- payload basename、正式 raw 分区路径和实际文件字节数与旧 Meta 记录完全一致。

旧 Meta 可以包含已由当前 schema 删除的其他顶层字段；迁移只使用上述 payload identity，
不得把旧字段带入当前 Meta。JSON 损坏、identity 字段缺失或无效、payload 变化、额外
sibling、symlink 或非正式 raw 路径都属于 blocked，不得被重建掩盖。预检存在任一
blocked 对象时，`--apply` 不写任何 Meta 并以非零退出。

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
│   ├── params.json
│   ├── metrics.json
│   └── inference.pkl
└── report/
    └── training_report.html
```

`inference.pkl` 是一个已经可用的 `InferenceModel`，同时包含原始预测模型、唯一的已拟合
预处理对象及 feature set/version 身份。预处理对象同时拥有实际训练列顺序、拟合状态和唯一
的 `transform` 实现；不存在独立的 `model.pkl` 或 `preprocess.pkl`。

Training artifact publish 依次原子写入 `params.json`、`metrics.json`，最后原子发布
`inference.pkl`。`inference.pkl` 存在且可加载只表示推理资产已经就绪；它不表示 report
成功，也不表示整个 workflow 成功。报告只读取经过同一 schema 边界校验的 params 与
metrics。

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

Training 或 backtest 在 artifact persist、metrics persist 或 report 阶段失败时，已经创建的
experiment 目录和制品保留为失败现场，不自动删除、回滚、续跑或恢复。残留目录不表示
experiment 成功，但会继续触发同名 experiment 的禁止覆盖规则。
