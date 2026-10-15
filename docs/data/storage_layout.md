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
包含 `/`、`\` 或 NUL。除此之外通用 basename 校验不限制字符集。Experiment ID 的输入格式
由 [CLI 契约](../engineering/cli_contract.md#通用边界)拥有，并在 CLI 参数解析时校验；
PathManager 接收完整的 experiment name，不提供 ID 格式校验。

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

Training 使用下述 training/report 子目录；五种 backtest 模式均在实验根目录保存单账户结果。
子目录按实际产物创建，不要求空目录。Experiment name 的生成规则由
`docs/offline_workflow_contract.md` 拥有；PathManager 只校验生成后的名称并返回完整路径。

当前 training experiment 的具体 artifact 为：

```text
experiments/<experiment_name>/
├── training/
│   ├── schedule.json
│   ├── model.joblib
│   ├── predictions/                # 实验选择评价时才创建
│   │   ├── YYYY-MM-DD.parquet
│   │   └── ...
│   └── metrics.json                # 实验选择评价时才创建
└── report/
    └── training_report.html        # 实验选择评价时才创建
```

一次任务保存一个模型。PathManager.experiment_training_model(experiment_name=...)
返回这个绝对 ref；每个评价日有独立预测文件。模型的业务内容、预测返回与评价 schema 只由
[training workflow owner](../offline_workflow_contract.md#training-workflow)定义。
请求内部验证时，验证日期的预测也保存于 predictions 目录，IC 独立保存于
`validation-metrics.json`；`metrics.json` 仅包含最终评估。盘前评价另外保存
`selection-metrics.parquet`、`selections.parquet` 和 `selection-summary.json`，通过 stage
区分内部验证和最终评估，并写入已有 report 路径。业务公式由 training workflow owner 拥有。

单次拟合的 schedule.json 是一个 TrainingRunRecord JSON object，保存已校验请求、解析后的拟合、内部验证、隔离与最终评估
日期、实际模型/数据配置、输出 model_ref 和标签理论成熟下界，字段语义由上述 owner 定义。
它在训练前写入，记录运行安排，不是成功发布确认或历史可用性证明，也不归档代码、环境
及完整输入修订。

保存边界为 `src.training.artifact.save(ref: Path, model: InferenceModel) -> None`，加载边界
为 `load(ref: Path) -> InferenceModel`；两者都拒绝相对路径。ref 由训练运行选择，消费侧
使用同一个具体引用，实验名称不能代替模型身份。不另建 registry、latest 指针或外层字典。
Joblib 单个文件直接保存完整就绪对象，不存在独立 model/preprocess/params 文件。

Save 创建必要父目录，在目标同目录创建唯一临时文件，完成序列化并关闭文件后，通过
`os.link` 不覆盖发布，再删除临时文件。目标已存在时原样 FileExistsError，不覆盖、不选择
另一文件名；并发发布只有一个成功。FileSystem.atomic_path 的 replace 发布不满足此处的
不可覆盖身份保证，不用于模型。序列化、文件和依赖错误原样传播；恢复内容类型错误或缺少
当前 InferenceModel 字段时抛 TypeError。加载边界检查字段完整性，并在缺失时指出模型路径、
缺失字段及重新训练要求：旧 Joblib 对象可能仍通过类型检查，但反序列化不会为新增字段
补上构造默认值。不补字段、不改写模型，也不重新校验已由训练建立的来源字段。
失败清理临时文件，清理错误不能隐藏原始
序列化或发布错误；发布后临时清理失败则向调用方报错，已发布的完整文件保留，不回滚或
自动重试。文件已经存在与 save 成功返回是不同事实。

每份预测 Parquet 保留原始完整样本键，以及 int64 decision_ts_us、字符串
model_ref 和 nullable float64 prediction；输入顺序和行数不因 drop 或缺标签改变。
Parquet schema metadata 的 prediction_inputs 保存 model_ref、model_sha256、
feature_sha256、trade_date、decision_ts_us。模型摘要对完整文件字节计算；输入摘要对
实际同序 Feature 表去除 schema metadata 后的 Arrow IPC stream 计算，包含完整键、
列序、数值类型和内容。它检测变化，不归档输入。预测记录由使用它的运行选择路径，
模型存放目录不自动拥有后续所有预测；本轮逐日训练评价布局如上图。
Metrics 与 HTML 在模型交付后分别原子写入，不组成多文件事务。它们缺失或失败不表示
模型不可恢复；模型可加载也不表示评价、报告或整个 workflow 成功。

五种 backtest 模式的 experiment 按实际能力保留：

```text
experiments/<experiment_name>/
├── predictions/<YYYY-MM-DD>/<ts_us>.parquet  # 本次实际推理才创建
├── orders.json                             # 完整委托/撤单输入流
├── targets.json                            # 风控前策略目标，execution 为空
├── risk-cases.json                         # 实际运行业务风控时保存案例
├── execution.parquet                       # 执行与结算事实
├── equity.parquet                          # 事件估值
├── valuation-marks.parquet                 # 初始与日终持仓的估值价格依据
├── metrics.json
└── report.html
```

risk_rule_eval 与其他模式使用同一布局；metrics 保存本账户的原始请求、预测引用、
完整评价和 applied_risk，风控有效性放在 risk_effectiveness 中，字段语义由
[Offline 工作流](../offline_workflow_contract.md#风控有效性评价)拥有。无业务风控时
applied_risk 为 null；risk/full 保存实际规则。不生成隐式基线目录或根对照结果。

五种回测统一使用 `backtest_{today}_{experiment_id}`，today 为 CLI 完成请求解析和配置加载后的
Asia/Shanghai 当天日期，格式为 `YYYY-MM-DD`；日期采样与唯一 ID 责任由
[Offline 工作流](../offline_workflow_contract.md#共同边界)定义。目录不包含回测日期范围或
模式前缀，也不增加重复 backtest/report 子目录；
各模式只生成实际能力的制品，mode 和日期范围保存在 metrics 的 parameters 中。固定预测输入保留外部引用，不无条件复制。
同日多次推理按 UTC epoch 微秒 ts_us 区分，预测 schema 与训练相同，不写回 training。

metrics 同时保存实际评价、预测引用、运行参数及 data_quality 中的诊断标记和行情异常；
估值沿用的价格来源仍保存于 valuation-marks.parquet，不另建异常制品。也不另写 binding.json、run_meta.json、
通用 replay metadata 或全部输入副本。orders.json、targets.json、risk-cases.json、valuation-marks.parquet 是可复演
业务事实，不是第二份运行元数据。各文件 schema、原始顺序、空值和发布时机归
[Backtest workflow](../offline_workflow_contract.md#backtest-workflow)；本节唯一拥有布局和文件名。
HTML 由 metrics 生成，不拥有另一份结果。模型目标评价保存在 metrics.signal_eval，
不再使用旧 metrics.model_evaluation 加 T+1 raw-close 诊断形状。

训练记录仍在模型同目录 schedule.json；预测中的模型/特征摘要及指标中的标签摘要
不等于完整源码、环境、输入归档或模型历史可用性证明。PathManager 提供 experiment 根目录
及固定路径方法；`experiment_backtest_predictions_dir(experiment_name=...)` 统一返回本次
运行的 predictions 目录。Workflow 把根预测目录及本路径的输出目录传给对应 Step；预测按
交易日期与决策时间生成文件，事实、指标和报告在该路径的输出目录按本节固定名称生成。路径方法只返回路径，所需父目录由实际写入工具创建。不新增接受任意制品
名称的公共路径 API，不根据文件存在性判断任务成功。

新运行采用上述布局，历史结果保持原位，不自动移动或增加旧路径探测。旧 metrics 的
model_evaluation observations 含绝对 prediction_ref，直接移动预测目录会破坏引用；
若另行要求迁移，必须一并更新这些引用并重新生成报告，不能只移动目录。

Training 或 backtest 在 artifact persist、facts persist、metrics persist 或 report 阶段失败时，已经创建的
experiment 目录和制品保留为失败现场，不自动删除、回滚、续跑或恢复。残留目录不表示
experiment 成功。Training 继续拒绝同名实验；Backtest 按日期加 UUID/唯一 ID 不重名的
前提运行，不检测同名目录，重新发起运行使用新的 experiment_id。
