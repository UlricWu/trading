# 跨后端表检测契约

- **状态**：正式 owner
- **适用范围**：`src.utils.table_ops` 对 `pyarrow.Table` 与
  `pandas.DataFrame` 提供的共同只读检测语义。
- **非适用范围**：数据集 key、schema、字段格式、业务空表规则、跨表对齐、scalar
  参数以及 Arrow/Pandas 专属数据操作。

## 公共 API

公共 API 固定为：

```python
from src.utils import table_ops

table_ops.require_columns(data, ("symbol", "close"), who="daily_bar")
table_ops.require_nonempty(data, who="daily_bar")
table_ops.require_non_null(data, ("symbol",), who="daily_bar")
table_ops.require_nonempty_strings(data, ("symbol",), who="daily_bar")
table_ops.require_unique(data, ("symbol",), who="daily_bar")
table_ops.require_finite(data, ("close",), who="daily_bar")
table_ops.require_positive(data, ("close",), who="daily_bar")
```

只接受 `pyarrow.Table` 与 `pandas.DataFrame`，不接受 `RecordBatch`、Series、duck
typing、自动转换或注册扩展。成功返回 `None`。

`who` 必须是非空字符串。`columns` 必须是非空、无重复的字符串 `Sequence`；字符串
本身、bytes、非 `Sequence` 或非字符串元素不构成合法 `columns`。

参数和表内容按以下顺序检测：

```text
data 类型 -> who -> columns 参数 -> 表中列身份 -> 数据谓词
```

## 逻辑数据等价

逻辑表由行数、列名出现次数以及每个唯一列名对应的有序逻辑值组成。列的物理顺序、
Pandas index、dtype 宽度、categorical metadata、Arrow chunk、schema metadata、
dictionary 编码及字典顺序均不属于逻辑身份。

列名大小写敏感，不做规范化。共同逻辑标量为：

| 逻辑值 | 物理表示 |
| --- | --- |
| missing | Arrow null、`None`、`pd.NA`、`pd.NaT`、浮点 NaN |
| string | Python/Pandas string、Arrow string/large_string、dictionary/categorical string |
| boolean | Python、NumPy、Pandas、Arrow boolean |
| number | 非 boolean 的整数与浮点数 |

数值使用精确相等：`1 == 1.0`、`0.0 == -0.0`；正负无穷只与同符号无穷相等。不使用
容差、舍入或字符串解析。boolean 与 number 分属不同逻辑类型。Decimal、complex、
bytes、日期对象和嵌套值当前不属于共同 key 类型；日期字段继续使用其现有字符串身份。

对相同逻辑表、相同函数参数，Pandas 与 Arrow 必须产生相同成功或失败结果、相同异常
类型和完整消息，并保持输入不变。

## 列与空表

指定列必须在表中恰好出现一次。缺失或同名多列均以以下错误失败：

```text
ValueError: {who}: columns must exist exactly once: {invalid_columns!r}
```

其中列集合统一使用 `repr(list(columns))`，并保持调用方顺序。额外列、额外列之间的
重复和列顺序不影响检测。

`require_nonempty` 要求行数大于零，失败消息为：

```text
ValueError: {who}: data must contain at least one row
```

列身份合法时，其他六个检测对零行表真空通过，不根据空列 dtype 推断字段类型。

## 值检测

`require_non_null` 拒绝所有 missing；空字符串、boolean、无穷及其他非 missing 值不由
它拒绝。失败消息为：

```text
ValueError: {who}: columns must not contain null values: {failing_columns!r}
```

`require_nonempty_strings` 要求每个值是非 missing 的真实 string 且不等于 `""`。
dictionary、large string 与 categorical 按解码后的逻辑值判断；不 trim、不做 Unicode
或大小写规范化。失败消息为：

```text
ValueError: {who}: columns must contain only non-empty strings: {failing_columns!r}
```

`require_unique` 把指定列组成复合 key。missing 与 missing 相等，null key 合法；
调用方需要非 null identity 时另行组合 `require_non_null`。string、boolean、integer、
float 和 missing 是当前支持的 key 值，boolean 与 number 不相等。出现其他非 null
逻辑值时失败：

```text
ValueError: {who}: key columns must contain only strings, booleans, integers, floats, or nulls: {failing_columns!r}
```

存在重复 key 时失败：

```text
ValueError: {who}: columns must form a unique key: {columns!r}
```

`require_finite` 要求每个值是非 boolean 的整数或浮点数，且不是 missing 或正负无穷。
零和负数合法。失败消息为：

```text
ValueError: {who}: columns must contain only finite numbers: {failing_columns!r}
```

`require_positive` 在 `require_finite` 的基础上要求严格大于零；正负零和所有负数均
非法。失败消息为：

```text
ValueError: {who}: columns must contain only positive finite numbers: {failing_columns!r}
```

每个值检测一次报告全部失败列，保持调用方传入顺序，不包含值、行号、Pandas index、
dtype、Arrow schema 或后端名称。

## 参数错误

错误类型和完整消息固定为：

```text
TypeError: data must be a pyarrow.Table or pandas.DataFrame
TypeError: who must be a string
ValueError: who must not be empty
TypeError: columns must be a sequence of strings
ValueError: columns must not be empty
ValueError: columns must not contain duplicates
```

支持的表违反检测谓词时只产生本文件定义的 `ValueError`，不得泄露 `KeyError`、
`RuntimeError`、Pandas 或 Arrow 原生异常。

## 只读保证

公共检测不得修改、排序、trim、填充、去重或转换输入，不得改变 Pandas 的值、index、
dtype、categorical metadata、attrs，也不得改变 Arrow 的值、schema、metadata、chunk
或 dictionary 编码。

禁止使用 `astype(str)`、`pd.to_numeric` 或 Arrow cast 把非法值变成合法值。实现可以
创建临时只读 mask，并可解码 dictionary/categorical 以观察其逻辑值；临时结果不得替换
输入或作为规范化数据返回。

## Owner 边界

调用方拥有“对哪个数据在何时调用哪些检测”。数据集 key、六位数字 symbol、精确
schema、哪些业务输出必须非空以及 feature/label 对齐关系均不由本 API 推断。

Arrow compute、RecordBatch、`append_or_replace`、`map_values_or_null` 等继续由 Arrow
实现拥有；rolling、groupby、merge、index 对齐等继续由 Pandas 调用方拥有。
