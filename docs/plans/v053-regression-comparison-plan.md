# v0.53：回归比较与 Memory 检索消融

## 目标与边界

本版新增独立的比较结果格式，使用固定 `coding-benchmark@1.1` 回答两个问题：v0.53 相对固定 v0.52 是否退步；在 v0.53 内开启自动 Memory 摘要检索后，结果与用量怎样变化。比较由三个组组成：`old-off`、`current-off`、`current-on`。每题每组重复三次，共 36 个独立槽位；两条预声明边只允许源码版本或 Memory 开关变化。

所有组使用相同的题目、六工具面、权限、模型别名、轮次/超时合同、48 次工具调用上限和 64,000 累计 token 上限。Memory 种子是四题共用的语义快照，每个 trial 依据新 workspace 身份重建存储。只开启 Context 的自动检索；不注册 Memory CRUD 工具、不继承开发者 Memory/Skills/References/MCP，也不把检索正文写入比较证据。独立 grader 在 Agent 收束后运行；fixture 结果不是模型成绩。

本计划不授权付费调用。完成实现与离线验收后，生成新的审阅清单。只有用户审阅清单并明确启动后，才能运行 live 批次。live 运行不重试、不补跑、不替换槽位；中断保留账本和未运行槽位。新批次必须使用新目录。

## 合同与运行

新增 `mini_agent.evaluation.comparison_schema` 独立定义 `ComparisonSpec`、`ComparisonRun`、`ComparisonTrial`，schema 1，不修改编码题集或可靠性结果合同。Spec 校验字段白名单、完整 commit ID、固定预算、组间允许变化、唯一槽位、十进制定点价格和敏感字段；组数最多 8、重复最多 10、总槽位最多 320。

协调器使用当前比较代码顺序执行，目标版本通过 `git archive` 材料化到 runner 自建临时目录。外部适配器的 `PYTHONPATH` 只指向目标 checkout，并在请求前检查目标 Runtime、MemoryStore、MemoryRetriever 和评测接口。每题每次都新建 workspace、HOME、Memory 和临时路径。worker 直接调用目标版本的 `AgentRuntime.run()`、`ToolExecutor`、`PermissionGate` 和 `EvaluationRuntimePolicy`；不注册 shell、MCP、Memory CRUD 或子代理。

每槽先原子记成 `running`，收束后再原子提交 trial 摘要和终态。worker 工具观察器旁路计算无效重复，不修改 Runtime 决策。比较证据只存相对路径、摘要、用量、状态、grader 结果和有限日志；Memory 正文不写入试验结果。grader 从冻结题目读取，独立启动并限时。清理失败与中断按单独状态保留。

## 指标与报告

`comparison_metrics.py` 计算 grader 通过率、严格任务成功、Agent/评分器调用与结果数、权限拒绝、无效重复、子代理调用数、输入/输出 token 来源、成本、耗时观测数/median/p95 和未运行槽位。恢复成功率对编码比较固定为 `null`。价格用冻结的 USD 每百万 token 单价与 `Decimal` 计算；缺少价格或用量则成本为 `null`，不表示精确账单。

`report-comparison` 校验 spec、plan、source 摘要、槽位顺序、suite 副本、trial/artifact 路径与 SHA-256，再从原始 trial 重建 JSON 和 Markdown。每个配对槽位都有 grader 结果和相对证据路径。批次槽位齐全与所有预声明比较边可比较分别报告；失败、来源不匹配、Memory 异常和证据问题进入复核队列。三次重复只作为描述性观察。

## CLI

```bash
PYTHONPATH=src python -m mini_agent.evaluation validate-comparison <filled-spec.json>
PYTHONPATH=src python -m mini_agent.evaluation plan-comparison <filled-spec.json> --output <new-plan-dir>
PYTHONPATH=src python -m mini_agent.evaluation self-test-comparison --output <new-run-dir>
PYTHONPATH=src python -m mini_agent.evaluation run-comparison <review-plan.json> --live --output <new-run-dir>
PYTHONPATH=src python -m mini_agent.evaluation report-comparison <run-dir>
```

`validate-comparison` 不连接模型；模板的 revision、profile 与价格占位值必须先按本地条件填写。`plan-comparison` 固定当前干净 HEAD、题集、grader 校准、目标 checkout、运行环境、Memory 摘要和完整执行顺序。`self-test-comparison` 使用固定响应，运行真实 Runtime 装配的 36 槽离线矩阵和失败/拒绝/重复边界探针，不发送模型请求。`run-comparison` 仅在 `--live` 和用户明确批准后执行。`report-comparison` 不连接 provider、不重评分，也不修改原始证据，只生成可重建的派生报告。

比较配置模板和四份 Memory 种子位于 `tests/fixtures/evaluation/comparison/`。模板不是可直接运行的 live 清单，种子须在审阅时确认不含补丁、答案、隐藏测试或 grader 内容。

## 修改与验收状态

- [x] 独立 schema、来源快照、顺序计划、原子账本和报告重建。
- [x] 目标版本隔离 worker、六工具权限、固定预算和 Memory 自动检索开关。
- [x] 四题 Memory 种子、36 槽固定响应离线矩阵与拒绝/失败/重复探针。
- [x] 编码 suite 公共摘要和冻结 grader runner 辅助接口；旧结果格式保持独立。
- [x] CLI、比较说明、基线状态、教程和版本文档同步。
- [x] 专项测试、完整测试和固定响应离线矩阵验收通过；完整 pytest 为 849 passed，36/36 fixture trial 均可评分且 grader 通过，两个声明比较边均可比较。固定响应使 Agent 状态为 `blocked`，严格任务成功为 0/36；这些结果只验证 harness，不作为能力成绩。详细记录见 `evaluation-regression-plan.md`。
- [ ] 教程事实脚本要求的 v0.53 tag 由维护者手动创建；助手不执行 tag 操作。
- [ ] 用户审阅已冻结的 36 槽审阅计划并明确启动 live；本计划本身不授予付费调用。

阶段十四仍未整体完成：v0.51 suite 1.1 live 编码基线和 v0.52 suite 1.6 live 可靠性基线继续单列，v0.53 live 比较尚未运行。
