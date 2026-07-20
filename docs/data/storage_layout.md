# 正式存储布局契约

- **状态**：强制执行
- **适用范围**：本地正式存储根目录，以及 raw、staging、processed、features、
  labels、experiments、registry 七个顶层命名空间的路径布局。
- **Owner 边界**：本文只拥有路径、固定文件名、路径段校验和必需 Parquet 的读取
  边界；字段 schema、metadata 内容、lineage、数据转换和发布资格由各自 owner 拥有。

## 存储根目录

`storage_root` 必须是调用方显式提供的、已存在的绝对目录。composition root 可以从
`ZERO_STORAGE_ROOT` 读取该值，但不得从仓库位置、用户目录或当前工作目录推导。

构造 `PathManager` 时必须确保以下固定顶层目录存在：

```text
raw/
staging/
processed/
features/
labels/
experiments/
registry/
```

该构造只创建固定顶层目录，不创建数据分区、实验或模型版本目录。

## 路径段与日期分区

broker、dataset、feature set、label set、version、experiment 和 model 等正式路径段
必须匹配：

```text
[A-Za-z0-9][A-Za-z0-9._-]{0,254}
```

交易日期必须是合法且规范化的 `YYYY-MM-DD`，分区名固定为：

```text
trade_date=YYYY-MM-DD
```

raw 和 staging payload 文件名必须是非空、无首尾空格的 basename，不得包含 `/`
或 `\`，也不得是 `.` 或 `..`。调用方可以选择安全 basename；默认文件名为
`data.parquet`。

## Raw 与 staging

```text
raw/<broker>/<source_name>/trade_date=<date>/<payload_file>
raw/<broker>/<source_name>/trade_date=<date>/meta.json

staging/<broker>/<source_name>/trade_date=<date>/<payload_file>
```

`staging` 是 raw ingest 的操作性暂存层；本文不授予把 staging 内容提交为 raw 的
资格。

## Processed、features 与 labels

```text
processed/<dataset_name>/<version>/trade_date=<date>/data.parquet
processed/<dataset_name>/<version>/trade_date=<date>/meta.json

features/<feature_set>/<version>/trade_date=<date>/data.parquet
features/<feature_set>/<version>/trade_date=<date>/meta.json

labels/<label_set>/<version>/trade_date=<date>/data.parquet
labels/<label_set>/<version>/trade_date=<date>/meta.json
```

`PathManager` 可以读取上述三个命名空间中的必需 `data.parquet`。文件缺失必须失败，
不得返回空表伪装缺失。feature 读取允许调用方投影一个无重复的字符串列序列；空列
序列表示读取全部列。请求列不存在时必须在读取前失败。具体表 schema 仍由对应数据
owner 管理，`PathManager` 不得推断、转换或补齐业务字段。

## Experiments

实验根目录为：

```text
experiments/<experiment_name>/
```

固定路径和允许文件如下：

```text
run_meta.json

inputs/
  feature_ref.json
  label_ref.json
  split.json
  model_ref.json
  market_data_ref.json
  backtest_config.json

training/
  preprocess.pkl
  model.pkl
  params.json
  metrics.json
  predictions.parquet

backtest/
  model_ref.json
  orders.parquet
  trades.parquet
  positions.parquet
  equity_curve.parquet
  metrics.json

report/
  training_report.html
  backtest_report.html
```

路径解析器不得接受表外文件名，也不得据此判断实验是否通过或可发布。

## Registry

```text
registry/<model_name>/<version>/preprocess.pkl
registry/<model_name>/<version>/model.pkl
registry/<model_name>/<version>/model_info.json
registry/<model_name>/<version>/source_experiment.json
```

这些路径只表达 released-model namespace 的正式位置。返回 registry 路径不等于模型
已经注册、发布或部署；相关状态转换由发布 owner 管理。
