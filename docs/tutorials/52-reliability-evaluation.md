# 第 52 课：让 Agent 在故障后继续工作（v0.52）

上一课：[重复运行编码基准](51-coding-benchmark.md) · [教程总览](README.md) · 下一课：v0.53（回归比较）

> 代码快照：`v0.52` · 相邻差异：`v0.51..v0.52` · 命令环境：Bash/zsh
>
> 源码链接固定到 `v0.52`。该 tag 由维护者手动建立；建立前请在当前工作树阅读对应文件。

## 本课目标

文件读取抛出异常、工具权限被拒、后台进程迟迟不退出、MCP Server 断开，都会打断 Agent 的工作。我们需要分别判断两件事：运行时有没有守住边界，以及 Agent 是否真的完成了剩下的任务。拒绝危险操作只能证明安全边界正确；它不能代替任务成功。

本课冻结 `reliability-boundaries@1.6`，覆盖 18 类故障，组成 50 个离线边界槽位。再从中选七类运行真实 Agent，每类计划三次，共 21 个 live trial。读完后，你应能解释两类结果为什么分开统计，也能从一份原始报告追到故障事件和每条不变量。

## 前置条件与版本切换

需要 Python 3.10+、终端和 Git。第 51 课把同一道编码题从相同文件开始重复运行；本版把错误注入到工具、权限、持久化、MCP 和子代理边界中。维护者建立相邻 tag 后，可用下面的命令查看改动：

```bash
git checkout v0.51
git diff --stat v0.51..v0.52
git diff v0.51..v0.52 -- src/mini_agent/evaluation tests/fixtures/evaluation/reliability
git checkout v0.52
```

本课源码索引：[reliability_schema.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability_schema.py)、[reliability.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability.py)、[reliability_worker.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability_worker.py)、[faults.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/faults.py)、[reliability_report.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability_report.py)。

## 上一版的限制

v0.51 说明一次编码任务是否通过独立验收，也保留失败和未运行的 trial。但它没有办法回答更底层的问题：权限拒绝后 handler 有没有被执行？持久化提交失败后下一次模型请求有没有发生？MCP 断连后客户端是否又把同一次调用发了一遍？

故障评测要在已存在的安全边界上制造一次有限、可记录的故障。若为评分放宽 PermissionGate、绕过 Plan 或跳过清理，结果就不能说明真实运行时是否可靠。因此本版只在测试运行器管理的 trial 工作区、fixture Server 和子进程中注入故障，不改变正式运行时的安全规则。

## 新增与改动文件

| 位置 | 变化 | 作用 |
|---|---|---|
| [reliability/suite.json](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/tests/fixtures/evaluation/reliability/suite.json) 和 18 个场景目录 | 新增 | 冻结任务、初始文件、响应材料、反馈策略、grader 和摘要。 |
| [evaluation/reliability_schema.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability_schema.py) | 新增 | 定义与编码题分开的 suite、scenario、request 和 trial 结果合同。 |
| [evaluation/faults.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/faults.py) | 新增 | 只提供具名、有限次数的 handler 异常、提交失败、屏障和子调用超时。 |
| [evaluation/reliability_worker.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability_worker.py) | 新增 | 用真实 `AgentRuntime.run()` 装配 live 场景，并提供不调用模型的 fixture 边界探针。 |
| [evaluation/reliability.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability.py) 与 [reliability_report.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability_report.py) | 新增 | 按冻结顺序保留槽位，运行阶段 worker，并从原始 trial 只读重建报告。 |
| [evaluation/evidence.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/evidence.py)、[user_actions.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/user_actions.py) | 新增 | 检查受限证据引用；复用 CLI 的用户侧恢复决定逻辑。 |
| [evaluation/__main__.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/__main__.py)、[evaluation/report.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/report.py) | 修改 | 添加可靠性命令，并让旧报告明确跳过独立的可靠性结果格式。 |

| [budget.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/budget.py)、[runtime.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/runtime.py) | 新增/修改 | 父任务请求与摘要共享可持久预算；为验证、计划推进和最终回复留出额度。 |
| [context.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/context.py)、[state.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/state.py)、[session.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/session.py) | 修改 | 构造有界请求视图，依据独立完成事实收尾，并提交唯一预留及结算。 |

## 版本变更定位

图例：`[旧]` 上一版已有；`[+]` 本版新增；`[~]` 本版修改；`[C]` 主要消费者；`[B]` 本版边界。

上一版先建工作区、运行 Agent，再由 grader 检查结果。它能评估任务，但不会主动制造运行时故障：

```text
v0.51 基线：
[旧] validate-suite
  → [旧] 固定题目、工作区和权限
  → [旧] 单次 Agent worker
  → [旧] 独立 grader
  → [旧] schema 2 trial + 顺序槽位账本
  → [C] report-suite 重建编码任务汇总
```

v0.52 在同一类证据链上增加故障事件、不变量和恢复结果。对于崩溃恢复，它会让初始 worker 真正退出，再启动新 Python 进程领取派生 session：

```text
v0.52 变更：
[+] validate-reliability → 冻结 suite 与材料摘要
  → [+] 按场景和参数变体建立有序槽位
  → [+] fixture probe 或真实 AgentRuntime worker
       ├─ [+] FaultController 记录实际命中点
       ├─ [~] 真实 ToolExecutor / PermissionGate / ProcessManager / MCP / Subagent
       └─ [B] 崩溃场景：退出初始进程 → 新进程 prepare_resume().claim()
  → [+] invariant_status 与 grader_passed 分开保存
  → [+] report-reliability 验证 suite、槽位和 evidence 摘要后重建指标

失败分支：[B] 故障未触发或 evidence 不完整时单列；不重试请求、不填补槽位。
```

## 先看两种评分

“不变量”是某种行为必须始终成立的规则。例如，被拒绝的写文件调用不得进入 handler；已准入但结果丢失的调用不得在恢复时重放。场景把每条规则列为独立 ID，报告按证据给出 `passed`、`failed` 或 `incomplete`。

任务 grader 则检查 Agent 最终留下的文件和行为。比如它在读取出错后是否修好函数、是否用当前 generation 的验证证据证明修复有效。Agent 自己说“完成了”，或曾在旧 generation 通过测试，都不能替代这项验收。

Fixture 直接调用边界组件，不运行 Agent；它会检查注入点、不变量和清理，但 `grader_passed` 留为 `null`。因此离线矩阵能够证明“边界探针发现了预期事实”，不能证明“真实 Agent 能从故障中恢复并完成任务”。恢复成功率只在 live cohort 中计算，分母排除没触发、证据不完整、来源不一致和基础设施错误；六个恢复任务中的超时、权限拒绝、预算耗尽和恢复失败仍计入分母。专门的路径拒绝场景只检查 handler 未执行、文件不变和父任务安全终止，因此不进入恢复分母。

Suite 里有 18 个场景。离线运行还把四类 MCP 行为分别放在 stdio 和显式 loopback HTTP 上；durable commit failure 分别注入准入、单次结果和整轮提交，用户恢复反馈也有两个参数变体。所有变体分别保留 trial，不合并成一个结论。

七类 live 任务覆盖工具 handler 异常、路径拒绝、验证修复、进程等待超时、handler 后崩溃、MCP 断连和子代理超时。每类冻结用户任务、可见工具、逐项权限、预算、反馈来源和 grader。子代理 timeout 在子模型 provider 请求前注入；结果会标记 `injected`，不会伪装成真实子模型调用。

任务要通过，评测用到的测试本身也必须能够通过。六个恢复场景都提供公开 unittest：错误的初始代码必须失败，已知正确版本必须成功，而且测试数量不能为零。预检与真实运行采用相同检查，避免 Agent 修好代码后仍因缺失测试目录失败。

独立评分器还会在单独进程中检查数值行为。例如 `return value + value` 和 `return value * 2` 都能接受；它判断输出，而不限定代码写法。该进程有时间和输出上限，具体实现见 [reliability_grader.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability_grader.py)。进程等待场景的本地 fixture 则持续运行至 Agent 显式停止，模型回复较慢也不会让预期超时提前消失。

故障必须发生在模型理解的那个阶段。崩溃任务先告诉模型当前尚未崩溃，第一次修改完成后才停止进程；恢复后的会话再要求调查和重规划。子代理超时也先提供固定且足够的预算，避免还没请求模型就被预算挡住。如果目标故障没有发生，相关边界只能标记为证据不完整，不能推断边界失效。

有些兼容 Chat API 的服务要求后续请求带回响应中的 `reasoning_content`。这是服务端用于续接请求的可选协议字段；适配器会有界保留它，流式响应则按顺序拼接，再随 assistant 消息原样回传。它不作为终端正文或完成证据。具体处理见 [openai_chat.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/providers/openai_chat.py)。

## 关键流程

真实故障不能由“配置中写了一个故障名称”来证明。运行器会在指定边界实际命中时追加一条有序故障事件，事件保存目标、次数和证据摘要。以子模型超时为例，注入发生在发送 provider 请求前；若模型没有请求 `spawn_subagent`，场景就会记为未触发。

请求 schema 使用固定字符串 ID 选择已实现的驱动器和 grader，不能让清单导入模块或执行 Python。材料路径必须位于 suite 内，文件数和大小有界；所有场景文件、支持 fixture 和 suite 本身都参与摘要核对。

运行后，每个 trial 保留自己的初始工作区、状态/轨迹摘要、注入与用户反馈事件、恢复步骤、日志和差异。`suite-run.json` 在启动前就列出所有槽位；若 worker 崩溃，已完成结果仍在，剩余槽位保持未运行。报告读取冻结材料、账本和结果，再检查身份和 evidence SHA-256；它不会调用模型或工具。

## 运行与观察

先运行完整离线矩阵。该命令只启动本地受控 fixture，不会调用真实 API。预期 50 个槽位全部有结果，每个故障都应有实际命中记录；task grader 和 live 恢复率显示 `null`，因为这里没有 Agent task：

```bash
PYTHONPATH=src python -m mini_agent.evaluation self-test-reliability --output /private/tmp/mini-agent-reliability-offline
```

运行 live 前，先看 `validate-reliability` 输出中的全部冻结任务和权限。审阅者应确认拒绝路径、模拟反馈、公开验证命令和预算正是要测量的行为，再决定是否启动 21 个真实 trial。输出目录必须是新目录，失败请求不会自动重发：

```bash
PYTHONPATH=src python -m mini_agent.evaluation validate-reliability tests/fixtures/evaluation/reliability/suite.json
PYTHONPATH=src python -m mini_agent.evaluation run-reliability tests/fixtures/evaluation/reliability/suite.json --live --repeats 3 --output docs/evaluation/baselines/v0.52/live-<run-id>
PYTHONPATH=src python -m mini_agent.evaluation report-reliability docs/evaluation/baselines/v0.52/live-<run-id>
```

报告先看每个场景的 `fault_triggered` 和 `invariant_status_counts`，确认实际故障与边界证据；再看 grader、Agent 终态、cleanup 和恢复率。若不变量通过而 grader 失败，表示系统正确挡住了风险，但 Agent 没完成任务。若故障未触发或证据不全，这个 trial 不能证明边界可靠，也不能悄悄从结果中删掉。

### 找出预算花在了哪里

故障已触发、文件也已修正，却仍未完成验证时，需要知道每轮请求携带了多少重复信息。Token 是模型计算输入与输出长度的单位；本地按文字长度估算的数量可能与服务端实际计数不同。新版在 trial 的 `phases`（运行阶段记录）中提供 `request_diagnostics`，每个请求只留下数字，不复制提示词或推理正文。

先看 `message_groups`：它将受保护指令、结构化任务状态、历史消息、压缩摘要和运行提醒分开估算；再看单列的 `tool_schema_tokens_estimated`，即发送给模型的工具说明开销。推理字段的计数是消息总量的子集，不能重复相加。`usage_source` 表示服务端计数、估算或尚未观测；`refusal_reason` 说明下一次请求为何被预算闸门挡住。恢复阶段还记录前阶段累计用量，因此不能把崩溃续跑当成预算重新开始。

这些数字由 [reliability_diagnostics.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability_diagnostics.py) 生成。首次评测和崩溃续跑共用同一套评测指令，恢复仅增加必要说明，避免上层项目说明只在续跑阶段额外进入上下文。预算仍按冻结合同执行，是否改善真实模型的恢复表现需要独立 live 证据。

### 给验证与最终回复留出预算

Agent 每次请求模型都会再次发送上下文，包括此前的工具结果和服务端要求带回的推理字段。因此，即使文件已修好，剩余预算也可能放不下验证后最后一轮回复。只限制单次上下文大小，不能控制整个任务多轮累积的开销。

本版提供可选的父任务累计预算。它把主模型请求、历史摘要和恢复后的请求记在同一个账本中。CLI 默认关闭；在本地配置设置 `PARENT_TASK_TOKEN_BUDGET` 后，新任务才启用。故障评测显式启用固定上限，所以预算约束也成为冻结评测合同的一部分。

输入预留先按较宽松的估算启动：前两次真实用量观测之前，用本地估算的两倍再加 256。此后从最近八次观测取最大的正误差，也就是“实际输入减去估算输入”大于零的部分，再加 10% 与 256。这样可以让已观测的偏差影响下一请求，而不会用模型自己的估算去校准自己。计算由 [budget.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/budget.py) 完成：

```python
error = max(max(0, actual - estimate) for estimate, actual in samples)
reserved_input = ((raw + error) * 110 + 99) // 100 + 256
```

整数运算表示向上取整，避免浮点舍入改变临界额度。主执行输出最多 1024 tokens，摘要与收尾最多 512，仍服从较小的模型配置上限。未知用量按整次输入和输出预留计费；真实用量超过预留时照实记录并停止后续工具或完成。估算仍不是真实 tokenizer，不能证明任意服务商都绝不超额。

预算开启时，请求视图只保留最近两轮完整工具回合。一个完整回合包括 assistant 的调用及每个对应的工具结果；可以先缩短旧结果，也可以整轮省略，不能留下没有结果的调用。保留的推理字段原样带回，用户消息、受保护指令和当前任务状态保留。完整 history 不变，后续检查仍能看到原始证据。摘要本身也花 tokens，只有估计总成本更低并且有额度才调用同一个模型；否则使用本地有界视图。

运行时还会为验证、未完成计划步骤和最终回复预留请求额度。当前代验证已通过，或活动计划步骤已经全部完成，并且进程、子结果和崩溃问题都已收束时，才进入收尾视图。这个视图保留目标、约束和完成事实，不发送工具 schema。模型必须真正返回没有工具调用的最终回复，再经过原有完成检查；运行时不会代它完成计划或伪造验证。

下面展示可选预算接入同一个运行流程的位置。预算关闭时沿用原路径；开启时，提交失败或保护内容无法容纳都会阻止后续请求：

```text
[旧] ParentRuntimePolicy + AgentRuntime.run
  → [~] Context：当前状态 + 用户约束 + 两轮完整工具回合
  → [+] TaskBudgetController：预留输入、输出与必要收尾
  → [~] DurableToolBoundary：提交预留
  → [旧] 冻结 binding 请求模型
  → [+] 结算真实用量，或按整个预留计费未知调用
  → [~] 提交结算 → [旧] 工具授权/执行或原完成检查
[B] 提交失败：停止；超额：工具逐项回灌拒绝，不准入 handler
[C] CLI 可选启用；suite 1.6 live 固定启用
```

保存和恢复也必须记得已经花掉的预算。State 中的可选账本随会话原子提交；恢复时若请求已经发出但没有结算，只按该请求的整个预留计费一次，不能通过重启重新获得上限。旧会话没有账本时保持旧行为，不追补费用。

新报告额外展示三层事实：最终文件正确、当前代有独立验证、模型正常完成。三者可以不同；例如测试通过但最后回复因预算失败，就不会进入严格恢复成功的分子。历史数字回放只说明已观察请求在新额度下的准入情况，不能预测模型的新回复或恢复成功率。

## 为什么这样设计

分开记录边界和任务成功，可以避免一种指标遮住另一种事实：单纯拒绝危险调用可以是安全的，却仍可能让任务失败；最终文件正确也不能证明过程没有重复副作用。固定、具名的注入器能重跑同一个故障，并限制测试材料只能选择代码里已经实现的行为。

代价是离线组件探针与 live Agent task 的结果不同，必须分 cohort 阅读；21 个 live 样本也很小，只说明这些冻结任务和当前模型来源的观察结果。工作区是 Runner 建立的独立目录，但没有获得操作系统级沙箱。v0.52 不计算有效重复指标，不做模型版本或能力消融比较。

## 实现拆解

`ReliabilityScenario` 保存用户看得到的任务和冻结条件；`ReliabilitySuite` 检查 ID、版本和材料摘要；`FaultController` 只允许有限故障 ID，并且只有实际到达指定目标后才记作触发。`run_reliability()` 先原子写入完整顺序账本，然后逐槽位复制初始文件并启动 worker。

Live worker 通过 `AgentRuntime.run()`、`ParentRuntimePolicy`、真实 ToolExecutor 和当前配置的冻结 model binding 处理任务。它为每个场景重建精确的工具面与 PermissionGate；进程、MCP 和子代理 fixture 只由该 trial 管理。崩溃恢复保存原始 session 后，让初始 worker 以受控退出码结束；Runner 再启动一个全新的 Python 进程，由它调用 `prepare_resume(...).claim()`，核对源 session 未变化并重建场景权限。

`build_reliability_report()` 重新载入 run 目录里的冻结 suite，核对账本指纹和槽位顺序，再校验每个 trial 的场景摘要与 evidence 引用。它只对 live trial 形成恢复成功率分母；无法判定的槽位仍显示为缺失或未运行，而不会推测补全。

## 本版特性、下一课与代码索引

v0.52 的 `reliability-boundaries@1.6` 包含 18 类故障场景、50 个离线边界槽位、21 个计划 live trial、独立不变量与任务评分、用户反馈来源记录和可重建报告。预算候选接入正式 Runtime，但不改默认父任务预算、超时或权限策略。当前离线矩阵结果与 live 状态见[基线记录](../evaluation/baselines/v0.52/README.md)，场景和指标说明见[评测文档](../evaluation/reliability.md)。

下一课将固定条件比较代码版本和能力消融。源码索引：[reliability.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability.py)、[reliability_worker.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/evaluation/reliability_worker.py)、[session.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/session.py)、[resume.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/resume.py)、[delegation.py](https://github.com/liiiiiiiiil/agent-from-scratch/blob/v0.52/src/mini_agent/delegation.py)。
