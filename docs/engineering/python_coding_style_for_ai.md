# Python 编码风格：AI 硬规则

- **状态**：强制执行
- **适用范围**：仓库自有、非生成、非 vendored、非第三方源码的 Python 代码。
- **规范词**：本文中的“必须”“不得”“仅当”“否则删除或改写”均为硬规则，不表示建议。
- **业务语义来源**：业务不变量、交易规则、字段定义、时间口径、费用口径和状态机以对应 owner doc 为准。代码不得自行发明另一套语义。

示例中的 owner doc 可以是：

```text
docs/domain/a_share_trading_rules.md
docs/system/live_trading_contract.md
docs/data/market_data_schema.md
```

---

## PY-001 修改范围与同域清理

### 触发条件

新增、修改、移动、重命名或删除任意 `*.py` 文件时触发。

### 必须判断的语义

必须判断：

- 本次实际修改了哪个语义单元：模块、类、函数、API、数据模型或执行边界；
- 本次修改是否使同一语义单元中的 import、分支、helper、兼容代码、注释或测试失效；
- 发现的问题是否属于本次直接修改范围，还是无关历史代码。

### 必须执行

- 必须清理本次修改直接造成的无用代码。
- 必须修复本次修改语义单元内能够确定的规则违规。
- 不得借“顺手优化”重写无关模块。

### 必须删除或改写

以下内容必须删除或改写：

- 本次修改后不再使用的 import、局部变量、private helper 和分支；
- 已被新实现完全替代的旧路径；
- 与当前实现不一致的注释和测试；
- 没有调用方、没有外部兼容责任、没有 owner 的遗留代码。

### 允许保留

仅当兼容层具有公开契约、明确迁移窗口、弃用提示和回归测试时，允许暂时保留。

### 反例

```python
from pathlib import Path  # 修改后已不再使用


def load_prices(source: PriceSource) -> list[Price]:
    return source.load()


def _load_prices_legacy(source: PriceSource) -> list[Price]:
    # 已无调用方，也没有公开兼容责任
    return source.load()
```

### 正例

```python
import warnings


def load_prices(source: PriceSource) -> list[Price]:
    return source.load()


def load_prices_v1(source: PriceSource) -> list[Price]:
    """公开兼容入口；计划在 3.0 删除。"""
    warnings.warn(
        "load_prices_v1() is deprecated; use load_prices()",
        DeprecationWarning,
        stacklevel=2,
    )
    return load_prices(source)
```

兼容入口还必须有测试：

```python
def test_load_prices_v1_warns_and_preserves_result() -> None:
    source = StubPriceSource([Price(symbol="600000", close=10.0)])

    with pytest.warns(DeprecationWarning):
        result = load_prices_v1(source)

    assert result == load_prices(source)
```

### 完成前复核

- 检查变更文件中的 unused import、unused local、无调用 helper 和不可达分支。
- 检查删除路径是否仍被公开 API、配置、序列化名称或插件入口引用。
- 检查保留的兼容层是否包含弃用提示、删除目标和测试。
- 检查是否发生无关范围重写。

---

## PY-002 Python 文件路径标识

### 触发条件

新增、修改、移动或重命名仓库自有且非生成的 `*.py` 文件时触发。

### 必须判断的语义

必须判断：

- 文件是否属于仓库自有源码；
- 文件是否为生成文件、vendored 文件或第三方源码；
- 文件当前仓库相对路径是什么；
- 文件是否包含 shebang 或 encoding declaration。

### 必须执行

适用文件必须包含以下格式的路径标识：

```python
# filepath: src/utils/filesystem.py
```

标识必须位于可选 shebang 和 encoding declaration 之后的第一行有效注释位置。路径必须：

- 相对于仓库根目录；
- 与实际路径完全一致；
- 使用 `/` 分隔；
- 在移动或重命名后同步更新。

### 必须删除或改写

- 错误路径标识必须改写。
- 重复路径标识必须删除到只保留一个。
- 移动文件后遗留的旧路径必须改写。

### 允许保留

生成文件、vendored 文件和第三方源码允许不添加标识，但必须能够从目录、生成声明或依赖来源证明其身份。

### 反例

实际文件为 `src/utils/filesystem.py`：

```python
# filepath: utils/filesystem.py

from pathlib import Path
```

可执行脚本把标识放在 shebang 之前：

```python
# filepath: scripts/import_data.py
#!/usr/bin/env python3
```

### 正例

普通模块：

```python
# filepath: src/utils/filesystem.py

from pathlib import Path
```

可执行脚本：

```python
#!/usr/bin/env python3
# filepath: scripts/import_data.py

from collections.abc import Sequence
```

带 encoding declaration：

```python
# -*- coding: utf-8 -*-
# filepath: src/text/normalizer.py
```

### 完成前复核

- 列出本次新增、修改、移动和重命名的全部 `*.py` 文件。
- 逐个确认标识存在、位置正确、路径完全匹配。
- 对移动或重命名文件同时搜索旧路径字符串。
- 对豁免文件确认其确实属于生成、vendored 或第三方源码。

---

## PY-003 import 与模块加载副作用

### 触发条件

新增或修改 import、模块级变量、注册逻辑、客户端初始化、配置读取或文件读取时触发。

### 必须判断的语义

必须判断模块被 import 时是否会：

- 访问网络、数据库或文件系统；
- 读取环境变量或当前时间；
- 创建外部客户端、线程、进程或连接；
- 修改 `sys.path`、全局注册表或其他模块状态；
- 因运行环境缺少配置而直接失败。

### 必须执行

- import 阶段只能执行纯声明和确定性的轻量常量构造。
- 外部资源初始化必须放入显式工厂、入口函数或 composition root。
- 包内依赖必须使用正常包导入，不得通过运行时修改 `sys.path` 解决。
- 不得使用 `from module import *`。

### 必须删除或改写

- 模块级网络、数据库、文件读取和客户端创建必须移入函数或对象生命周期。
- `sys.path.append(...)`、`sys.path.insert(...)` 必须删除并修复包结构或运行方式。
- wildcard import 必须改为显式 import。

### 允许保留

仅允许保留不可变常量、类型定义、函数定义、类定义和不依赖外部状态的纯映射。

### 反例

```python
import os
import sys

import pandas as pd

sys.path.append("../src")

API_KEY = os.environ["MARKET_API_KEY"]
CLIENT = MarketClient(API_KEY)
REFERENCE_DATA = pd.read_parquet("/data/reference.parquet")

from trading.rules import *
```

### 正例

```python
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class MarketSettings:
    api_key: str
    reference_path: Path


def build_market_client(settings: MarketSettings) -> MarketClient:
    return MarketClient(api_key=settings.api_key)


def load_reference_data(path: Path) -> ReferenceData:
    return ReferenceData.from_parquet(path)
```

入口负责读取环境并组装依赖：

```python
def main() -> int:
    settings = load_settings_from_environment()
    client = build_market_client(settings)
    run_import(client=client, reference_path=settings.reference_path)
    return 0
```

### 完成前复核

- 在干净环境中确认 import 模块不会触发 I/O 或要求运行配置。
- 搜索 `sys.path` 修改、wildcard import、模块级客户端和模块级数据读取。
- 确认入口函数显式创建并传递外部依赖。

---

## PY-004 public API 类型契约与 `Any` 收口

### 触发条件

新增或修改 public 函数、public 方法、类构造函数、协议、回调、序列化边界或第三方 SDK 适配器时触发。

### 必须判断的语义

必须判断：

- 调用方需要传入什么；
- 返回值在成功、缺失和失败时分别是什么；
- 哪些值来自无类型的外部边界；
- 类型是否表达了单位、可空性、容器可变性和稳定数据结构。

### 必须执行

- 所有新增或修改的 public API 必须完整标注参数类型和返回类型。
- `Any` 只能出现在无法控制的外部边界，并且必须立即经过运行时校验或转换，不能继续传播到领域逻辑。
- 不得用 `Any`、无注解参数或无注解返回值绕过类型设计。

### 必须删除或改写

- public API 中无必要的 `Any` 必须替换为具体类型、`Protocol`、`object` 加校验或命名数据模型。
- 外部 SDK 返回的动态对象必须在 adapter 中收口。
- `# type: ignore` 若只为掩盖错误类型设计，必须删除并修复类型契约。

### 允许保留

当第三方库确实无类型信息时，adapter 内部允许短暂使用 `Any`；校验后返回值必须是仓库自有的明确类型。

### 反例

```python
def submit_order(payload):
    response = legacy_sdk.submit(payload)
    return response
```

```python
def calculate_score(data: Any) -> Any:
    return data["alpha"] * data["liquidity"]
```

### 正例

```python
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OrderReceipt:
    order_id: str
    accepted: bool


def _parse_order_receipt(raw: object) -> OrderReceipt:
    if not isinstance(raw, Mapping):
        raise TypeError("order receipt must be a mapping")

    order_id = raw.get("order_id")
    accepted = raw.get("accepted")
    if not isinstance(order_id, str) or not isinstance(accepted, bool):
        raise ValueError("invalid order receipt fields")

    return OrderReceipt(order_id=order_id, accepted=accepted)


def submit_order(request: OrderRequest) -> OrderReceipt:
    raw: Any = legacy_sdk.submit(request.to_payload())
    return _parse_order_receipt(raw)
```

### 完成前复核

- 检查本次修改的 public API 是否全部有参数和返回类型。
- 搜索新增或修改范围内的 `Any`、`type: ignore`、无注解 `def`。
- 对每个保留的 `Any` 确认其位于外部 adapter，且在离开 adapter 前已校验并转换。

---

## PY-005 只读容器接口、不可变默认值与所有权

### 触发条件

函数或方法接收、保存、修改、返回序列或映射时触发。

### 必须判断的语义

必须判断：

- API 只需要读取容器，还是明确需要修改调用方容器；
- 实现是否会保存容器并在调用结束后继续使用；
- 是否需要独立的顶层容器；
- 嵌套可变对象是否也需要独立所有权；
- 空集合与 `None` 是否具有不同业务语义。

### 必须执行

- API 只读时，参数必须使用 `Sequence[T]`、`Mapping[K, V]`、`Collection[T]` 等只读接口。
- 空集合默认值必须使用不可变值，如 `()` 或不可变映射替代方案。
- 实现需要修改或保存容器时，必须在入口创建独立的顶层容器。
- 需要深层所有权时，必须显式复制嵌套对象或转换为不可变结构。

### 必须删除或改写

- `list`、`dict` 仅因实现习惯而出现在只读 API 参数中时，必须改为只读接口。
- `=[]`、`={}` 等可变默认参数必须删除。
- 对调用方容器的隐式原地修改必须删除，除非该修改是 API 明确契约。
- 把 `list(...)` 或 `dict(...)` 误当成深复制的代码必须改写。

### 允许保留

仅当 API 的核心契约就是修改调用方提供的容器时，允许接收 `MutableSequence`、`MutableMapping` 等可变接口；函数名、类型和文档必须明确表达该副作用。

### 反例

```python
def load_items(items: list[Item] = []) -> list[Item]:
    items.append(load_default_item())
    return items
```

```python
def normalize_records(
    records: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    owned_records = list(records)
    owned_records[0]["status"] = "normalized"  # 仍会修改调用方的嵌套 dict
    return owned_records
```

### 正例

只需要顶层所有权：

```python
from collections.abc import Sequence


def load_items(items: Sequence[Item] = ()) -> list[Item]:
    owned_items = list(items)
    owned_items.append(load_default_item())
    return owned_items
```

需要嵌套所有权：

```python
from collections.abc import Mapping, Sequence


def normalize_records(
    records: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    owned_records = [dict(record) for record in records]
    for record in owned_records:
        record["status"] = "normalized"
    return owned_records
```

明确修改调用方容器：

```python
from collections.abc import MutableSequence


def drain_pending_items(queue: MutableSequence[Item]) -> None:
    while queue:
        persist_item(queue.pop(0))
```

### 完成前复核

- 搜索新增或修改函数中的 `=[]`、`={}` 和其他可变默认值。
- 检查只读参数是否错误使用 `list`、`dict`。
- 检查实现是否保存或修改调用方容器。
- 检查嵌套容器是否需要深层复制。
- 对保留的可变参数确认副作用在名称、类型和测试中都明确可见。

---

## PY-006 `None` 必须表达独立语义

### 触发条件

新增或修改 `T | None`、`Optional[T]`、默认值 `None`、空集合处理或缺省配置时触发。

### 必须判断的语义

必须分别定义：

- `None` 表示什么；
- 空字符串、空序列、空映射或零值表示什么；
- 调用方是否需要区分“未提供”“自动推导”和“明确提供空值”。

### 必须执行

- 仅当 `None` 与所有合法 `T` 值具有不同语义时，才允许使用 `T | None`。
- `None` 与空集合语义相同时，必须使用不可变空集合默认值，不得增加可空分支。
- 不得构造无效默认表达式规避类型设计。

### 必须删除或改写

以下形式必须删除或改写：

```python
def load_items(items: Sequence[Item] = () | None) -> list[Item]:
    ...
```

当 `None` 与空集合都表示“没有 items”时，以下分支必须删除：

```python
def load_items(items: Sequence[Item] | None = None) -> list[Item]:
    owned_items = list(items or ())
    return owned_items
```

### 允许保留

当 `None` 表示“使用系统默认值”，而空集合表示“明确不使用任何值”时，允许保留可空类型。

### 反例

```python
def load_items(items: Sequence[Item] | None = None) -> list[Item]:
    # None 和 () 都被解释为没有 item，没有独立语义
    return list(items or ())
```

### 正例

`None` 与空集合语义相同：

```python
def load_items(items: Sequence[Item] = ()) -> list[Item]:
    return list(items)
```

`None` 与空集合语义不同：

```python
def load_items(items: Sequence[Item] | None = None) -> list[Item]:
    selected_items = load_default_items() if items is None else items
    return list(selected_items)
```

调用语义必须可区分：

```python
load_items()    # 使用系统默认 items
load_items(())  # 明确不加载任何 item
```

### 完成前复核

- 对每个新增或修改的 `| None` 写出 `None` 的一句话语义。
- 将该语义与空值、零值逐一比较。
- 若无法说出差异，删除 `None` 和对应分支。
- 检查测试是否覆盖 `None` 与空值的差异。

---

## PY-007 稳定结构必须使用命名数据模型

### 触发条件

数据跨越函数、模块、分层、队列、缓存、数据库 adapter 或序列化边界时触发。

### 必须判断的语义

必须判断：

- 字段集合是否稳定；
- 字段是否有必填、可空、单位或不变量；
- 调用方是否依赖字符串 key 或 tuple 下标；
- 该结构是否属于外部原始 payload，还是仓库内部契约。

### 必须执行

- 稳定的仓库内部结构必须使用 `dataclass`、`NamedTuple`、`TypedDict`、`Protocol` 或明确领域类型。
- 运行时需要不变量和属性访问时，必须使用可校验的命名模型。
- 外部 `dict` 或 JSON 必须在边界转换，不能以裸映射贯穿领域逻辑。

### 必须删除或改写

- 使用 `dict[str, object]` 表达稳定领域对象的代码必须改写。
- 使用魔法 tuple 下标传递稳定字段的代码必须改写。
- 同一字段在不同调用点使用不同字符串 key 的实现必须统一到命名模型。

### 允许保留

原始 JSON、数据库行或第三方 payload 在 adapter 内允许短暂保持映射形式；离开 adapter 前必须转换。纯序列化输出允许返回 `dict[str, object]`。

### 反例

```python
def calculate_notional(order: dict[str, object]) -> float:
    return float(order["price"]) * int(order["quantity"])


order = ("600000", 10.25, 1_000)
notional = order[1] * order[2]
```

### 正例

```python
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Order:
    symbol: str
    price: Decimal
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


def calculate_notional(order: Order) -> Decimal:
    return order.price * order.quantity
```

序列化边界允许显式转换：

```python
def order_to_payload(order: Order) -> dict[str, object]:
    return {
        "symbol": order.symbol,
        "price": str(order.price),
        "quantity": order.quantity,
    }
```

### 完成前复核

- 搜索新增或修改范围内的稳定裸 `dict`、多元素 tuple 和字符串 key。
- 检查数据模型是否表达了必填、可空、单位和不变量。
- 检查外部 payload 是否在 adapter 内完成转换。
- 检查序列化函数是否只负责格式转换，不承载业务计算。

---

## PY-008 最小语义抽象

本规则约束 Python 工程抽象。业务语义仍以对应 owner doc 为准。

### 触发条件

新增或修改 private helper、wrapper、adapter 方法、转发方法或仅为缩短调用方而提取函数时触发。

### 必须判断的语义

必须判断该 helper 是否至少承担以下一种职责：

- 实现 owner doc 定义的业务不变量；
- 封装调用方不应知道的路径决策；
- 维护 metadata 或 lineage；
- 建立独立的日志、错误处理或事务边界；
- 隔离外部依赖；
- 形成可独立测试且有稳定契约的技术边界；
- 被多个调用方复用，并提供统一语义。

### 必须执行

private helper 必须具有可命名、可解释、可验证的语义边界。不得仅以“以后可能复用”“让函数看起来更短”或“保持形式统一”为由创建或保留 helper。

### 必须删除或改写

如果 private helper 同时满足以下条件，必须删除并让调用方直接调用已有 public API：

- 只有一个调用点；
- 仅将参数原样或改名后转发；
- 没有增加任何业务不变量、路径决策、metadata、lineage、日志、错误、事务或外部依赖边界；
- 没有隐藏调用方不应承担的复杂性。

### 允许保留

即使 helper 当前只有一个调用点，只要它承担明确且稳定的语义边界，仍允许保留。命名和实现必须体现该边界，不得表现为无语义转发层。

### 反例

```python
self._write_table(output_path, output.table)


@staticmethod
def _write_table(path: Path, table: pa.Table) -> None:
    write_parquet_atomic(output_file=path, table=table)
```

### 正例

无新增语义时直接调用：

```python
write_parquet_atomic(
    output_file=output_path,
    table=output.table,
)
```

单调用点但承担原子发布边界时允许保留：

```python
def _publish_snapshot_atomically(
    *,
    output_path: Path,
    table: pa.Table,
    lineage: LineageRecord,
) -> None:
    temporary_path = output_path.with_suffix(".tmp.parquet")
    write_parquet_atomic(output_file=temporary_path, table=table)
    write_lineage_record(temporary_path, lineage)
    temporary_path.replace(output_path)
```

该 helper 隐藏了临时路径、lineage 和原子替换，调用方不应重复承担这些语义。

### 完成前复核

- 找出本次新增或修改且只有一个调用点的 private helper。
- 判断其是否只是转发已有 public API。
- 对照 owner doc，确认其是否承载明确业务语义或技术边界。
- 删除没有明确边界的 helper。
- 若保留看似简单的单调用 helper，在完成汇报中说明其承担的边界。

---

## PY-009 函数必须保持单一语义，禁止模式开关拼接工作流

### 触发条件

新增或修改函数中的布尔开关、模式字符串、环境分支、回测/实盘分支、读/写分支或多种返回路径时触发。

### 必须判断的语义

必须判断不同分支是否改变了：

- 数据来源；
- 副作用；
- 事务边界；
- 错误处理；
- 返回类型；
- 业务不变量；
- owner doc 或调用方角色。

### 必须执行

一个函数必须对应一个稳定的业务动作或技术动作。若开关选择的是不同工作流，必须拆分为独立入口，并把真正共享的纯逻辑提取出来。

### 必须删除或改写

- `live=True`、`backtest=True`、`write=True` 等改变数据源或副作用的模式开关必须删除。
- 同一函数根据模式返回不同类型的实现必须拆分。
- 仅为共用少量代码而把独立工作流塞进一个函数的实现必须改写。

### 允许保留

仅当布尔参数只调整同一算法内部的局部策略，且不改变数据源、副作用、错误边界和返回类型时，允许保留。该参数必须为 keyword-only，并使用明确名称。

### 反例

```python
def run_strategy(
    *,
    live: bool,
    date: date,
) -> BacktestResult | list[Order]:
    if live:
        snapshot = live_feed.read()
        return broker.submit(build_orders(snapshot))

    frame = history_repository.load(date)
    return run_backtest(frame)
```

### 正例

```python
def calculate_target_weights(
    snapshot: MarketSnapshot,
    rules: StrategyRules,
) -> TargetWeights:
    return score_and_allocate(snapshot=snapshot, rules=rules)


def run_live_strategy(
    feed: LiveMarketFeed,
    broker: Broker,
    rules: StrategyRules,
) -> list[OrderReceipt]:
    snapshot = feed.read_snapshot()
    targets = calculate_target_weights(snapshot, rules)
    return broker.submit_all(build_orders(targets))


def run_backtest_strategy(
    snapshot: MarketSnapshot,
    rules: StrategyRules,
) -> TargetWeights:
    return calculate_target_weights(snapshot, rules)
```

允许的局部算法开关：

```python
def normalize_scores(
    scores: np.ndarray,
    *,
    clip_outliers: bool = False,
) -> np.ndarray:
    normalized = zscore(scores)
    return np.clip(normalized, -3.0, 3.0) if clip_outliers else normalized
```

### 完成前复核

- 搜索新增或修改函数中的 `bool` 参数、`mode`、`env`、`live`、`backtest`。
- 对每个分支比较数据源、副作用、错误和返回类型。
- 若任一项不同，拆分入口。
- 检查共享逻辑是否为纯计算，而不是另一层模式分支。

---

## PY-010 名称必须表达领域语义、方向和单位

### 触发条件

新增或修改模块名、类名、函数名、参数名、变量名、异常名或常量名时触发。

### 必须判断的语义

必须判断名称能否表达：

- 处理的领域对象；
- 动作或状态；
- 输入输出方向；
- 时间点、窗口或单位；
- 是否为原始值、调整值、净值或中间值。

### 必须执行

- 名称必须使调用方无需阅读实现即可理解核心语义。
- 带单位的值必须在名称或类型中体现单位。
- 布尔值必须表达可判定命题，如 `is_`、`has_`、`can_`、`should_`。
- 异常名称必须表达失败语义。

### 必须删除或改写

- `data`、`info`、`obj`、`temp`、`result2`、`process`、`handle`、`do_work` 等无法表达稳定语义的名称必须改写。
- 仅以 `Manager`、`Helper`、`Utils` 命名且无法说明职责边界的类型或模块必须改写或拆分。
- 单位不明的 `timeout`、`price`、`rate` 必须补充名称或领域类型。

### 允许保留

- `i`、`j` 仅允许用于很短的局部索引循环。
- `x`、`y` 仅允许用于公式或局部数值变换，且语义在紧邻上下文中明确。
- `Manager` 仅当类型确实统一执行一个明确策略边界时允许使用，例如 `RiskManager` 必须只负责风险决策，不能同时负责数据加载和下单。

### 反例

```python
def process(data, timeout):
    result2 = do_work(data)
    return result2


class DataManager:
    ...
```

### 正例

```python
def calculate_net_excess_return(
    gross_return: Decimal,
    transaction_cost_rate: Decimal,
) -> Decimal:
    return gross_return - transaction_cost_rate


def wait_for_fill(
    order_id: str,
    timeout_seconds: float,
) -> Fill:
    ...


is_trading_day = calendar.is_trading_day(trade_date)
```

允许的明确管理边界：

```python
class RiskManager:
    def evaluate_order(
        self,
        order: ProposedOrder,
        portfolio: PortfolioSnapshot,
    ) -> RiskDecision:
        ...
```

### 完成前复核

- 阅读变更 diff 中的所有新增名称，确认其包含对象、动作和必要单位。
- 搜索 `data`、`info`、`temp`、`manager`、`helper`、`utils` 等模糊名称。
- 对保留的通用名称确认其作用域足够小，或其职责边界确实明确。

---

## PY-011 返回值、缺失与失败必须使用不同契约

### 触发条件

新增或修改返回 `None`、`False`、空集合、空字符串、状态码、异常或联合类型的函数时触发。

### 必须判断的语义

必须分别定义：

- 正常成功；
- 正常但没有结果；
- 输入无效；
- 外部依赖失败；
- 业务拒绝；
- 部分成功。

### 必须执行

- 正常缺失允许使用 `T | None`，但必须是预期业务状态。
- 失败必须抛出明确异常或返回命名结果类型，不得伪装成空值。
- 同一函数不得混合返回 `T`、`False`、`None` 和不同结构的 `dict`。
- 业务拒绝若属于正常流程，必须使用明确枚举或结果对象。

### 必须删除或改写

- 用 `False`、`None` 或空集合吞掉错误的代码必须改写。
- 依赖调用方猜测 sentinel 含义的实现必须改写。
- 返回值类型因分支而变化的函数必须拆分或使用命名结果类型。

### 允许保留

查询“可能不存在”的对象时允许返回 `T | None`；调用方必须显式处理。批处理部分成功时允许返回命名报告对象。

### 反例

```python
def load_position(symbol: str):
    try:
        row = database.fetch(symbol)
    except DatabaseError:
        return False

    if row is None:
        return None

    return {"symbol": symbol, "quantity": row.quantity}
```

### 正例

```python
@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: int


class PositionRepositoryError(RuntimeError):
    pass


def load_position(symbol: str) -> Position | None:
    try:
        row = database.fetch(symbol)
    except DatabaseError as exc:
        raise PositionRepositoryError(
            f"failed to load position for {symbol}"
        ) from exc

    if row is None:
        return None

    return Position(symbol=symbol, quantity=row.quantity)
```

正常业务拒绝使用命名结果：

```python
class RiskDecisionType(Enum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision: RiskDecisionType
    reason: str | None = None
```

### 完成前复核

- 列出每个修改函数的成功、缺失、失败和拒绝语义。
- 检查这些语义是否被不同类型或异常明确区分。
- 搜索用于错误处理的 `return None`、`return False`、`return []`。
- 检查调用方是否显式处理预期缺失和业务拒绝。

---

## PY-012 外部输入必须在边界校验一次并转换

### 触发条件

读取 JSON、CSV、数据库行、环境变量、CLI 参数、HTTP payload、消息队列、第三方 SDK 或用户输入时触发。

### 必须判断的语义

必须判断：

- 哪一层首次取得不可信输入；
- 字段类型、必填性、范围、单位和相互约束；
- 校验后应转换成什么仓库自有类型；
- 内部函数是否仍在重复解释原始输入。

### 必须执行

- 不可信输入必须在 adapter 或 API 边界完成校验和转换。
- 领域逻辑必须接收已校验的命名类型。
- 相同校验不得散落在多个下游 helper 中。
- 错误信息必须指出字段和违规原因，不得只抛出通用 `ValueError("invalid")`。

### 必须删除或改写

- 在多个内部函数重复检查同一原始 key、类型或范围的代码必须收口到边界。
- 让裸 payload 穿过多层调用的实现必须改写。
- 仅依赖类型转换异常而不提供字段上下文的实现必须改写。

### 允许保留

不同边界允许各自校验同一领域类型，例如 HTTP 和 CSV adapter 分别解析输入；二者转换后的领域对象必须一致。

### 反例

```python
def calculate_order_value(payload: dict[str, object]) -> Decimal:
    if "price" not in payload:
        raise ValueError("invalid")
    price = Decimal(str(payload["price"]))
    quantity = int(payload["quantity"])
    return price * quantity


def submit_order(payload: dict[str, object]) -> None:
    if int(payload["quantity"]) <= 0:
        raise ValueError("invalid")
    broker.submit(payload)
```

### 正例

```python
@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    price: Decimal
    quantity: int


def parse_order_request(payload: Mapping[str, object]) -> OrderRequest:
    symbol = payload.get("symbol")
    price_raw = payload.get("price")
    quantity_raw = payload.get("quantity")

    if not isinstance(symbol, str) or not symbol:
        raise ValueError("field 'symbol' must be a non-empty string")

    try:
        price = Decimal(str(price_raw))
    except InvalidOperation as exc:
        raise ValueError("field 'price' must be decimal-compatible") from exc

    if not isinstance(quantity_raw, int) or quantity_raw <= 0:
        raise ValueError("field 'quantity' must be a positive integer")

    return OrderRequest(symbol=symbol, price=price, quantity=quantity_raw)


def calculate_order_value(request: OrderRequest) -> Decimal:
    return request.price * request.quantity
```

### 完成前复核

- 标出每个不可信输入的首次进入点。
- 确认进入领域逻辑前已完成类型、范围和组合约束校验。
- 搜索下游对同一原始字段的重复检查。
- 检查错误消息是否包含具体字段和原因。

---

## PY-013 异常只能在能够恢复、翻译或建立边界时捕获

### 触发条件

新增或修改 `try`、`except`、`raise`、重试、降级或任务边界时触发。

### 必须判断的语义

必须判断捕获异常后是否能够：

- 完成确定性恢复；
- 转换为本层稳定异常；
- 添加调用方需要的上下文；
- 执行资源回滚；
- 在最外层任务边界记录失败并返回失败状态。

### 必须执行

- 必须捕获最窄的可处理异常。
- 翻译异常时必须使用 `raise ... from exc` 保留因果链。
- 无法恢复或增加边界语义时，不得捕获。
- 任务最外层允许捕获 `Exception`，但必须记录失败并让任务以失败状态结束。

### 必须删除或改写

- bare `except:` 必须删除。
- `except Exception: pass`、记录后继续假装成功、返回空值吞错必须删除。
- 同一异常在多层重复记录必须改为只在责任边界记录一次。

### 允许保留

- 针对可预期瞬时错误的有限重试；
- 把第三方异常翻译为仓库稳定异常；
- worker、CLI 或服务请求最外层的失败记录。

### 反例

```python
def load_snapshot(path: Path) -> MarketSnapshot | None:
    try:
        return read_snapshot(path)
    except:
        return None
```

```python
try:
    broker.submit(order)
except Exception as exc:
    logger.error(f"submit failed: {exc}")
# 继续把订单标记为成功
mark_submitted(order)
```

### 正例

```python
class SnapshotReadError(RuntimeError):
    pass


def load_snapshot(path: Path) -> MarketSnapshot:
    try:
        return read_snapshot(path)
    except (OSError, ParquetError) as exc:
        raise SnapshotReadError(
            f"failed to read market snapshot: {path}"
        ) from exc
```

最外层任务边界：

```python
def worker_main(job: ImportJob) -> int:
    try:
        run_import_job(job)
    except Exception:
        logger.exception(f"import job failed; job_id={job.job_id}")
        return 1
    return 0
```

### 完成前复核

- 对每个 `except` 写出“恢复、翻译、回滚或任务终止”中的一项。
- 若一项都不成立，删除该 `except`。
- 检查捕获类型是否足够窄。
- 检查异常翻译是否保留 `from exc`。
- 检查任务失败是否确实传播为失败状态。

---

## PY-014 日志必须使用 f-string、保持结构化且不得泄露敏感信息

### 触发条件

新增或修改日志、异常记录、审计字段、请求上下文或完成汇报时触发。

### 必须判断的语义

必须判断：

- 该日志面向调试、运行监控、审计还是失败诊断；
- 是否包含可定位实体，如 `job_id`、`symbol`、`trade_date`；
- 是否包含密码、token、密钥、完整凭证、个人敏感信息或原始 payload；
- 同一失败是否已在下层或上层记录。

### 必须执行

- 所有 logger 调用必须使用 f-string 生成日志消息，不得使用 `%` 占位符参数化格式或字符串拼接。
- 日志中的上下文必须使用稳定的 `key=value` 形式，以便检索和聚合。
- 日志必须包含必要上下文，但不得输出敏感值。
- 异常堆栈只能在负责处理或终止任务的边界记录一次。
- 高频循环中的逐行日志必须删除或聚合。

### 必须删除或改写

- 包含 secret、token、password、credential 或完整请求 payload 的日志必须删除或脱敏。
- `logger.info("...%s", value)` 等参数化日志必须改为 f-string。
- 通过 `+`、`%` 或 `.format()` 提前拼接的日志消息必须改为 f-string。
- 同一异常在每层重复 `logger.exception` 必须删除到只保留责任边界。

### 允许保留

调试日志允许记录非敏感中间状态；必须能通过日志级别关闭，且不得位于无界高频循环中。

### 反例

```python
logger.info(
    "submitting order; order_id=%s symbol=%s",
    order_id,
    symbol,
)
logger.debug(f"broker token={settings.broker_token}")

try:
    repository.save(order)
except RepositoryError:
    logger.exception(f"save failed; order_id={order.order_id}")
    raise
```

调用方再次重复记录：

```python
try:
    service.submit(order)
except RepositoryError:
    logger.exception(f"submit failed; order_id={order.order_id}")
    raise
```

### 正例

```python
logger.info(
    f"submitting order; order_id={order.order_id} "
    f"symbol={order.symbol} quantity={order.quantity}"
)
```

下层增加异常上下文但不重复记录：

```python
def save_order(order: Order) -> None:
    try:
        repository.save(order)
    except DatabaseError as exc:
        raise OrderPersistenceError(
            f"failed to persist order {order.order_id}"
        ) from exc
```

任务边界记录一次：

```python
try:
    submit_order_workflow(order)
except OrderPersistenceError:
    logger.exception(f"order workflow failed; order_id={order.order_id}")
    raise
```

### 完成前复核

- 搜索 logger 调用中未使用 f-string 的消息、参数化占位符、字符串拼接和敏感字段名。
- 检查每条日志是否包含必要实体上下文。
- 沿异常调用链确认同一失败只记录一次堆栈。
- 检查高频循环日志是否已聚合。

---

## PY-015 外部资源必须由明确生命周期管理

### 触发条件

打开文件、数据库连接、事务、锁、临时目录、网络会话、线程池、进程池或其他可关闭资源时触发。

### 必须判断的语义

必须判断：

- 谁创建资源；
- 谁拥有资源；
- 正常、异常和取消路径如何释放；
- 资源是单次操作还是长生命周期；
- 是否存在标准或自定义 context manager。

### 必须执行

- 单次资源必须使用 `with` 或 `async with`。
- 事务必须在明确边界提交或回滚。
- 长生命周期资源必须由拥有者实现 `close()`；对外暴露作用域生命周期的资源拥有者必须同时实现 context manager 协议。
- 不得依赖垃圾回收、`__del__` 或进程退出释放关键资源。

### 必须删除或改写

- 手工 `open()` 后依赖末尾 `close()` 的代码必须改为 context manager。
- 多个 return/exception 路径可能跳过释放的实现必须改写。
- 无 owner 的全局连接或 session 必须删除。

### 允许保留

显式长期连接允许跨多个操作复用，但必须由 composition root 创建，由明确对象持有，并在应用关闭时释放。

### 反例

```python
def read_config(path: Path) -> str:
    file = path.open("r", encoding="utf-8")
    content = file.read()
    file.close()
    return content
```

```python
CONNECTION = database.connect()
```

### 正例

```python
def read_config(path: Path) -> str:
    with path.open("r", encoding="utf-8") as file:
        return file.read()
```

长生命周期 owner：

```python
class MarketDataSession:
    def __init__(self, client: MarketClient) -> None:
        self._client = client

    def __enter__(self) -> "MarketDataSession":
        self._client.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._client.close()
```

```python
with MarketDataSession(client) as session:
    import_market_data(session)
```

### 完成前复核

- 列出本次创建的所有可关闭资源及其 owner。
- 模拟正常、异常、提前 return 和取消路径，确认均会释放。
- 搜索模块级连接、裸 `open()`、手工 `close()` 和 `__del__`。
- 检查事务失败时是否回滚。

---

## PY-016 核心逻辑不得直接读取时间、环境、随机数和文件路径

### 触发条件

领域计算、策略、风控、标签、回测、状态机或可重复测试逻辑中使用当前时间、环境变量、随机数、工作目录、固定绝对路径或网络状态时触发。

### 必须判断的语义

必须判断结果是否依赖：

- `datetime.now()`、`date.today()` 或系统时区；
- `os.environ`、`os.getenv`；
- `random` 或隐式随机种子；
- 当前工作目录、用户目录或硬编码绝对路径；
- 当前网络或外部服务状态。

### 必须执行

- 核心逻辑必须通过参数或依赖对象接收 `as_of`、clock、settings、path、random generator 和外部数据。
- 环境读取和当前时间读取只能发生在入口、配置 adapter 或 composition root。
- 随机算法必须显式接收 seed 或 generator。
- 测试必须能完全控制这些输入。

### 必须删除或改写

- 核心函数中的 `datetime.now()`、`date.today()`、`os.getenv()`、`Path.cwd()`、`Path.home()` 和全局随机调用必须删除。
- 硬编码机器路径必须改为配置或参数。
- 通过 monkeypatch 大量全局状态才能测试的设计必须改写为显式依赖。

### 允许保留

CLI、服务启动入口和配置 loader 允许读取当前环境；读取后必须转换为明确配置对象并向下传递。

### 反例

```python
def should_rebalance(portfolio: Portfolio) -> bool:
    now = datetime.now()
    cutoff = os.getenv("REBALANCE_CUTOFF", "14:30")
    return now.strftime("%H:%M") >= cutoff
```

```python
def sample_candidates(symbols: Sequence[str]) -> list[str]:
    return random.sample(list(symbols), k=100)
```

### 正例

```python
@dataclass(frozen=True, slots=True)
class RebalanceRules:
    cutoff: time


def should_rebalance(
    portfolio: Portfolio,
    *,
    as_of: datetime,
    rules: RebalanceRules,
) -> bool:
    return as_of.time() >= rules.cutoff
```

```python
def sample_candidates(
    symbols: Sequence[str],
    *,
    rng: random.Random,
    sample_size: int,
) -> list[str]:
    return rng.sample(list(symbols), k=sample_size)
```

入口读取环境：

```python
def main() -> int:
    settings = load_settings_from_environment()
    as_of = settings.clock.now()
    run_rebalance(as_of=as_of, rules=settings.rebalance_rules)
    return 0
```

### 完成前复核

- 搜索核心模块中的当前时间、环境变量、全局随机和硬编码路径。
- 确认所有不确定输入都能由测试传入。
- 使用固定时间、固定配置和固定 seed 重复运行测试，结果必须一致。

---

## PY-017 业务常量与不变量必须来自 owner doc

### 触发条件

新增或修改费率、时间窗口、涨跌停比例、批量大小、阈值、状态迁移、字段口径或任何影响业务结果的常量时触发。

### 必须判断的语义

必须判断：

- 该值是业务规则、技术常量还是算法超参数；
- 对应 owner doc、配置版本和生效区间是什么；
- 单位、精度和边界条件是什么；
- 值是否可能按市场、板块、日期或环境变化。

### 必须执行

- 业务规则必须通过命名配置或规则对象进入核心逻辑。
- 规则对象必须能够表达版本或生效范围，若 owner doc 有该要求。
- 代码注释只能指向规则来源和解释非显然原因，不得重新发明规则。
- 金额和费率必须使用满足精度要求的类型，不得默认使用二进制浮点。

### 必须删除或改写

- 业务计算中的魔法数字必须删除。
- 在多个模块复制同一业务常量必须收口。
- 将日期相关规则永久硬编码为单值的实现必须改写。

### 允许保留

纯技术且稳定的常量允许模块级定义，例如 `SECONDS_PER_MINUTE = 60`；其不得被伪装成业务规则。

### 反例

```python
def calculate_sell_cost(notional: float) -> float:
    return notional * 0.001


def can_submit(now: datetime) -> bool:
    return now.time() >= time(14, 30)
```

### 正例

```python
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class TradingRuleVersion:
    effective_from: date
    effective_to: date | None
    stamp_duty_rate: Decimal
    decision_cutoff: time


def calculate_sell_cost(
    notional: Decimal,
    rules: TradingRuleVersion,
) -> Decimal:
    return notional * rules.stamp_duty_rate
```

规则装配处指向 owner doc：

```python
# Source: docs/domain/a_share_trading_rules.md, section "Sell-side taxes".
RULES_2026 = TradingRuleVersion(
    effective_from=date(2026, 1, 1),
    effective_to=None,
    stamp_duty_rate=Decimal("0.0005"),
    decision_cutoff=time(14, 30),
)
```

允许的技术常量：

```python
SECONDS_PER_MINUTE = 60
```

### 完成前复核

- 搜索变更中的数值字面量、时间字面量和重复常量。
- 对每个影响业务结果的值确认 owner doc、单位和生效范围。
- 检查同一规则是否在多个模块复制。
- 检查金额、费率和精度类型是否符合契约。

---

## PY-018 禁止隐式全局状态与 Singleton

### 触发条件

新增或修改模块级可变变量、全局缓存、service locator、`get_instance()`、单例 metaclass、全局 client 或隐式注册表时触发。

### 必须判断的语义

必须判断：

- 依赖是否从函数签名和构造函数可见；
- 状态由谁创建、重置和关闭；
- 测试是否需要修改全局状态才能隔离；
- 并发任务是否会共享并污染状态；
- 缓存是否为纯函数、输入键控且有界。

### 必须执行

- 运行时依赖必须通过参数、构造函数或 composition root 显式传递。
- 不得新增 Singleton、service locator 或模块级可变业务状态。
- 缓存仅允许用于纯函数结果，并且必须由完整输入决定结果。

### 必须删除或改写

- `Class.instance()`、`get_global_client()`、全局 repository、全局 portfolio 等隐式依赖必须删除。
- 测试依靠清空全局状态的设计必须改写。
- 会读取环境或外部状态的 `lru_cache` 必须删除。

### 允许保留

- 模块级不可变常量；
- 输入完整、无副作用、结果确定且缓存有界的纯函数缓存；
- 框架强制的注册表仅能位于 adapter 层，且不得承载可变业务状态。

### 反例

```python
class Database:
    _instance: "Database | None" = None

    @classmethod
    def instance(cls) -> "Database":
        if cls._instance is None:
            cls._instance = Database(os.environ["DATABASE_URL"])
        return cls._instance


def load_orders() -> list[Order]:
    return Database.instance().fetch_orders()
```

### 正例

```python
class OrderService:
    def __init__(self, repository: OrderRepository) -> None:
        self._repository = repository

    def load_orders(self) -> list[Order]:
        return self._repository.fetch_orders()


def build_order_service(settings: Settings) -> OrderService:
    repository = SqlOrderRepository.connect(settings.database_url)
    return OrderService(repository)
```

允许的纯缓存：

```python
from functools import lru_cache


@lru_cache(maxsize=128)
def parse_schema(schema_text: str) -> ParsedSchema:
    return ParsedSchema.parse(schema_text)
```

### 完成前复核

- 搜索 `instance`、`singleton`、`global`、模块级 client 和可变容器。
- 确认每个运行时依赖都能从调用路径显式追踪。
- 检查测试是否需要清理全局状态。
- 对缓存确认结果只由参数决定，并设置容量上限。

---

## PY-019 禁止深继承树，复用实现必须使用组合、协议或纯函数

### 触发条件

新增或修改基类、抽象类、mix-in、模板方法、框架扩展类或类层次结构时触发。

### 必须判断的语义

必须判断：

- 继承表示稳定的“is-a”契约，还是仅为了复用实现；
- 子类是否依赖父类隐式状态、调用顺序或 protected 方法；
- 修改父类是否会影响多个不相关子类；
- 行为能否通过组合、`Protocol` 或纯函数表达。

### 必须执行

- 仓库自有具体类不得形成多级实现继承链。
- 具体类最多实现一个稳定抽象契约；共享实现必须使用组合、协议或纯函数，不得依赖多级具体继承。
- 不得通过 mix-in 叠加隐式状态和方法解析顺序。

### 必须删除或改写

以下结构必须拆分：

```text
BaseLoader -> Loader -> CachedLoader -> ParquetLoader
```

- 仅为复用方法而继承的具体类必须改为组合。
- 依赖父类 protected 状态或模板调用顺序的实现必须改写为显式依赖。

### 允许保留

- 一个稳定的 `ABC` 或 `Protocol` 契约；
- 框架要求的直接继承，例如一个 `BaseModel`；
- 标准库异常继承。

允许保留的继承不得再叠加仓库自有具体中间层。

### 反例

```python
class BaseLoader:
    def load(self, path: Path) -> DataFrame:
        raise NotImplementedError


class CachedLoader(BaseLoader):
    def load(self, path: Path) -> DataFrame:
        cached = self._cache.get(path)
        return cached if cached is not None else super().load(path)


class ParquetLoader(CachedLoader):
    def load(self, path: Path) -> DataFrame:
        return pd.read_parquet(path)
```

### 正例

```python
from typing import Protocol


class TableLoader(Protocol):
    def load(self, path: Path) -> DataFrame:
        ...


class ParquetTableLoader:
    def load(self, path: Path) -> DataFrame:
        return pd.read_parquet(path)


class CachedTableLoader:
    def __init__(self, loader: TableLoader, cache: TableCache) -> None:
        self._loader = loader
        self._cache = cache

    def load(self, path: Path) -> DataFrame:
        cached = self._cache.get(path)
        if cached is not None:
            return cached

        frame = self._loader.load(path)
        self._cache.put(path, frame)
        return frame
```

### 完成前复核

- 绘制本次涉及类的仓库自有继承链。
- 若存在具体类继承具体类再被继承，必须拆分。
- 检查共享行为是否可替换为组合、协议或纯函数。
- 检查 mix-in 是否携带状态或依赖方法解析顺序。

---

## PY-020 禁止 God Object，编排器不得实现领域计算

### 触发条件

类新增数据读取、特征计算、信号、风控、优化、执行、持仓、日志、序列化或资源管理职责时触发。

### 必须判断的语义

必须判断：

- 该类是否因多个 owner doc 变化而变化；
- 是否拥有多个独立资源生命周期；
- 是否同时做计算和 I/O；
- 是否同时决定策略、风控和执行；
- 是否难以在不创建大量无关依赖的情况下测试单个行为。

### 必须执行

- 一个类必须拥有一个明确职责边界。
- 编排器只允许排序步骤、传递数据和处理工作流级失败；不得实现因子、风险、优化或成交公式。
- 独立职责必须拆成可独立测试的组件。

### 必须删除或改写

同时承担以下多项职责的类必须拆分：

- 拉取或持久化数据；
- 计算信号；
- 风控；
- 组合优化；
- 下单；
- 持仓状态；
- 日志或报告生成。

### 允许保留

应用服务或 orchestrator 允许持有多个依赖，但其方法必须只做显式编排，不得复制依赖内部的业务逻辑。

### 反例

```python
class TradingEngine:
    def run(self, trade_date: date) -> None:
        frame = self._database.load_prices(trade_date)
        frame["score"] = frame["momentum"] - frame["volatility"]
        frame = frame[frame["score"] > 0.5]
        frame["weight"] = frame["score"] / frame["score"].sum()
        frame = frame[frame["weight"] < 0.1]
        orders = self._build_orders(frame)
        self._broker.submit_all(orders)
        self._logger.info(f"trading workflow completed; trade_date={trade_date}")
```

### 正例

```python
class TradingWorkflow:
    def __init__(
        self,
        market_repository: MarketRepository,
        signal_model: SignalModel,
        risk_policy: RiskPolicy,
        order_builder: OrderBuilder,
        broker: Broker,
    ) -> None:
        self._market_repository = market_repository
        self._signal_model = signal_model
        self._risk_policy = risk_policy
        self._order_builder = order_builder
        self._broker = broker

    def run(self, trade_date: date) -> list[OrderReceipt]:
        snapshot = self._market_repository.load_snapshot(trade_date)
        targets = self._signal_model.calculate_targets(snapshot)
        approved_targets = self._risk_policy.apply(targets)
        orders = self._order_builder.build(approved_targets)
        return self._broker.submit_all(orders)
```

每个组件独立实现自己的契约：

```python
class SignalModel(Protocol):
    def calculate_targets(self, snapshot: MarketSnapshot) -> TargetWeights:
        ...
```

### 完成前复核

- 为每个修改类列出“变化原因”和“资源 owner”。
- 若一个类因多个 owner doc 或多个资源生命周期而变化，必须拆分。
- 检查 orchestrator 是否出现领域公式、DataFrame 因子运算或风控阈值。
- 检查单元测试是否能只构造该职责需要的依赖。

---

## PY-021 回测与实盘必须共享核心业务逻辑

### 触发条件

新增或修改回测、模拟盘、实盘、离线回放、信号、风控、持仓状态机或订单生成逻辑时触发。

### 必须判断的语义

必须判断回测与实盘之间哪些差异属于：

- 数据输入 adapter；
- clock；
- 成交模拟或真实 broker；
- I/O 与持久化；
- 真实业务规则。

### 必须执行

- 信号、风控、目标权重、T+1 状态和订单意图等核心业务规则必须只有一份实现。
- 回测与实盘只允许在数据来源、clock、成交 adapter 和外部副作用上不同。
- 同一输入快照和规则版本必须产生同一核心决策。

### 必须删除或改写

- `calculate_signal_backtest()` 与 `calculate_signal_live()` 各自复制公式的实现必须合并。
- 回测为了方便绕过实盘规则的分支必须删除，除非 owner doc 明确定义为不同产品语义。
- 实盘 adapter 内复制风控或状态机逻辑必须移回共享核心。

### 允许保留

真实 broker 与 simulated broker、历史数据源与 live feed、系统 clock 与 fixed clock 允许分别实现同一协议。

### 反例

```python
def calculate_signal_backtest(frame: DataFrame) -> Series:
    return frame["momentum_20"] - frame["volatility_20"]


def calculate_signal_live(snapshot: DataFrame) -> Series:
    # 公式已与回测发生漂移
    return snapshot["momentum_20"] - 0.5 * snapshot["volatility_20"]
```

### 正例

```python
def calculate_signal(
    features: FeatureMatrix,
    rules: SignalRules,
) -> SignalVector:
    values = (
        features.momentum_20
        - rules.volatility_penalty * features.volatility_20
    )
    return SignalVector(values)
```

```python
backtest_signal = calculate_signal(
    historical_adapter.load_features(trade_date),
    rules,
)
live_signal = calculate_signal(
    live_adapter.load_features(as_of),
    rules,
)
```

一致性测试：

```python
def test_live_and_backtest_adapters_produce_same_core_decision() -> None:
    historical_adapter = StubHistoricalFeatureAdapter(
        feature_matrix=fixed_feature_matrix(),
    )
    live_adapter = StubLiveFeatureAdapter(
        feature_matrix=fixed_feature_matrix(),
    )

    backtest_features = historical_adapter.load_features(
        trade_date=date(2026, 7, 14),
    )
    live_features = live_adapter.load_features(
        as_of=datetime(2026, 7, 14, 14, 30),
    )

    assert calculate_signal(backtest_features, RULES) == calculate_signal(
        live_features,
        RULES,
    )
```

### 完成前复核

- 搜索带 `live`、`paper`、`backtest` 后缀的业务计算函数。
- 对比公式、阈值、状态迁移和订单意图是否重复。
- 使用同一标准化输入执行不同 adapter，确认核心结果一致。
- 检查差异是否严格限制在 I/O、clock 和成交实现。

---

## PY-022 Repository 只能承担持久化语义

### 触发条件

新增或修改 `Repository`、DAO、storage adapter、数据库查询、Parquet 访问或 DataFrame 返回接口时触发。

### 必须判断的语义

必须判断代码是在：

- 读取、写入、查询、删除或映射持久化数据；
- 还是计算特征、信号、风险、收益、排名或组合权重；
- 查询过滤是存储选择条件，还是业务筛选规则。

### 必须执行

- Repository 必须只负责持久化访问和存储模型映射。
- 特征、信号、风险、收益和 DataFrame 业务变换必须位于领域服务或纯函数。
- Repository 返回值必须具有明确 schema 或领域模型，不得成为任意 DataFrame 运算入口。

### 必须删除或改写

- `repository.calculate_factor()`、`repository.rank_candidates()`、`repository.apply_risk_limits()` 必须移出 Repository。
- 接收任意 lambda 或任意 DataFrame transformation 的通用 Repository 必须删除。
- 把业务过滤藏进 SQL 且无法从领域调用看见的实现必须改写。

### 允许保留

Repository 允许接收明确的存储查询条件，如 `trade_date`、`symbols`、分页和字段投影；允许把数据库行映射为领域模型。

### 反例

```python
class MarketRepository:
    def load_and_rank_candidates(self, trade_date: date) -> DataFrame:
        frame = self._read_parquet(trade_date)
        frame["score"] = frame["momentum"] / frame["volatility"]
        return frame.nlargest(500, "score")
```

### 正例

```python
class MarketRepository(Protocol):
    def load_features(
        self,
        trade_date: date,
        symbols: Sequence[str],
    ) -> FeatureMatrix:
        ...


def rank_candidates(
    features: FeatureMatrix,
    rules: RankingRules,
) -> RankedCandidates:
    scores = calculate_scores(features, rules)
    return select_top_candidates(scores, limit=rules.candidate_limit)
```

### 完成前复核

- 检查 Repository 方法名是否只表达 load、save、find、list、delete、upsert 等持久化动作。
- 搜索 Repository 内的因子公式、排名、风控和收益计算。
- 检查查询过滤是否只是存储选择条件。
- 检查返回结构是否具有稳定 schema。

---

## PY-023 数值热路径必须保持向量化、纯函数和批量语义

### 触发条件

新增或修改大规模数组、DataFrame、订单簿、分钟数据、横截面计算、因子计算、优化输入或批量特征处理时触发。

### 必须判断的语义

必须判断：

- 数据规模是否随日期、股票数、订单数或分钟数增长；
- 是否在逐行创建对象、调用虚方法、构造策略或写日志；
- 是否能使用 NumPy、PyArrow、Polars、Pandas 向量化或批量 kernel；
- 算法是否真正具有顺序状态依赖。

### 必须执行

- 无顺序依赖的数值计算必须使用向量化或批量操作。
- 设计模式只能在热路径外选择一次实现，不得在每行或每个元素上进行动态分派。
- 热路径必须将 I/O 与批量计算分离；批量计算函数必须是纯函数，输入数组并返回数组或命名批量结果。
- 无界 DataFrame 不得使用 `iterrows()` 实现可向量化计算。

### 必须删除或改写

- 每行创建 strategy、visitor、command 或领域对象的实现必须删除。
- 可向量化的 Python for-loop、`iterrows()` 和逐行 logger 调用必须改写。
- 为“模式统一”把简单数组公式包装成多层对象调用的实现必须扁平化。

### 允许保留

真正具有顺序状态依赖的算法允许显式循环，例如按时间顺序推进 T+1 lot 状态；该循环必须隔离、可测试，不得混入无关 I/O 和对象工厂。

### 反例

```python
scores: list[float] = []
for _, row in frame.iterrows():
    strategy = ScoreStrategyFactory.create(row["board"])
    scores.append(strategy.calculate(row))
frame["score"] = scores
```

### 正例

模式在热路径外选择一次，内部批量计算：

```python
def calculate_scores(
    momentum: np.ndarray,
    volatility: np.ndarray,
    *,
    volatility_penalty: float,
) -> np.ndarray:
    return momentum - volatility_penalty * volatility


frame["score"] = calculate_scores(
    frame["momentum_20"].to_numpy(),
    frame["volatility_20"].to_numpy(),
    volatility_penalty=0.5,
)
```

允许的顺序状态机：

```python
def apply_t1_lot_events(
    initial_state: LotState,
    events: Sequence[PositionEvent],
) -> LotState:
    state = initial_state
    for event in events:
        state = transition_lot_state(state, event)
    return state
```

### 完成前复核

- 搜索热路径中的 `iterrows()`、逐元素对象创建、工厂调用和日志。
- 对无状态公式确认已批量化。
- 对保留循环写出顺序依赖，并检查其是否与 I/O 分离。
- 使用代表性数据规模运行性能基准；不得只用数十行样本证明可接受。

---

## PY-024 DataFrame 所有权和赋值必须显式

### 触发条件

函数接收、返回、缓存或修改 Pandas `DataFrame`、`Series` 或类似可变表对象时触发。

### 必须判断的语义

必须判断：

- 输入 frame 由调用方还是当前函数拥有；
- 当前操作返回 view 还是 copy；
- 是否会修改调用方可见数据；
- 是否存在 chained assignment；
- schema 在操作前后如何变化。

### 必须执行

- 默认不得修改调用方拥有的 DataFrame。
- 需要修改时，必须在函数入口显式复制，并使用体现所有权的名称，如 `owned_frame`。
- 赋值必须使用 `.loc[...]` 或单次明确列赋值，不得使用 chained assignment。
- 返回值 schema 必须稳定并由测试验证。

### 必须删除或改写

- `frame[mask]["col"] = value` 必须改写。
- 未声明的原地 mutation 必须删除。
- 依赖 `SettingWithCopyWarning` 不触发来证明正确的实现必须改写。

### 允许保留

private 函数允许原地修改由调用方明确转移所有权的对象，但参数或局部名称必须表明 ownership，且该对象不得在其他路径继续使用。

### 反例

```python
def add_score(frame: pd.DataFrame) -> pd.DataFrame:
    frame[frame["volume"] > 0]["score"] = (
        frame["momentum"] / frame["volatility"]
    )
    return frame
```

### 正例

```python
def add_score(frame: pd.DataFrame) -> pd.DataFrame:
    owned_frame = frame.copy()
    valid_mask = owned_frame["volume"] > 0
    owned_frame.loc[valid_mask, "score"] = (
        owned_frame.loc[valid_mask, "momentum"]
        / owned_frame.loc[valid_mask, "volatility"]
    )
    return owned_frame
```

明确转移所有权的 private helper：

```python
def _add_score_in_place(owned_frame: pd.DataFrame) -> None:
    owned_frame.loc[:, "score"] = (
        owned_frame["momentum"] / owned_frame["volatility"]
    )
```

### 完成前复核

- 检查每个 DataFrame 参数的 owner。
- 搜索 chained assignment 和未说明的 `inplace=True`。
- 检查复制是否只发生在真正的所有权边界，避免无意义重复复制。
- 检查输出列、dtype、索引和排序是否有测试。

---

## PY-025 public API 必须给出具体调用示例

### 触发条件

新增、修改或保留注释、docstring、TODO、FIXME、弃用说明或 owner doc 引用时触发。

public API 如果只有功能摘要和类型签名，调用者仍然不知道如何构造对象、组合参数或消费
返回值，必须转而阅读实现并猜测调用关系。这表示公开契约不完整；具体调用示例不是可选
说明，而是 public API docstring 的组成部分。

### 必须判断的语义

必须判断文字是否解释：

- 调用契约；
- public API 的最小实际调用方式；
- 非显然业务原因；
- owner doc 来源；
- 单位、边界或副作用；
- 为什么不能使用更直观实现；
- 临时限制的 owner 和退出条件。

### 必须执行

- public API 的非显然契约必须写入 docstring 或类型模型。
- 每个新增或修改的 public class、function 和 method 都必须在自身 docstring 中提供
  `Example:`，展示至少一次使用当前真实 API 名称和参数的具体调用。
- public class 的示例必须展示构造；其主要用途是调用方法时，还必须至少展示一条正常
  方法调用链。public method 的示例必须出现该方法自身的调用，可以复用 class docstring
  已说明的实例变量。
- 示例必须表达最小成功路径；返回值需要继续消费才能说明用途时，必须展示该消费关系。
  不得用签名复述、纯文字“调用此方法”、不存在的 helper、旧 API 或与实现无关的伪代码
  代替具体调用。
- 注释必须解释“为什么”或“不变量”，不得复述代码。
- TODO/FIXME 必须包含 owner、触发条件或删除条件；无 owner 的占位 TODO 不得保留。
- 代码变化后必须同步更新注释。

### 必须删除或改写

- 复述下一行代码的注释必须删除。
- 与实现不一致的注释必须改写或删除。
- 被注释掉的旧代码必须删除，不得作为历史记录保留。
- `TODO: later`、`FIXME` 等无责任和退出条件的文字必须删除。

### 允许保留

- 解释业务规则来源；
- 解释数值算法或性能限制；
- 解释看似多余但用于维护不变量的步骤；
- 具有 issue、owner 或删除版本的临时兼容说明。

### 反例

```python
# Add one to retry count
retry_count += 1

# Loop over orders
for order in orders:
    submit(order)

# old implementation
# result = legacy_calculate(frame)
```

不构成 public API 调用示例：

```python
def universe(
    self,
    *,
    trade_date: str,
) -> tuple[str, ...]:
    """Return the universe.

    Example:
        Call this method to get symbols.
        symbols = build_symbols()
    """
    ...
```

### 正例

```python
# The broker may acknowledge before the fill is queryable; retrying closes
# that documented consistency gap without resubmitting the order.
retry_count += 1
```

```python
def calculate_forward_return(
    prices: PriceSeries,
    horizon_trading_days: int,
) -> ReturnSeries:
    """Return T+1 executable-entry to T+1+horizon close return.

    The label timing follows docs/research/label_contract.md and must not use
    the signal-day close as the executable entry price.
    """
    ...
```

public API 的最小具体调用示例：

```python
class Access:
    """Read one processed market-data version.

    Example:
        pm = PathManager(Path("/absolute/path/to/formal-storage"))
        access = Access(pm=pm, processed_version="v1")
        symbols = access.universe(
            trade_date="2026-05-06",
            min_listing_calendar_days=20,
        )
    """
```

```python
def universe(
    self,
    *,
    trade_date: str,
    min_listing_calendar_days: int,
) -> tuple[str, ...]:
    """Return the filtered daily-bar universe.

    Example:
        symbols = access.universe(
            trade_date="2026-05-06",
            min_listing_calendar_days=20,
        )
    """
    ...
```

有退出条件的临时兼容说明：

```python
# Compatibility for model manifests produced before schema v3.
# Remove after release 4.0 once migration issue DATA-231 is closed.
legacy_name = payload.get("feature_name")
```

### 完成前复核

- 阅读所有新增和修改注释，删除仅复述代码的文字。
- 搜索被注释掉的代码、TODO 和 FIXME。
- 检查注释中的路径、版本、字段和公式是否仍与实现一致。
- 检查非显然 public 契约是否由类型、docstring 或 owner doc 引用表达。
- 检查每个新增或修改的 public API 都有使用真实名称和参数的具体调用示例。

---

## PY-026 测试必须可重复且镜像源码布局

### 触发条件

新增、修改、移动、重命名或删除 pytest 测试文件，或新增功能、修改行为、修复缺陷、调整边界条件、改变异常、重构共享逻辑或删除兼容路径时触发。

### 必须判断的语义

必须判断：

- 哪个外部可观察行为发生变化；
- 缺陷的最小复现输入是什么；
- 应验证返回值、状态、异常、持久化结果还是副作用；
- 测试是否依赖当前时间、环境、网络、执行顺序或 private helper 名称。
- 每个测试文件唯一对应哪个 `src/` 源码模块。

### 必须执行

- 每个行为变化必须新增或更新测试。
- 每个缺陷修复必须先有能失败的回归场景，再由修复使其通过。
- 测试必须断言 public 契约或稳定边界，不得把 private helper 的存在当成行为。
- 时间、随机、环境和外部依赖必须固定或替换为 test double。
- `tests/` 中直接验证源码模块的 pytest 文件必须镜像该模块在 `src/` 下的相对目录，并使用 `test_<module>.py` 命名。规范映射为：

  ```text
  src/<relative-directory>/<module>.py
  tests/<relative-directory>/test_<module>.py
  ```

  `<relative-directory>` 为空时，测试文件位于 `tests/` 根目录。例如：

  ```text
  src/api/app.py                         -> tests/api/test_app.py
  src/data_system/steps/fact_ingest_step.py
                                         -> tests/data_system/steps/test_fact_ingest_step.py
  src/cli.py                             -> tests/test_cli.py
  ```

- 一个 pytest 文件必须具有唯一源码模块 owner。断言分属多个源码模块 public 行为的聚合测试必须按 owner 拆分；共享 fixture 不改变测试文件的源码归属。
- 跨模块行为若由既有 composition root 或工作流模块拥有，测试必须镜像该 owner 模块；不得仅为归集测试而虚构源码 owner 或无对应源码的聚合测试名。

### 必须删除或改写

- 只断言 private helper 被调用的测试必须改为断言行为，除非该调用本身是明确外部协议。
- 依赖真实网络、真实当前时间、真实用户目录或随机顺序的单元测试必须改写。
- 修复代码但没有复现原问题的测试不得视为完成。
- 未镜像源码目录、文件名无法映射到唯一源码模块，或混合多个源码 owner 的 pytest 文件必须移动、重命名或拆分。

### 允许保留

集成测试允许访问真实数据库或服务，但必须使用隔离环境、明确标记和可重复数据；单元测试仍必须覆盖核心行为。

### 反例

```python
def test_writer_calls_private_helper(mocker: MockerFixture) -> None:
    writer = SnapshotWriter()
    spy = mocker.spy(writer, "_write_table")

    writer.write(snapshot())

    spy.assert_called_once()
```

```python
def test_rebalance_uses_today() -> None:
    assert should_rebalance(portfolio()) is True  # 随日期和时间变化
```

### 正例

```python
def test_snapshot_writer_publishes_table_and_lineage_atomically(
    tmp_path: Path,
) -> None:
    writer = SnapshotWriter(output_root=tmp_path)

    writer.write(snapshot())

    assert read_table(tmp_path / "snapshot.parquet") == expected_table()
    assert read_lineage(tmp_path / "snapshot.lineage.json") == expected_lineage()
    assert not list(tmp_path.glob("*.tmp.parquet"))
```

```python
def test_rebalance_starts_at_configured_cutoff() -> None:
    rules = RebalanceRules(cutoff=time(14, 30))

    assert should_rebalance(
        portfolio(),
        as_of=datetime(2026, 7, 14, 14, 29, 59),
        rules=rules,
    ) is False
    assert should_rebalance(
        portfolio(),
        as_of=datetime(2026, 7, 14, 14, 30, 0),
        rules=rules,
    ) is True
```

### 完成前复核

- 将每个代码行为变化映射到至少一个测试。
- 对缺陷修复确认测试在旧实现上确实失败。
- 检查测试是否固定时间、seed、配置和输入顺序。
- 检查断言是否面向 public 行为，而不是 private 实现细节。
- 将每个新增、修改、移动或重命名的 pytest 文件映射到唯一源码模块，并确认目录与文件名符合镜像规则。

---

## PY-027 完成前必须执行统一复核

### 触发条件

任何 Python 代码任务准备标记为完成时触发。

### 必须判断的语义

必须判断：

- 哪些文件被新增、修改、移动、重命名或删除；
- 哪些规则被触发；
- 哪些检查由工具执行；
- 哪些检查只能人工完成；
- 哪些命令因环境或仓库缺失而未执行。

### 必须执行

完成前必须按以下顺序复核：

1. 获取变更文件清单。
2. 检查所有适用 Python 文件的 filepath 标识。
3. 检查 pytest 文件是否镜像唯一源码模块的目录与文件名。
4. 检查新增或修改的 private helper，尤其是单调用点转发层。
5. 检查类型、`None`、容器所有权、稳定数据模型和返回契约。
6. 检查异常、日志、资源、时间、环境、随机数和全局状态。
7. 检查继承、God Object、回测/实盘重复、Repository 越界和数值热路径。
8. 检查无用代码和过期注释。
9. 运行仓库配置的 formatter、linter、type checker 和 tests。
10. 在完成汇报中准确列出已运行、未运行和保留例外。

不得把“代码看起来正确”当作复核。

### 必须删除或改写

- 检查失败对应的问题必须修复，或在确有阻塞时明确报告，不能标记为通过。
- 未运行测试时，不得写“所有测试通过”。
- 保留规则例外但没有边界说明的代码必须删除或补充依据。

### 允许保留

仅当仓库缺少对应工具、依赖不可用或环境阻塞时，允许不运行某项命令；完成汇报必须写明未运行项、原因和剩余风险。

### 反例

```text
已完成，代码应该没有问题，所有测试通过。
```

实际情况却是没有运行 type checker，也没有运行测试。

### 正例

先按仓库配置执行；以下仅为命令形式示例：

```bash
git diff --name-only --diff-filter=ACMR -- '*.py'
ruff format --check src tests
ruff check src tests
mypy src
pytest -q
```

检查单调用 helper 的示例：

```bash
rg -n 'def _write_table|_write_table\(' src tests
```

合格完成汇报示例：

```text
完成范围：src/io/snapshot_writer.py、tests/io/test_snapshot_writer.py

已复核：
- filepath 标识与实际路径一致；
- 删除单调用、无语义的 _write_table 转发 helper；
- 保留 _publish_snapshot_atomically，因为它统一临时路径、lineage 和原子替换边界；
- 未新增 Any、可变默认参数、隐式 None 语义或全局状态。

已运行：
- ruff format --check src tests
- ruff check src tests
- mypy src
- pytest -q tests/io/test_snapshot_writer.py

未运行：
- 全量集成测试；当前环境没有测试数据库。剩余风险仅限数据库 adapter 集成。
```

### 完成前复核

- 完成汇报必须与实际执行记录一致。
- 任何失败、跳过或未运行项必须显式列出。
- 对保留的简单 helper、兼容层、循环、继承或全局缓存说明其允许保留条件。
- 不得使用“应该”“大概”“看起来”等词替代验证结果。

---

# AI 执行清单

以下清单是 PY-027 的压缩执行视图，不替代各规则正文。

```text
[ ] [PY-001] 已确定本次修改的语义单元和 owner doc，并完成同域清理。
[ ] [PY-002] 所有适用 *.py 文件都有正确 filepath 标识。
[ ] [PY-003] import 无 I/O、无 sys.path 修改、无 wildcard import。
[ ] [PY-004] public API 类型完整；Any 已在外部边界收口。
[ ] [PY-005] 只读容器使用 Sequence/Mapping；无可变默认值。
[ ] [PY-006] 每个 None 都有独立语义。
[ ] [PY-007] 稳定结构已使用命名数据模型。
[ ] [PY-008] 单调用 private helper 不是无语义转发层。
[ ] [PY-009] 函数没有用模式开关拼接不同工作流。
[ ] [PY-010] 名称表达领域对象、动作、方向和单位。
[ ] [PY-011] 成功、缺失、拒绝和失败契约明确。
[ ] [PY-012] 外部输入已在边界校验并转换。
[ ] [PY-013] exception 只在恢复、翻译、回滚或终止边界捕获。
[ ] [PY-014] 日志使用 f-string 和结构化上下文；无敏感信息、无重复堆栈。
[ ] [PY-015] 外部资源有明确 owner 和释放路径。
[ ] [PY-016] 核心逻辑不直接读取时间、环境、随机数和机器路径。
[ ] [PY-017] 业务常量来自 owner doc，并表达版本、单位和精度。
[ ] [PY-018] 无 Singleton、service locator 和模块级可变业务状态。
[ ] [PY-019] 无深继承树；共享实现使用组合、协议或纯函数。
[ ] [PY-020] 无 God Object；orchestrator 只编排。
[ ] [PY-021] 回测与实盘共享同一核心业务逻辑。
[ ] [PY-022] Repository 只承担持久化语义。
[ ] [PY-023] 数值热路径批量化；保留循环具有真实顺序依赖。
[ ] [PY-024] DataFrame ownership 和赋值明确。
[ ] [PY-025] public API 有具体调用示例；注释解释契约、原因或不变量；无注释掉的旧代码。
[ ] [PY-026] 测试可重复，且 pytest 文件镜像唯一源码模块的目录与文件名。
[ ] [PY-027] 已运行并准确报告 formatter、linter、type checker 和 tests。
```

不合格完成汇报示例：

```text
基本完成，应该都符合规范。
```

合格完成汇报示例：

```text
规则复核完成。发现并删除 1 个单调用无语义转发 helper；保留 1 个原子发布 helper，边界为临时文件、lineage 和 rename。ruff、mypy、目标测试均通过；全量集成测试未运行，原因已列出。
```
