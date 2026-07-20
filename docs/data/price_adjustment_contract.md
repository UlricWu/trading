# 价格复权契约

- **状态**：强制执行
- **适用范围**：对同一输入价格表生成 raw、前复权（qfq）和后复权（hfq）价格视图。
- **Owner 边界**：本文拥有复权公式、输入字段约束、as-of 锚点和输出所有权；复权因子
  的获取、供应商映射、停牌推断和文件 I/O 不属于本文。

## 输入契约

输入必须是 `pandas.DataFrame`。调用方必须显式提供：

- `adjustment`：`raw`、`qfq` 或 `hfq`；
- `asof_date`：规范且合法的 `YYYY-MM-DD`；qfq 使用该日期，raw 和 hfq 不使用该值
  参与计算；
- `price_columns`：无重复的非空字符串序列，且每个列名必须存在；
- `output_prefix`：字符串；空字符串表示覆盖返回副本中的选定价格列，非空字符串
  表示写入新的 `<output_prefix><price_column>` 列。

qfq 和 hfq 必须包含 `adj_factor`。qfq 还必须包含：

- `symbol`：每行均为非空字符串；
- `trade_date`：每行均为规范且合法的 `YYYY-MM-DD`；
- 每个输入 symbol 在 `asof_date` 恰好有一行复权因子。

缺少 symbol 的 as-of 因子或同一 symbol 存在多个 as-of 因子时必须失败，不得使用
其他日期或其他 symbol 的因子补齐。

## 计算公式

对行 `i`、选定价格 `p_i` 和该行因子 `f_i`：

```text
raw_i = p_i
hfq_i = p_i * f_i
qfq_i = p_i * f_i / f_symbol(asof_date)
```

raw 必须原样复制价格值，不解释数值有效性。hfq 和 qfq 中，价格、行因子或 as-of
因子无法转换为数值或不大于零时，对应调整价格必须为 null；不得把无效值替换为零、
前值或其他 symbol 的值。

## 输出与所有权

函数不得修改调用方持有的 DataFrame，必须返回独立顶层副本。未选中的列、索引和行序
保持不变。

- `output_prefix=""`：raw 保持选定价格列不变；qfq/hfq 在返回副本中覆盖选定列。
- 非空 `output_prefix`：三种 adjustment 都在返回副本中写入带前缀的新列，原价格列
  保持不变。

本契约只定义表内转换，不授权读取因子、拼接其他 DataFrame、写文件或推断停牌和除权
事件。
