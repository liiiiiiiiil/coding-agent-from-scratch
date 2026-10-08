# 第 53 课：怎样公平比较两个 Agent 条件

代码快照：`v0.53` · 相邻差异：`v0.52..v0.53` · 命令：Bash/zsh

[上一课：故障注入与恢复评测](52-reliability-evaluation.md) · [教程索引](README.md)

本课里我们要回答一个看起来简单、实际很容易弄错的问题：新版本是否真的比旧版本好？Agent 每次回答会有波动。只看一次成功，可能把偶然当成改进；只换掉旧版本，也可能把题目或权限变化误当成版本效果。

v0.52 已经能冻结编码题目、隔离工作区并让独立 grader 评分，但它只运行一个版本的题目集。v0.53 加上一份比较合同，把三个条件排进同一个批次：旧版本关闭记忆检索、当前版本关闭检索、当前版本开启检索。这样前两组回答版本是否退步，后两组回答自动 Memory 检索带来什么变化。

## 本课目标

读完后，你应能看出一次公平比较需要先固定哪些条件，解释 36 个 trial 如何配对，并区分离线 Runtime 自测与真实模型成绩。Memory 检索消融只表示比较启用和关闭自动摘要检索的差别。

## 前置条件

准备 Python 3.10+ 和 Git；先了解上一课的固定题集、隔离工作区与独立 grader。当前源码链接固定到 `v0.53`，tag 尚未由维护者创建时，先阅读默认分支的源码，不要把未存在的 tag 当成已冻结快照。

## 先准备代码快照

Git tag 是给一份代码快照起的固定名字，教程用它保证示例指向同一份源码。本课声明 `v0.53`；新 tag 需要由维护者手动创建。切换前先查看本课使用的源码差异：

```bash
git checkout --detach v0.53
git diff v0.52..v0.53
git diff --stat v0.52..v0.53
```

第一条命令把工作区切到本课版本；第二条命令展示它相对 v0.52 的改动。本文命令假定在 Bash/zsh 的仓库根目录执行。若维护者还没有创建 `v0.53` tag，应先停在当前分支阅读源码，不能假定该名字已经可切换。

## 新增与改动文件

先看这些文件，能找到比较条件在哪里校验、目标 Runtime 从哪里装配、报告怎样重建：

| 文件 | 作用 |
|---|---|
| [`comparison_schema.py`](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_schema.py) | 定义比较清单、批次账本和单次结果的严格字段。 |
| [`comparison.py`](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison.py) | 冻结来源和顺序，协调逐题执行并保留槽位状态。 |
| [`comparison_sources.py`](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_sources.py) | 从完整 commit 创建临时源码副本，并在请求前检查接口。 |
| [`comparison_worker.py`](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_worker.py) | 装配目标版本的 Runtime、工具权限和 Memory 检索。 |
| [`comparison_report.py`](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_report.py) | 校验原始证据摘要并重建配对报告。 |

## 关键流程：v0.52 与 v0.53 的数据流

上一版的路径是：固定题目创建工作区，Agent 使用六个文件与计算工具，停止后 grader 检查结果，最后汇总单个版本的试跑：

```text
v0.52 基线
[旧] validate-suite
  → [旧] 一份固定题集和一组权限
  → [旧] EvaluationRunner.run_case()
  → [旧] AgentRuntime + 六工具 + PermissionGate
  → [旧] 独立 grader
  → [C] report-suite 汇总 trial
```

它能回答“这个版本做得怎样”，却不能保证两次运行只差一个条件。v0.53 把比较条件和顺序先冻结，再从对应 commit 启动目标 Runtime：

```text
v0.53 比较
[+] validate-comparison → 检查三组条件与两条比较边
  → [+] plan-comparison 冻结题集、来源、环境和 36 个槽位
  → [~] 为每个槽位创建独立 workspace / HOME / Memory
  → [+] 临时 checkout + 统一外部 worker
       ├─ [C] 目标版本 AgentRuntime、ToolExecutor、PermissionGate
       ├─ [C] current-on：目标版本 MemoryRetriever → Context
       └─ [B] 不装配 shell、Memory CRUD、MCP 或子代理
  → [C] 独立 grader 检查 Agent 停止后的工作区
  → [+] 原子账本 + trial/artifact 摘要
  → [+] report-comparison 校验引用后重建配对结果

失败或中断：[B] 留下错误槽位与未运行槽位；不自动重试或补跑。
```

图中，[旧] 表示上一版已有，[+] 表示新增，[~] 表示扩展，[C] 表示主要消费者，[B] 表示责任边界。比较器只协调运行；每个 Agent 的 Runtime、工具和权限来自选中的 commit。这样旧版不会误用当前版核心代码。

## 实现拆解

### 比较条件为什么要先冻结

同一轮比较有三组：`old-off`、`current-off`、`current-on`。`off` 和 `on` 指是否允许 Context 自动检索固定记忆摘要。每题三次，所以 `4 题 × 3 组 × 3 次 = 36 个槽位`。每题内轮换组顺序，减少总让某一组先运行造成的顺序偏差。

比较边是“只允许变化的条件清单”：

| 比较边 | 唯一允许变化 | 能回答的问题 |
|---|---|---|
| `old-off → current-off` | 源码 commit | v0.53 是否相对 v0.52 退步 |
| `current-off → current-on` | Memory 检索开关 | 固定记忆的自动摘要检索是否改善结果、增加多少代价 |

如果 suite、grader、工具权限、预算、绑定或其他字段不一致，比较器会报出不可比较原因。它不会把问题较多的槽位静默丢掉，让剩下的样本看起来完整。

schema 通过组字段差异校验比较边，槽位顺序由题目索引和重复序号生成。核心轮换规则很短：

```python
shift = (case_index + repetition - 1) % len(groups)
rotated = groups[shift:] + groups[:shift]
```

`shift` 决定每题每次从哪一组开始；完整顺序在模型运行前写入计划。实现见 [`comparison_execution_order()`](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_schema.py)。

### Memory 消融如何保持公平

消融实验的意思是固定其他条件，只打开或关闭一个能力。本版准备四份很短的语义记忆，只包含接口约定和调查方法。每个 trial 都按自己的 workspace 身份重建同一快照，避免一个 trial 学到另一题结果。

关闭组跳过自动检索；开启组使用目标版本自己的 `MemoryStore`、`MemoryRetriever` 和既有 Context 逻辑，最多选四条、总计 2,400 字符。Memory CRUD 工具不注册，因此 Agent 不能改写实验材料。Memory 目录在 workspace 外，证据只保存材料摘要、是否发生检索及失败类别，不保存注入正文。

检索失败仍按 Context 已有的降级方式运行，但报告会标记实验条件异常。这个样本保留在账本里，不能解释成“检索正常开启”的结果。完整材料边界见[比较说明](../evaluation/comparison.md)和[四份冻结种子](https://github.com/liiiiiiiiil/agent-from-scratch/tree/v0.53/tests/fixtures/evaluation/comparison/memories)。

### 运行与观察

首先可用固定响应跑离线自测：

```bash
PYTHONPATH=src python -m mini_agent.evaluation self-test-comparison --output /private/tmp/v053-comparison-offline
PYTHONPATH=src python -m mini_agent.evaluation report-comparison /private/tmp/v053-comparison-offline
```

自测仍装配真实 Runtime 和 PermissionGate，但模型响应来自本地 fixture，不访问 provider。它会检查 36 个槽位从各自初始文件独立运行、grader 能接受已知修复，并旁路探测工具失败、权限拒绝和相同调用重复。报告里 `run_kind` 是 `fixture`，不能把这些通过数算成模型成绩。

真实评测前，复制 [`comparison.template.json`](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/tests/fixtures/evaluation/comparison/comparison.template.json)，填入当前完整 commit、已配置的 profile 别名和审阅后的价格快照，然后生成计划：

```bash
PYTHONPATH=src python -m mini_agent.evaluation validate-comparison /path/to/filled-spec.json
PYTHONPATH=src python -m mini_agent.evaluation plan-comparison /path/to/filled-spec.json --output /private/tmp/v053-review-plan
```

计划会显示题目、成功标准、三个版本/检索组、模型来源摘要、预算、价格、Memory 材料摘要和完整顺序。生成计划不会请求模型。只有用户审阅并明确批准这份清单后，才能执行带 `--live` 的命令：

```bash
PYTHONPATH=src python -m mini_agent.evaluation run-comparison /private/tmp/v053-review-plan/comparison-plan.json --live --output /path/to/new-run
```

每个槽位只启动一次。运行中断后，账本保留当前状态与未运行槽位；修复实现后应冻结新 revision 并开新 run，不能重用旧批次补洞。

报告把 grader 通过率和严格任务成功分开。正常文本结束不代表 grader 通过；grader 通过也不代表临时资源清理成功。模型请求、成功响应、工具调用、权限拒绝、输入/输出 token、Agent/grader 耗时和成本分别列出。没有用量或价格时显示 `null`；恢复成功率对这类编码比较也固定为 `null`。

## 为什么这样设计

把协调器与目标 Runtime 分开，可以在同一版 runner 里调用 v0.52 和 v0.53 的代码，同时避免当前模块混入旧版本。每个版本从完整 commit 的归档临时展开，接口预检发生在模型请求之前；不兼容时直接拒绝。

逐槽原子账本和不重试规则保留失败事实。报告从 trial 与 artifact 的摘要重建，因此汇总可以重复生成并定位到每个配对证据。独立 grader 与 Agent 的自述分开，避免把模型自我评价当成验收。

本版只得到固定题集、同一绑定和冻结记忆材料下的描述性观察。它不证明统计显著性，不衡量跨任务学习，也不代表其他模型或记忆策略。历史 v0.51 和 v0.52 live 基线仍未完成；v0.53 live 清单还需单独审阅。

## 本版特性、下一课与代码索引

本版交付比较合同和离线运行链路；真实比较仍要先经用户审阅后才能启动。阶段十四没有据此宣布完成，下一步是先复核历史 live 基线欠项，再由维护者决定后续课程主题。

- [比较合同与摘要](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_schema.py)
- [顺序编排与试验隔离](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison.py)
- [目标源码归档与 API 预检](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_sources.py)
- [Runtime worker 与检索旁路观察](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_worker.py)
- [指标与可重复报告](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_metrics.py) · [报告校验](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/comparison_report.py)
- [CLI 命令](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/__main__.py) · [冻结题集摘要](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.53/src/mini_agent/evaluation/benchmark.py)
