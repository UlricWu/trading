# 服务器部署基线候选

## Status

`open`

## Goal and Scope

为测试环境和正式环境选择一套可恢复、可验证且权限边界明确的服务器部署基线，使当前发布
工作流能够在满足部署契约后接入真实部署。

本 change 只覆盖部署传输方式、不可变部署单元、运行时、服务切换、健康检查和回滚基线。
不改变现有分支流转、release PR、semantic-release 或测试与生产部署源；不授权创建服务器
账号、目录、systemd unit、GitHub secrets 或部署 workflow。

## Baseline and Candidate Design

当前正式基线由 [`docs/engineering/release_workflow.md`](../../docs/engineering/release_workflow.md)
定义：仓库只实现代码晋级和发布编排，尚未实现服务器部署；部署认证、目标、运行时、回滚、
健康检查和人工接管边界仍待 owner 决策。

若后续没有容器或集群平台约束，候选基线为：

```text
GitHub Environment secrets
  -> SSH（固定 host key、独立低权限部署用户）
  -> releases/<commit-or-tag> 独立目录
  -> 独立 Python virtualenv
  -> current/previous 原子软链接
  -> systemd service
  -> HTTP readiness 检查
  -> 失败时切回 previous 并重新检查
```

该候选不排除 wheel、Docker、Kubernetes、部署 webhook 或 self-hosted runner；这些约束明确后
再比较并收敛最终设计。

## Decisions and Evidence

- 已确认当前发布工作流没有服务器部署实现，不能把部署源准备完成表述为服务器部署成功。
- 已确认候选方案不构成正式设计或实施授权，因此从 owner doc 迁入本 change。
- 尚未获得目标基础设施、认证方式、运行环境和运维 owner 信息，当前没有足够依据采用或
  拒绝该候选。

## Acceptance

决策前必须明确 [`docs/engineering/release_workflow.md`](../../docs/engineering/release_workflow.md)
“开始实现部署前的准入条件”列出的全部信息，并验证：

1. 认证方式满足最小权限、身份校验、轮换和测试/生产隔离要求。
2. 部署单元能够绑定确定的 commit SHA、tag 或 digest，并安装到隔离目录。
3. 服务切换、失败回滚和回滚后健康检查具有可重复执行的明确步骤。
4. readiness 检查能够验证环境、版本和关键依赖，而不只检查进程或端口。
5. 持久化数据变化与旧版本回滚的兼容边界已经明确。
6. 该候选已与实际可用的 wheel、容器、Kubernetes、webhook 或 self-hosted runner 约束比较。

采用时必须同步更新适用的 owner docs、workflow、部署入口、配置和测试；拒绝时必须记录导致
候选不适用的约束或 owner 决策。

## Recovery

当前阻塞项是部署目标、认证方式、运行时、服务管理、配置注入、readiness、回滚、数据兼容、
报警和人工接管 owner 均未确定。没有已接受的残余风险。

下一步是由 owner 提供上述约束，再基于 Acceptance 比较候选并明确采用、修改或拒绝。候选
采用前不得新增默认生效的服务器部署入口。
