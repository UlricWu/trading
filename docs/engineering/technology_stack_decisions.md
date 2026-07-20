# 技术栈决策

- **状态**：强制执行
- **适用范围**：项目日志实现，以及 Level-2 source-native `.csv.7z` raw payload
  的 ingest、读取、归一化和转换流程。
- **用途**：记录已经明确选定的技术栈、生产约束及其决策依据。改变本文中的技术
  选型或约束前，必须先更新本文并补充新的验证依据。
- **规范词**：本文中的“必须”“不得”“仅”均为硬约束，不表示建议。

## 日志技术栈

Loguru 是项目明确选定的日志技术栈。项目自有日志实现必须以 Loguru 为基础；不得
在未更新本决策文档的情况下引入另一套并行的日志技术栈。

## Level-2 source-native `.csv.7z`

### 7z CLI 发现顺序与失败语义

读取 source-native `.csv.7z` raw payload 的低层工具必须按以下顺序发现 7z-compatible
CLI：

1. `7zz`
2. `7za`
3. `7z`

`7zz` 是主链路首选，`7za` 和 `7z` 仅作为 fallback。未发现可用命令时，归一化或
转换流程必须失败，不得写入空的 processed 数据，也不得提交 processed `meta.json`。

### 禁止生产代码执行完整 archive 校验

生产代码不得执行 `7zz t`、`7za t`、`7z t` 等完整 archive 校验。

raw ingest 只提交已存在 payload 的对象级 meta，不通过 7z CLI 预先证明 archive 可以
完整解压。后续流式读取发生解压、读取或子进程失败时，流程必须直接失败。

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

7z stdout 中的 CSV header 必须使用 UTF-8（允许 UTF-8 BOM），必须恰好包含一行
非空、无首尾空格且互不重复的列名。header 超过 1 MiB、缺失、编码无效或结构无效时
读取必须失败。

source-native reader 必须保留所有列，并在该边界统一解析为 Arrow string。CSV quoting
按标准 CSV 语义处理。以下 token 精确表示 null，quoted token 也使用同一规则：

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
