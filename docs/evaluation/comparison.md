# 回归比较与 Memory 检索消融

比较评测把相同任务交给不同条件下的 Agent，并在相同规则下重复运行。它用于区分偶然的一次成功与可观察的版本差异；三次重复只用于描述结果，不构成统计显著性结论。

本版固定 `coding-benchmark@1.1` 四题，每题每组重复三次：

| 组 | 代码来源 | 自动 Memory 检索 | 比较问题 |
|---|---|---|---|
| `old-off` | 固定 v0.52 commit `9d498f91f7ca21caa7f4f842fba0a49075754f20` | 关闭 | 基线 |
| `current-off` | 冻结后的 v0.53 commit | 关闭 | 与 v0.52 比较 |
| `current-on` | 同一 v0.53 commit | 开启 | 与 v0.53 关闭组比较 |

两条边分别是 `old-off → current-off` 和 `current-off → current-on`。模型别名、题目、初始文件、grader、权限和预算保持一致；只改变边中声明的来源 revision 或检索开关。完整批次计划 36 个独立槽位，执行顺序按题目和重复序号轮换组顺序。

## Memory 材料和隔离

四份记忆种子只描述接口约定、项目背景和调查方法，不含修复补丁、答案、隐藏测试或 grader 内容。每个 trial 都从同一语义快照新建目标版本 `MemoryStore`，再使用目标版本 `MemoryRetriever` 和现有 Context 检索逻辑。启用组最多检索四条、总注入上限 2,400 字符。关闭组不执行检索。

Memory 文件放在 trial workspace 外，且每个 trial 使用新的 workspace 身份、HOME、Memory 和临时目录。种子不会从一个 trial 带到另一个 trial。报告只记录固定材料摘要、检索是否发生、尝试次数和失败类别，不保存注入正文。检索失败按 Context 既有降级处理，但该样本会标成条件异常，不能当作正常开启组样本解读。

比较 worker 使用目标 checkout 中的 `AgentRuntime.run()`、工具执行器和权限闸门。可见工具沿用编码基准的六个文件/计算工具，不开放 shell、Memory CRUD、MCP、Skills、References 或子代理。Agent 停止后，独立 grader 检查工作区；Agent 自述和 Runtime verification evidence 都不替代独立评分。

题目合同若要求提交回归测试，runner 会在干净副本中预建空的 `tests/` 目录。六工具中没有建目录工具，这个空目录让 Agent 能按合同新增测试文件；不会预放测试代码或答案，所有比较组使用同样的工作区准备规则。

## 指标和缺失值

- **独立验收通过率**：grader 通过数 / 可评分 trial，同时报告计划槽位、终态、基础设施错误和未运行数。
- **严格任务成功**：Agent 正常文本结束、State 终态为 `done`、没有运行错误、grader 通过且清理完整。
- **调用情况**：模型请求、成功响应、工具调用、权限拒绝及工具成功/失败/拒绝分别计数；子代理调用固定为 0。
- **Token 与成本**：输入/输出分开，标记 provider、estimated、mixed 或 unavailable 来源。成本使用冻结价格快照和 `Decimal`；缺价或缺用量时保持 `null`，不视作精确账单。
- **耗时**：Agent 与 grader 分开列出观测数量、median 和 p95；没有观测时为 `null`。
- **无效重复**：同一进展阶段内再次调用相同工具与规范化参数，结果也相同且没有新的结构化 State 事实时计数。路径参数规范化为 workspace 相对路径；正文只在内存中哈希，不进入报告。
- **恢复成功率**：编码比较不适用，始终为 `null`；可靠性历史报告独立引用。

报告按题展示通过数、分母、百分点差异、用量和耗时差异，并为每个配对槽位提供结果及相对证据路径。来源、权限、预算、题集、grader 或槽位不匹配时，报告列出具体不可比较原因，不能缩小分母后仍声称原比较完成。

## 评分资格与连接诊断（报告规则 2）

原始 grader 是否返回、是否通过，与 trial 能否用于能力比较分别记录。Provider 连接、超时、HTTP/协议失败，以及 worker、grader 或清理故障排除在能力分母之外；Agent 自身代码异常、执行超时、权限拒绝和预算耗尽仍作为任务失败保留。基础设施错误每个槽位只计一次。

组汇总分别给出原始 grader 计数、有效能力样本和排除原因。配对差异只用双方均有效的样本；缺少有效配对时差异为 `null`。`batch_complete` 只表示全部槽位已有终态，`comparison_complete` 还要求全部预声明配对有效且合同条件一致。原始 token 和失败耗时继续显示用于诊断；保守估算不代表已发生用量或正常模型运行代价。

新结果的可选 `agent.error_diagnostic` 只含白名单类别、底层异常类别、整数 errno 与 TLS 校验码；异常原因链最多读取四层，不保存异常消息、URL、认证头或正文。旧结果缺失诊断时继续保持未知。该诊断不会改变 HTTP 传输、代理支持或重试行为。

对旧批次生成独立修正版报告，使用源归档外的全新目录：

```bash
PYTHONPATH=src python -m mini_agent.evaluation report-comparison <run-dir> --output <new-derived-dir>
```

派生报告携带报告规则版本、源 run ID 和输入证据摘要。已有目标目录会被拒绝；原 trial、账本及报告保持原样。没有 `--output` 时仍在原 run 目录重建派生报告，沿用已有命令行为。比较未完成或发现回归返回 1，配置/证据错误返回 2；输出报告不代表启动了新试验。

## 命令

命令示例使用 Bash/zsh。模板中的当前 revision、模型 profile alias 与价格是占位值；先复制模板并填入审阅后的本地条件。价格若无法确认，应填写 `null`，并明确快照来源。真实 endpoint、model ID 和 API key 只留在本地配置。

```bash
PYTHONPATH=src python -m mini_agent.evaluation validate-comparison /path/to/filled-spec.json
PYTHONPATH=src python -m mini_agent.evaluation plan-comparison /path/to/filled-spec.json --output /private/tmp/v053-review-plan
PYTHONPATH=src python -m mini_agent.evaluation self-test-comparison --output /private/tmp/v053-offline-comparison
```

计划清单冻结当前完整 commit、运行环境、题目和 grader 摘要、模型 binding 摘要、Memory 材料和 36 槽顺序。清单生成后应审阅所有题目、模型别名、预算、价格快照和来源。本文档不授权真实模型调用。

只有用户明确批准当前审阅清单后才执行：

```bash
PYTHONPATH=src python -m mini_agent.evaluation run-comparison /path/to/review-plan/comparison-plan.json --live --output /path/to/new-run
PYTHONPATH=src python -m mini_agent.evaluation report-comparison /path/to/new-run
```

Live 不会自动重试、补跑或替换槽位；中断后保留当前账本和未运行槽位，新尝试需新的 run ID。报告命令从 plan、账本和证据摘要重建报告，不连接 provider、不再次运行 grader，也不修改原始 trial 证据。

## 本版范围

本消融只研究固定 Memory 材料在同任务中的自动摘要检索。它不衡量跨任务记忆学习，不推断到其他题集、模型或记忆策略。首批三组使用同一模型绑定；模型/provider 对比不属于这 36 个 live 槽位。v0.51 编码与 v0.52 可靠性历史基线仍单独标记未完成。
