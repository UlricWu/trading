# Python 编码风格：AI 硬规则

- **状态**：强制执行
- **适用范围**：仓库自有、非生成、非 vendored、非第三方源码的 Python 代码。
- **Owner 边界**：本文件是 Python 编码规范的唯一入口和 owner；[细则](references/python_coding_style_rules.md) 是本规范的按需正文。
- **规范词**：“必须”“不得”“仅当”“否则删除或改写”均为硬规则，不表示建议。
- **业务语义来源**：业务不变量、交易规则、字段定义、时间口径、费用口径和状态机以对应 owner doc 为准。代码不得自行发明另一套语义。

## 按变更查阅

修改 Python 时，用下表定位本次变更触发的规则，再读取对应细则；不要求通读全部规则和示例。
规则只在本次新增、修改或直接影响的范围内复核。纯 Markdown 文案/链接修改、只读问答不触发 Python 代码检查。
新增或修改 public class、function、method 同时触发 PY-004、PY-025；缺陷修复和行为变更同时触发 PY-026。
完成检查按 PY-027 选择受影响范围，CI 和发布必需检查仍由发布 owner 定义。

| 本次改动 | 查阅规则 |
| --- | --- |
| Python 文件增删改、移动、重命名及直接造成的清理 | [PY-001 修改范围](references/python_coding_style_rules.md#py-001) |
| 自有 Python 文件新增、修改、移动或重命名 | [PY-002 路径标识](references/python_coding_style_rules.md#py-002) |
| import、模块级初始化、配置/文件读取或注册逻辑 | [PY-003 import 副作用](references/python_coding_style_rules.md#py-003) |
| public API、构造函数、协议、回调、序列化边界或 SDK 适配器 | [PY-004 类型契约](references/python_coding_style_rules.md#py-004) |
| 序列/映射参数、返回值、保存或修改 | [PY-005 容器所有权](references/python_coding_style_rules.md#py-005) |
| None、可选值、空集合或缺省配置 | [PY-006 None 语义](references/python_coding_style_rules.md#py-006) |
| 数据跨函数、模块、队列、缓存、存储或序列化边界 | [PY-007 稳定数据模型](references/python_coding_style_rules.md#py-007) |
| private helper、wrapper、adapter 或转发层 | [PY-008 最小抽象](references/python_coding_style_rules.md#py-008) |
| 布尔/模式开关、环境分支或多种返回路径 | [PY-009 单一语义](references/python_coding_style_rules.md#py-009) |
| 模块、类、函数、参数、变量、异常或常量命名 | [PY-010 命名](references/python_coding_style_rules.md#py-010) |
| 返回值、缺失、拒绝、失败或联合类型 | [PY-011 返回契约](references/python_coding_style_rules.md#py-011) |
| JSON、CSV、数据库、环境、CLI、HTTP、队列、SDK 或用户输入 | [PY-012 外部输入边界](references/python_coding_style_rules.md#py-012) |
| 异常、重试、降级或任务边界 | [PY-013 异常边界](references/python_coding_style_rules.md#py-013) |
| 运行时日志、异常记录、审计字段或请求上下文 | [PY-014 日志](references/python_coding_style_rules.md#py-014) |
| 文件、连接、事务、锁、临时目录、会话、线程池或进程池 | [PY-015 资源生命周期](references/python_coding_style_rules.md#py-015) |
| 核心逻辑或可重复测试中的时间、环境、随机数、路径或网络状态 | [PY-016 不确定输入](references/python_coding_style_rules.md#py-016) |
| 业务常量、阈值、状态迁移或字段口径 | [PY-017 业务常量](references/python_coding_style_rules.md#py-017) |
| 全局变量、缓存、Singleton、service locator、client 或注册表 | [PY-018 全局状态](references/python_coding_style_rules.md#py-018) |
| 基类、抽象类、mix-in、模板方法、框架扩展或类层次 | [PY-019 继承](references/python_coding_style_rules.md#py-019) |
| 类或编排器新增职责 | [PY-020 职责边界](references/python_coding_style_rules.md#py-020) |
| 回测、实盘、回放、信号、风控、持仓或订单逻辑 | [PY-021 共享核心](references/python_coding_style_rules.md#py-021) |
| Repository、DAO、storage adapter、数据库、Parquet 或表返回接口 | [PY-022 持久化边界](references/python_coding_style_rules.md#py-022) |
| 大规模数组、表、订单簿、因子或批量计算 | [PY-023 数值热路径](references/python_coding_style_rules.md#py-023) |
| DataFrame/Series 等可变表对象的输入、返回、缓存或修改 | [PY-024 表所有权](references/python_coding_style_rules.md#py-024) |
| public API 新增/修改，或注释、docstring、TODO、弃用说明、owner 引用改动 | [PY-025 具体调用示例](references/python_coding_style_rules.md#py-025) |
| pytest 文件改动、功能/行为/边界变化、缺陷修复、共享逻辑重构或兼容路径删除 | [PY-026 回归与测试布局](references/python_coding_style_rules.md#py-026) |
| 本次有 Python 文件改动，准备完成汇报 | [PY-027 按影响验证](references/python_coding_style_rules.md#py-027) |
