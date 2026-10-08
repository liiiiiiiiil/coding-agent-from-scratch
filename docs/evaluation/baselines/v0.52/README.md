# v0.52 Reliability 基线

当前可执行候选为 `reliability-boundaries@1.6`，摘要：`376170c1721cf6862c2c9faa3bc99195ff2c7a008afc0e7e89910bcbdd3502db`。它修正验证修复场景缺少目录/搜索工具的问题；运行时同时修复非法恢复引用的保存失败，并使用专用收尾提示降低预算预留。最新已运行的 live 仍是 suite 1.5 的 `live-20260930-01`：严格恢复 6/14，进程超时和崩溃场景各只触发 1/3，基线未完成。本次未运行新 live，历史批次与分母保持独立。修改与验收见[本轮修复记录](live-failure-repair-20261008.md)。

## Suite 1.5 系统修正候选

父任务主请求和摘要共用持久预算，崩溃恢复从会话继承；不增大评测上限。普通 CLI 默认 `PARENT_TASK_TOKEN_BUDGET=None`。请求视图保留最近两轮完整工具协议和用户约束，入选的推理字段原样带回，完整历史不变。完成条件仍由计划、验证、进程、子代理和恢复事实决定，必须取得真实无工具最终回复。

数字[成本回放](budget-repair-cost-replay.json)来自第六批原始证据：144 个决策、16 个历史拒绝、输入 885,685 / 输出 44,417。仅在历史请求和余额下应用新校准，130 个决策可准入，观察到的输入均在新预留内；6 个旧输出高于新输出上限。这些数字不预测新上下文或恢复率。预算定点修复后的两次 live 尝试也未获得可评分的模型恢复样本，见下文。

已修复一个生产缺陷：无持久化后台启动丢弃已完成的准入对象，再次预留 attempt，造成未结算记录。现直接执行原准入对象，完成与保存仍拒绝真实未结算 attempt。文件级实施记录与验收见[计划](../../../plans/evaluation-regression-plan.md)。

预算定点修复验收：全量测试 **812 passed**、相关回归 **95 passed**；离线矩阵 50/50 故障触发且不变量通过、无基础设施错误。教程结构、README 和 diff 检查通过；事实检查仍只缺用户手动建立的 v0.52 tag。[定点修复记录](stage-budget-repair.md)与[验收摘要](stage-budget-repair-validation.json)已归档。当前 live 前预检及代码指纹见各批次的 `review-plan.json` 和 `report.json`。

## Live 基线（suite 1.5）

新批次[`live-20260929-07/`](live-20260929-07/)（run ID `45e3d375-d69f-4318-80b7-bf284c078def`）按固定顺序执行 21 个 live 槽位。报告纳入 19 个可评分 trial，另 2 个槽位以 `provider_protocol_error` 结束；错误记录的 provider status 与 detail 均为空，脱敏诊断栈止于响应调用校验，现有证据不能确认根因，也不能将其归因为 HTTP 400。没有重试、补跑或替换槽位。报告核对 suite 摘要、代码 revision、runtime fingerprint 与 model binding 均一致；无证据断链、cleanup incomplete 或人工复核项。

报告纳入评分的 trial 中，17 次目标故障已触发且 17 项不变量通过；另两个基础设施错误 trial 的原始证据也显示目标故障已触发、不变量通过，但因 provider 协议错误不进入评分。权限拒绝的 3 个边界专用 trial 不进入恢复分母。其余可评分恢复 trial 成功 5/14（35.7%）。任务结果分层为：16 个任务 trial、10 个最终文件正确、5 个当前 generation 验证通过、5 个模型正常完成；这 5 个任务同时满足文件正确、验证和正常完成条件。

| 场景 | 故障触发 | 不变量通过 | Grader 通过 | 恢复成功 |
|---|---:|---:|---:|---:|
| `tool-handler-exception` | 2/3 纳入评分；另 1 个基础设施错误 trial 也触发 | 2/2 纳入评分；基础设施 trial 通过 | 2/2 纳入评分 | 0/2 |
| `permission-denied` | 3/3 | 3/3 | 不适用（边界专用） | 不适用 |
| `verification-repair` | 3/3 | 3/3 | 3/3 | 3/3 |
| `process-wait-timeout` | 2/3；1 次未触发 | 2/2；另 1 项不完整 | 0/3 | 0/2 |
| `crash-after-handler` | 1/3 纳入评分；1 次未触发；另 1 个基础设施错误 trial 也触发 | 1/1 纳入评分、1 项不完整；基础设施 trial 通过 | 1/2 纳入评分 trial | 0/1 |
| `mcp-disconnect` | 3/3 | 3/3 | 2/3 | 2/3 |
| `subagent-timeout` | 3/3 | 3/3 | 2/3 | 0/3 |

两条基础设施错误分别对应 `tool-handler-exception-default-r02` 与 `crash-after-handler-default-r02`。另有一个 `process-wait-timeout` trial 在故障注入前因 token limit 停止。上述槽位都按原结果保留，不自动重跑；因此 `tool-handler-exception`、`crash-after-handler` 和 `process-wait-timeout` 未达到每场景三次已触发且可评分的门槛。

`subagent-timeout` 的三次试验均通过“失败结果已领取”和“用量只结算一次”不变量，各只有一次子代理启动；但父 Agent 三次都因 token limit 停止，恢复为 0/3。该结果说明子代理边界和结算受控，父任务仍未完成规定恢复。Crash 基础设施错误 trial 的原始证据也显示副作用仅一次、未重放、源 session 只读且 grader 通过；恢复阶段的 provider 协议错误仍使它不能成为可评分恢复样本。

请求诊断记录 130 项决策：117 个 provider 响应、11 个预算拒绝、2 个 provider 协议错误。11 次拒绝均因受保护上下文无法放入剩余任务预算；请求未发送。117 个响应中有 114 个实际输入超过原始估算，但均没有超过校准输入预留，输出也没有超过已分配上限，观测预算超额为 0。该批证据显示预算边界得到遵守，但保守上下文准入仍会阻断调查和收尾。报告分别列出文件正确、当前 generation 验证与模型完成，不能用其中单项替代严格恢复成功指标。

完整可重建报告为[`report.json`](live-20260929-07/report.json)，运行前审阅清单为[`review-plan.json`](live-20260929-07/review-plan.json)，槽位账本为[`suite-run.json`](live-20260929-07/suite-run.json)；逐 trial 原始证据保存在 `trials/`。从这些原始文件重建的报告与归档报告字节一致。

## 定点修复后的独立 Live 批次（suite 1.5）

修复后重新执行预检，冻结任务及材料摘要仍为 suite 1.5，但 Runtime 指纹已更新为 `a32092e178ce86eb106f46a082801dca8b44c9b80d03d69d06661d2aee592a72`。两次运行使用独立目录，槽位都保留在原始账本中，没有请求自动重试或逐槽替换。

| 批次 | 槽位结果 | 恢复评分 |
|---|---|---|
| [`live-20260929-08/`](live-20260929-08/)（run ID `a3d95482-7d7f-4120-825f-a1bf7e7da430`） | 21/21 `provider_http_503` 基础设施错误，0 个报告 trial。 | 分母 0；无可评分恢复样本。 |
| [`live-20260929-09/`](live-20260929-09/)（run ID `4919dd0f-8d45-451d-bbe4-2683af110aff`） | 20/21 为 `provider_connection_error`；另 1 个工具异常 trial 收到模型响应后，在故障触发前因 `token_limit` 停止。 | 分母 0；该 trial 未触发故障。 |

第二批 20 个连接类错误的原始类型为 19 个 `ProviderConnectionError` 与 1 个 `ProviderTimeoutError`。三个 `verification-repair` trial 有实际故障命中记录，但随后模型请求失败，故仍属于基础设施错误；它们没有 grader 或不变量评分。21 个 trial 清理均完成，报告没有人工复核项或不可读证据。两批报告都可从冻结材料、账本和原始 trial 重建，重建文件与归档报告逐字节一致。

因此这两次修复后运行没有证明恢复率改善；suite 1.5 live 基线仍未完成。上一批 `live-20260929-07` 的 5/14 是不同 Runtime 指纹下的历史观察，不能与本批次合并，也不能替代本轮修复后的有效样本。

## 评测器修正

2026-09-29 复核确认六个恢复任务的公开测试目录缺失、预检与 live 评分不同；权限拒绝后的替代任务要求与正式 Runtime 立即终止合同冲突；0.8 秒进程寿命无法保证覆盖模型响应延迟。当前权限 scorer 访问不存在的 `ExecutionAttempt.arguments` 可复现 `AttributeError`，已改用参数摘要匹配；历史异常栈缺失，无法断言旧槽位必然在同一位置失败。行为评分也已接受等价实现。

修正合同与验收方法见[评测说明](../../reliability.md)。历史 trial、suite-run 和 report 均保留原样；旧版“未启动”标记不作为异常发生阶段的确证。`review-plan.json` 和 `live-binding-preview.json` 同样属于历史审阅材料，新批次开始前重新运行了 `validate-reliability`。

修正版离线矩阵已完成 50/50 槽位，故障全部触发、不变量全部通过，无基础设施错误或断链证据。运行 ID 为 `0326a821-b347-460a-8ec6-4ffd1c491c89`，原始结果位于本机 `/private/tmp/mini-agent-reliability-v052-suite11-fix-escalated/`。沙箱限制下的独立尝试有 8 个 loopback 注入错误，保留于 `/private/tmp/mini-agent-reliability-v052-suite11-fix/`；未逐槽替换。这些均为离线边界证据，不计作模型成绩。

## Suite 1.2 修正

第三批 live 复核后，修正普通及流式 OpenAI Chat 响应的 `reasoning_content` 往返保留；字段仅随普通 assistant history 和 session 保存，不作为终端正文、State 或 Trace 摘要输出。类型和长度有界，旧会话中不存在的推理字段不能补造；旧会话若已丢失服务端要求的数据，仍可能无法续接。

崩溃任务现在明确“当前尚未崩溃”，首次文件修改返回后才注入，恢复后再调查与重规划。子代理超时场景公开固定 32,000 token 的单次调查预算，schema 与实际参数校验一致；不再让过小预算在首次模型请求前抢先终止，且 fixture 不发送真实子 provider 请求。四个复杂场景补齐父侧 `list_dir`/`grep` 只读调查入口。目标故障未触发时，相关不变量记为 `incomplete`，独立清理和用量证据仍分别检查。

新增离线回归覆盖推理字段、完整子代理超时恢复和实际崩溃前持久化边界。全量回归 749 passed；suite 1.2 离线矩阵 50/50 故障触发、不变量通过，无基础设施错误、证据断链或未运行槽位。运行 ID `3daa5c6d-88e5-41c6-ab65-7f9f06c0f869`，原始结果保留于 `/private/tmp/mini-agent-reliability-v052-suite12/`。沙箱内的独立尝试因禁止绑定 loopback 端口而有 8 个注入错误，保留于 `/private/tmp/mini-agent-reliability-v052-suite12-sandbox/`，未逐槽替换。suite 1.2 已另行运行 live，结果见下节；suite 1.1 成绩仍单独保留、不沿用为新合同基线。修正没有放宽正式 Runtime 的授权或完成条件。

## Suite 1.3 修正

复查 suite 1.2 worker 可复现：`start_process(cwd=".")` 将相对目录按 worker 启动目录（项目根）解析，与 trial workspace 比较后在 handler 内抛出异常。现在相对目录按 trial workspace 解析；参数检查在 handler 准入前执行，越界、不存在或越界符号链接均拒绝，不启动进程、不预留修改 generation。省略 cwd、`.`、`./` 与工作区绝对路径通过同一条完整超时恢复回归。旧 trial 没有异常栈，因此此处是可复现的实现缺陷，不将旧槽位的具体异常位置追认为已观测事实。

另确认崩溃续跑通过正式 resume 重新发现上层项目 AGENTS.md，首次评测却使用评测专用指令，造成两阶段上下文不一致。评测 worker 现在统一生成两阶段受保护提示词，恢复仅追加简短恢复说明；正式 Runtime 的指令发现、授权、逐项处理 issue、重规划和独立验证规则不变。完整续跑回归覆盖派生会话、禁止重放、新计划与当前 generation 验证。

新增 `state_summary.tool_diagnostics`，只记录异常类型、代码相对位置、参数 hash 和可用 errno；不保存参数正文、异常自由文本或局部变量。新增 `phases.agent/pre_crash/recovery.request_diagnostics`，逐请求区分 system、State、历史、摘要、提醒和工具 schema 的估算，记录实际/估算用量来源、推理字段的子集估算、输出上限、前阶段累计用量及拒绝原因。缺失 provider usage 不记为实际零；估算不是精确 tokenizer，超预算量单独显示。总预算和完成条件未放宽，也不能据离线修正宣称模型恢复率已提高。

合同升为 1.3 并重算冻结材料与 suite 摘要。旧 1.0/1.1/1.2 artifact 原样保留；首次 live 前没有补跑或替换历史槽位。

离线矩阵验证：50/50 故障触发与不变量通过，无基础设施错误、证据断链或未运行槽位。最终运行 ID `504b0c57-db4d-4bc9-8436-a12cffe7380c`，原始结果 `/private/tmp/mini-agent-reliability-v052-suite13-final/`，源码指纹与当前工作树一致；前一次完整离线结果仍保留于 `/private/tmp/mini-agent-reliability-v052-suite13/`。沙箱内独立尝试因禁止绑定回环端口有 8 个注入错误，保留在 `/private/tmp/mini-agent-reliability-v052-suite13-sandbox/`，没有逐槽替换。历史 suite 1.1 与 1.2 报告重建通过，恢复成绩仍为 5/10 与 5/16。

定点回归 59 passed，预检、教程结构、README 与 diff 检查通过。前两次完整测试为 758 passed、1 failed：旧进程清理测试 `test_cleanup_cannot_succeed_when_wait_does_not_confirm_exit` 在 mock wait 后立即收尾，收到系统 PermissionError；沙箱外独立重跑同样失败，具体系统拒绝原因未确认。测试收尾现在先对真实直接子进程做 3 秒有界等待，再由原清理逻辑确认完整退出；原“不确认退出不能成功”的断言全部保留，生产清理代码未修改，单项回归通过。教程事实检查仅缺少用户手动创建的 v0.52 tag。

最终验收：全量 pytest **759 passed**，含进程清理测试的定点回归 **77 passed**；当前源码离线矩阵 **50/50** 通过。Suite 1.3 live 结果见下节。

## Suite 1.4：预算准入修正

Suite 1.3 第五批诊断发现：实际输入高于 `len(text)//3` 估算，进程 trial 累计 65,910、崩溃 trial 累计 65,461，超过 64,000；另两个崩溃 trial 的实际累计为 58,030 与 61,900，因剩余预算无法容纳下一请求停止，不能笼统写成全部已超过上限。验证修复的两个失败 trial 则在首次 shell 调用被拒绝后终止，并非预算耗尽。

当前合同冻结 `request_budget_policy`：输入预留为原估算乘以校准系数、向上取整后加 256；系数初始为 2，只依据本 trial 的纯 provider 用量上调为已观测实际/估算比例的 1.1 倍，不因估算用量或低比例下调。预留至少 256 输出 tokens（配置输出上限更小时沿用该上限），其余输出上限按剩余预算计算。首次运行与崩溃恢复均使用同一规则，恢复继承前阶段累计用量和校准样本；没有扩大总预算。

准入和输出上限在请求前计算一次，不在 Runtime 预记原输入后重复扣算。新增数字字段 `reserved_input_tokens_calibrated`、`input_multiplier`、`planned_output_limit` 和 `budget_refusal_reason`，分别记录校准预留、系数、可用输出上限及“已超额/已耗尽/不足以容纳下一请求”的原因。已发送请求仍保留实际用量，不能截断计数来伪装预算合规。若 provider 返回后确认已超额，当前轮每个工具回灌预算错误且不执行 handler；纯文本也不能宣称完成。

该规则是保守预估，不能保证未知 provider 的任意请求永不超额；首次响应或异常偏差仍可能超过预留，此时保留超额事实并停止。它也不会自动解决多轮调查、重复上下文与验证收尾效率。当前只完成预算修复，不以离线结果推断恢复率改善，旧 live artifact 未改动。

验收：定点回归 **67 passed**，全量 pytest **767 passed**；预检与历史报告重建通过。教程结构、README 和 diff 检查通过；事实检查仍缺少用户手动创建的 v0.52 tag。沙箱内独立离线尝试因回环端口限制有 8 项注入错误，保留于 `/private/tmp/mini-agent-reliability-v052-suite14-sandbox/`。最终离线矩阵 **50/50** 故障触发与不变量通过，无基础设施错误或未运行槽位；运行 ID `cd7f12b3-7ffc-408b-8d27-e0e54b7e6b41`，证据 `/private/tmp/mini-agent-reliability-v052-suite14-final/`，源码指纹与当前工作树一致。前次完整矩阵保留于 `/private/tmp/mini-agent-reliability-v052-suite14/`，未逐槽替换。

## Live 基线（suite 1.4）

独立批次[`live-20260929-06/`](live-20260929-06/)（run ID `5039acb5-c599-4b85-bfd3-73c327231107`）按冻结顺序完成 21/21 槽位；21 次故障全部触发，19 项不变量通过、2 项失败，grader 通过 15 项、失败 3 项。权限拒绝的 3 个边界专用 trial 不运行任务 grader，也不进入恢复分母；其余 18 个 trial 恢复成功 1 次（5.6%）。无基础设施错误、注入错误、未运行槽位、不可读 trial、清理失败或人工复核项；suite、代码 revision、runtime fingerprint 和 model binding 均一致。七个 live 场景均有 3 个已触发且可评分样本，因此达到冻结的 live 样本覆盖门槛；较低恢复率仍是本批的真实结果。

| 场景 | 故障触发 | 不变量结果 | Grader 通过 | 恢复成功 |
|---|---:|---|---:|---:|
| `tool-handler-exception` | 3/3 | 3 通过 | 3/3 | 0/3 |
| `permission-denied` | 3/3 | 3 通过 | 不适用（边界专用） | 不适用 |
| `verification-repair` | 3/3 | 1 通过、2 失败 | 1/3 | 1/3 |
| `process-wait-timeout` | 3/3 | 3 通过 | 2/3 | 0/3 |
| `crash-after-handler` | 3/3 | 3 通过 | 3/3 | 0/3 |
| `mcp-disconnect` | 3/3 | 3 通过 | 3/3 | 0/3 |
| `subagent-timeout` | 3/3 | 3 通过 | 3/3 | 0/3 |

预算诊断共记录 144 个请求决策：128 个请求获得 provider 响应，16 个在发送前因 `insufficient_request_budget` 被拒，拒绝记录均无 provider 用量、`planned_output_limit=0`，随后 Agent 以 `token_limit` 停止，没有继续工具回合。128 个已响应请求的实际输入用量都高于原始估算，但均未超过校准后的输入预留；实际输出均未超过分配上限，记录的预算超额为 0。三个 `crash-after-handler` 恢复阶段分别继承 22,292、22,454、22,112 个前阶段累计 tokens，并继续使用已校准预留。

拒绝发生时部分 trial 仍有未用预算，但校准后的下一请求输入预留已超过可用余额，系统没有发出模型请求。这保护了预算边界，也使本批 16 个 trial 因请求准入失败而未能完成任务。Live 没有观测到 provider 返回超额响应，因此“响应后阻止工具与完成”的分支未在本批真实响应中触发；该行为由修正报告中的定点回归和离线验收覆盖。输入估算仍不是精确 tokenizer，不能据此保证任意 provider 请求绝不超额；重复上下文与执行顺序问题仍待修复。

完整报告为[`report.json`](live-20260929-06/report.json)，槽位账本为[`suite-run.json`](live-20260929-06/suite-run.json)，运行前的校验输出为[`review-plan.json`](live-20260929-06/review-plan.json)，冻结清单和逐 trial 证据分别保存在 `frozen/` 与 `trials/`。本批不自动重试、补跑或替换样本。

## Live 基线（历史 suite 1.3）

首次独立批次[`live-20260929-05/`](live-20260929-05/)按冻结顺序完成 21/21 槽位。20 次故障实际触发，1 次未触发；没有注入错误、基础设施错误、不可读 trial、未运行槽位、清理失败或人工复核项。已触发故障中 18 项不变量通过、2 项失败；grader 通过 14 项。权限拒绝是边界专用场景，3 项均通过不变量检查，不进入恢复分母。其余场景恢复成功 8/17（47.1%）。Suite 摘要、代码 revision、runtime fingerprint 和 model binding 均一致。

| 场景 | 故障触发 | 不变量结果 | Grader 通过 | 恢复成功 |
|---|---:|---|---:|---:|
| `tool-handler-exception` | 3/3 | 3 通过 | 3/3 | 3/3 |
| `permission-denied` | 3/3 | 3 通过 | 不适用（边界专用） | 不适用 |
| `verification-repair` | 3/3 | 1 通过、2 失败 | 1/3 | 1/3 |
| `process-wait-timeout` | 2/3 | 2 通过、1 不完整 | 2/3 | 1/2 |
| `crash-after-handler` | 3/3 | 3 通过 | 3/3 | 0/3 |
| `mcp-disconnect` | 3/3 | 3 通过 | 2/3 | 1/3 |
| `subagent-timeout` | 3/3 | 3 通过 | 3/3 | 2/3 |

suite 1.3 未再出现 provider HTTP 400，21 个槽位的基础设施错误均为 0。新增逐请求诊断显示，三个 `crash-after-handler` trial 都在正确崩溃边界恢复，副作用没有重放且 grader 检查通过；但恢复阶段均因共享父 token 预算达到上限而停止，未完成规定恢复流程，故恢复成功为 0/3。`subagent-timeout` 三次都注入成功且结果结算不变量通过，其中两次完成独立验证并通过 grader。`process-wait-timeout` 由 suite 1.2 的 1/3 提升到 2/3 命中；第三个 trial 在调用进程工具前耗尽预算。两次已触发 trial 中一次完成任务，另一次耗尽预算。验证修复有两次未完成修复与验证。

因此 suite 1.3 live 基线**仍未完成**：`process-wait-timeout` 只有 2 个已触发且可评分 trial，未达到每场景 3 个的冻结门槛。按规则不补跑或替换该槽。原始结果与可重建报告见[`report.json`](live-20260929-05/report.json)，槽位账本为[`suite-run.json`](live-20260929-05/suite-run.json)，逐 trial 证据保存在 `trials/`。该批是首次 suite 1.3 成绩，与 suite 1.2 的 5/16 和更早批次分别保留。

## Live 基线（历史 suite 1.2）

修复后独立批次[`live-20260929-04/`](live-20260929-04/)按冻结顺序完成 21/21 槽位，21 个 trial 均有终态记录，基础设施错误为 0，故障注入错误为 0；故障触发 19 次、未触发 2 次，不变量通过 17 项、失败 2 项、不完整 2 项，grader 通过 12 项。恢复成功 5/16（31.25%），权限拒绝为边界专用场景，不进入恢复分母。Suite 摘要、代码 revision、runtime fingerprint 和 model binding 均一致；无不可读 trial、cleanup incomplete 或人工复核项。

| 场景 | 故障触发 | 不变量结果 | Grader 通过 | 恢复成功 |
|---|---:|---|---:|---:|
| `tool-handler-exception` | 3/3 | 3 通过 | 2/3 | 2/3 |
| `permission-denied` | 3/3 | 3 通过 | 不适用（边界专用） | 不适用 |
| `verification-repair` | 3/3 | 1 通过、2 失败 | 3/3 | 1/3 |
| `process-wait-timeout` | 1/3 | 1 通过、2 不完整 | 0/3 | 0/1 |
| `crash-after-handler` | 3/3 | 3 通过 | 3/3 | 0/3 |
| `mcp-disconnect` | 3/3 | 3 通过 | 3/3 | 1/3 |
| `subagent-timeout` | 3/3 | 3 通过 | 1/3 | 1/3 |

本批没有再出现 provider HTTP 400；三次 MCP 断连都满足不复用连接、不自动重试。三次 crash-after-handler 都在正确边界崩溃，副作用只发生一次、源 session 保持只读，恢复会话也完成 issue 处理、重新规划和授权；但三次都在当前 generation 验证前耗尽父侧总 token 预算，因此恢复成功为 0/3。子代理超时 3/3 实际注入，父侧均领取结果并只结算一次；其中两次因 token budget 耗尽未完成恢复，只有 1/3 正常完成并通过 grader。被注入替代的子 LLM 请求均标记为 `injected`，不计作真实子模型调用。

进程超时仅 1/3 命中：一次 `start_process` handler exception 后以副作用范围未知安全阻断，一次未到达 `wait_process`，一次正确观测到等待超时且通过边界不变量，但未完成后续任务；未触发槽位相关不变量均记为 `incomplete`。因此 suite 1.2 live 基线**仍未完成**，因为 `process-wait-timeout` 尚无 3 个已触发且可评分样本。报告和全部逐 trial 证据见[`report.json`](live-20260929-04/report.json)及 `trials/`；本批失败没有补跑、替换或并入 suite 1.1。

## 历史 Live 基线（suite 1.1）

2026-09-29 经预检后顺序运行三批，每批都是 21 个固定槽位、独立输出目录；失败请求没有自动重试、补跑或替换。完整结果均从冻结 suite、槽位账本和逐 trial artifact 用专用报告器重建，运行前后的 suite 摘要、代码 revision 与 runtime fingerprint 一致。

- [`live-20260929-01/`](live-20260929-01/)：18 个评分 trial、3 个基础设施错误；13 次故障触发、12 个不变量通过、9 个 grader 通过，恢复成功 5/10。`subagent-timeout` 的 3 个槽位被异常诊断器自身的 `binding.profile.model` AttributeError 遮蔽；修正为 `model_id` 后开独立批次。
- [`live-20260929-02/`](live-20260929-02/)：18 个评分 trial、3 个基础设施错误；14 次故障触发、13 个不变量通过、10 个 grader 通过，恢复成功 7/11。子代理工具 JSON Schema 将 Python 正则 `\Z` 暴露给 provider，服务端拒绝该 schema；保留本地严格 UUID 校验，provider-facing pattern 改为可移植的锚定表达式后再开独立批次。
- [`live-20260929-03/`](live-20260929-03/)：21 个槽位均有终态记录，其中 20 个评分 trial、1 个基础设施错误；13 次故障触发、12 个不变量通过、9 个 grader 通过，恢复成功 5/10（50%）。权限拒绝是边界专用场景，3 次拒绝与安全终态全部通过，不进入任务恢复分母。

第三批逐场景结果：

| 场景 | 故障触发 | 不变量通过 | Grader 通过 | 恢复成功 |
|---|---:|---:|---:|---:|
| `tool-handler-exception` | 3/3 | 3/3 | 3/3 | 1/3 |
| `permission-denied` | 3/3 | 3/3 | 不适用（边界专用） | 不适用 |
| `verification-repair` | 3/3 | 3/3 | 3/3 | 3/3 |
| `process-wait-timeout` | 1/3 | 1/3 | 2/3 | 1/1 |
| `crash-after-handler` | 0/3 | 0 通过、3 不完整 | 0/3 | 无可计分样本 |
| `mcp-disconnect` | 2/3 | 2/2 可评分样本 | 1/2 可评分样本 | 0/2 |
| `subagent-timeout` | 1/3 | 0/3 | 0/3 | 0/1 |

第三批唯一基础设施错误是 `mcp-disconnect` 第 2 槽的 provider HTTP 400。脱敏诊断表明服务端在 thinking mode 下要求回传 `reasoning_content`；当时通用 OpenAI Chat 适配器没有保留该 provider 扩展字段。它不是凭据错误；现已加入离线验证过的往返保留，新 live 兼容性尚未验证。该槽保留为基础设施错误，没有重试。`subagent-timeout` 不再出现 schema 400，但只有 1/3 次触发注入；另外两次以 Agent failure/token limit 结束。`crash-after-handler` 三次都未触发，不能据此评价恢复行为。

Suite 1.1 live 基线**未完成**：至少 `process-wait-timeout`、`crash-after-handler`、`mcp-disconnect`、`subagent-timeout` 未达到每场景 3 个故障已触发且可评分样本。第三批报告为[`report.json`](live-20260929-03/report.json)，逐 trial 诊断与证据在各自 `trials/` 子目录；批次 01、02 的报告分别保存在对应目录。不得把三批合并成一次固定配置的基线，也不得把其恢复率解释成稳定模型能力指标。

## 离线矩阵

此前候选批次 `offline-20260928-final2`（suite run `099db82e-23ec-4d0f-9c7e-ffac9f585bcb`）对修正前 suite 运行 50 个参数槽位，50 个故障均触发、50/50 不变量通过。中间修正候选 `b0db6164…0125a3c` 的离线结果保存在 `offline-20260928-corrected/`，同样 50/50 通过。当前审阅候选加入标准 `recover`/`request_replan` 控制工具、公开精确触发动作和 trial 内进程 fixture；`offline-20260928-runtime-controls/` 已完成 50/50 槽位，故障均触发、不变量均通过。

发现并修复 live 请求 artifact 将本地 provider 脱敏值写入 `request.json` 的问题后，新的独立运行 [`offline-20260928-security-redaction/`](offline-20260928-security-redaction/) 再次完成 50/50 槽位：50 个故障触发、50/50 不变量通过，无基础设施错误、证据断链或未运行槽位。运行前一次受限沙箱尝试 [`offline-20260928-sandbox-loopback-limited/`](offline-20260928-sandbox-loopback-limited/) 保留了 8 个 loopback HTTP fixture 启动失败槽位；该次不计入通过结果，也没有逐槽补跑。两份结果使用独立目录，供复核环境差异。

先前候选批次 `offline-20260928` 和 `offline-20260928-final` 均保留作审计记录，不计入当前 suite 验收。首批使用任务 grader 检查加入前的候选合同；中间批次在冻结副本清理运行缓存规则加入前运行。

这批 fixture 直接检查边界组件，不调用 Agent；因此全部 task grader 为 `null`，Agent 终态为空，恢复成功率也为 `null`。这是离线边界验收，不是模型任务结果。真实 MCP loopback HTTP 使用授权的本机测试执行环境。

对需要完整 Agent 续行的 fixture 故障，`recovery_status` 保留 `incomplete`，不推断恢复成功。例如崩溃后继续规划、MCP 断连后的替代任务都由对应 live trial 评估；block 反馈变体则在阻止继续后记录为已收束。Fixture 不把组件边界通过冒充为完整任务恢复。

## 历史 Live 基线（suite 1.0）

修正候选 live 计划为七个代表性场景各 3 次，共 21 个固定顺序槽位。当前任务、可见工具、精确权限、反馈、预算和 grader 标准见[`review-plan.json`](review-plan.json)。模型来源摘要见[`live-binding-preview.json`](live-binding-preview.json)，运行时会重新解析并冻结绑定。

首批结果保存在[`live-20260928-01/`](live-20260928-01/)，21 个槽位均已执行，但多个故障未触发、grader 未通过。中间合同摘要 `b0db6164…0125a3c` 的 live 批次保存在[`live-20260928-02/`](live-20260928-02/)：21 个计划槽位中 18 个实际运行，`subagent-timeout` 的 3 个父模型请求返回 HTTP 400，作为基础设施错误保留；其余 18 个 trial 中故障触发 6 次、不变量通过 3 次、grader 通过 0 次，恢复分母为 6、成功为 0。该批不是完整基线。

历史 suite 1.0 摘要的 live 批次保存在[`live-20260928-03/`](live-20260928-03/)：21 个槽位均有终态记录，其中 8 个 worker 产生 trial，13 个为基础设施错误；没有自动重试或补跑。8 个 trial 中 5 个故障触发、3 个不变量通过、grader 通过 0 次；恢复分母 5、成功 0。逐场景为：`tool-handler-exception` 3/3 触发且不变量通过；`verification-repair` 2 个 trial 触发、两项不变量失败，另 1 个 provider protocol error；`permission-denied` 2 个 trial 均未触发且不变量失败，另 1 个 worker `AttributeError`；`process-wait-timeout` 1 个 trial 未触发且不变量失败，另 2 个 provider connection error；`crash-after-handler`、`mcp-disconnect`、`subagent-timeout` 的三个槽位各自均因 provider connection error 未进入评分。该批不是完整基线。

脱敏修复后的 live 批次[`live-20260928-04/`](live-20260928-04/)保留了 21 个终态槽位：17 个 trial、4 个基础设施错误；9 次故障触发、7 个不变量通过、1 个 grader 通过，恢复成功为 0/9。逐场景结果为：`tool-handler-exception` 3/3 触发且不变量通过；`mcp-disconnect` 3/3 触发且不变量通过；`verification-repair` 3/3 触发，1 个不变量及 grader 通过、2 个失败；`permission-denied` 2 个 trial 均未触发且不变量失败，另 1 个 `AttributeError`；`process-wait-timeout` 和 `crash-after-handler` 各 3 个 trial 均未触发；`subagent-timeout` 三槽均因 provider HTTP 400 记为基础设施错误。报告来源一致、没有不可读证据；所有 21 个 `request.json` 均不再包含脱敏值，复扫没有发现敏感值。基线仍不完整，因为四个 live 场景没有达到各自至少 3 个故障已触发且可评分样本的标准。

批次 04 的逐 trial 证据核对记录在[`report.json`](live-20260928-04/report.json)及各 trial 的 `trial.json` 中：两个 `permission-denied` trial 只记录到 `run_shell` 被拒绝，没有记录到目标 `write_file(src/locked.py)`；第三个槽位被旧 runner 标记为启动前 `AttributeError`，但现有 artifact 未保存异常位置，无法确认实际失败阶段。`process-wait-timeout` 中有一次 `wait_process` 在 handler 执行前被 `repair_phase_gate` 拒绝，其余槽位也没有成功观测到指定等待超时。三个 `crash-after-handler` trial 都以 `token_limit` 结束，尝试记录没有到达 `write_file`/`edit_file`，所以崩溃注入未发生。三个 `subagent-timeout` trial 都在父侧首个工具调用前收到 `provider_http_400`（`tool_calls=0`），子模型超时注入未发生。这些是证据能够支持的运行现象，不构成对运行时缺陷或服务端拒绝原因的根因结论。

请求 artifact 的脱敏问题影响前三个历史 live 目录。三个目录的请求副本均已清除敏感字段，原始文件 SHA-256 与不含敏感值的复现说明分别保存在各批次的[`artifact-security-findings.json`](live-20260928-03/artifact-security-findings.json)中。Runner 已改为不把这些值持久化，并在发布前清理生成的文本 artifact。修复后的离线矩阵通过。没有自动重试、补跑或替换失败槽位。Suite 1.1 编码基线仍单独标为未完成，v0.51 的历史争议成绩继续保留。

运行命令：

```bash
PYTHONPATH=src python -m mini_agent.evaluation validate-reliability tests/fixtures/evaluation/reliability/suite.json
PYTHONPATH=src python -m mini_agent.evaluation run-reliability tests/fixtures/evaluation/reliability/suite.json --live --repeats 3 --output docs/evaluation/baselines/v0.52/live-<run-id>
PYTHONPATH=src python -m mini_agent.evaluation report-reliability docs/evaluation/baselines/v0.52/live-<run-id>
```

Live 必须在审阅全部任务后显式启动，并从本地配置解析模型来源。若配置不可用或某次请求失败，结果保留为基础设施错误或失败 trial，不会自动重试。Suite 1.1 的 v0.51 编码基线仍单独标记未完成；suite 1.0 的历史争议成绩保留在原目录。

## suite 1.5 后的定点修复

已修正当前验证和计划推进请求无法使用预留额度的问题，补齐拒绝时的上下文下限与预算分配诊断，以及工具参数 JSON 错误的停止原因、长度、位置和用量。完整输出失败与来源未确认的流式错误按新证据分别分类，历史成绩不追溯修改。长历史下 19k/27k 的确定响应回归完成验证及收尾；11k 不足时保持拒绝。详见[修改和限制](stage-budget-repair.md)与[验证记录](stage-budget-repair-validation.json)。

本轮未启动新的 paid live，未创建 suite 1.6。重新冻结候选前暂停整批运行；当前实现变化不能沿用 suite 1.5 的旧 Runtime 审阅指纹。v0.52 live 基线仍未完成。

## 2026-09-30 provider 恢复后的预检

按恢复顺序重新核对了 `live-20260929-08`、`live-20260929-09` 的原始 trial。08 的 21 个请求全部是 HTTP 503；09 有 20 个连接类错误和一个 provider 响应。该响应来自 `tool-handler-exception`，故障没有触发，Runtime 因 `provider_usage_exceeded_reservation` 停止。单次输入为 13,748，原始估算 2,918，校准预留 6,092，超过预留 7,656；累计使用 13,812，仍有 50,188 的 64k 任务预算。超额保护有效，但这次没有可评分恢复样本。

原始 trial 没有保存请求正文。离线重建同一场景的请求形状，没有发送网络请求：3 条消息、11 个工具 schema，序列化正文约 12,807 个字符 / 19,315 字节。旧报告中的 2,252 消息估算来自当时重建；冻结 trial 自身记录为 2,273 消息、645 schema、合计 2,918。历史 Git revision 为 `cd7ec0ee`，其中 `count_tokens` 对字符串使用 `len(text) // 3`，递归累加 list 项和 dict value，但不计 dict key 或 JSON 标点。这确认估算算法存在系统性漏项；单个 provider usage 不能区分各漏项、provider 隐藏前缀及其 tokenizer 的贡献。序列化字节数不等于 token 数。没有保留未经验证的估算器改动，因为试验性序列化估算破坏了 19k/27k 晚期恢复预算回归。

用户要求恢复 #09 精确 binding 后，本地再次只读核对：当前指纹 `fbfec446…`，目标 `2c7e1654…`，仍不匹配；当前 Runtime 指纹 `8ef329a9…` 也不同于批次记录的 `a32092e1…`。Git revision 虽与 trial metadata 相同，工作树的 Runtime 源码指纹不同，因此不能声称当前离线重建是历史请求的精确复现。未发出探针或基线请求。美元上限与费率/生效中的 provider 硬限额仍未提供，付费请求条件不成立。完整摘要与指纹记录见[预检调查记录](provider-restoration-investigation-20260930.json)。

当前本地 `legacy-default` 指纹与 09 的冻结指纹不一致，旧批次也没有价格快照，本地配置没有费率字段。因此本轮**没有发送探针请求，也没有启动 21 槽位 live**；无法诚实地声称已使用同一 binding 或满足美元费用上限。没有槽位被消耗、替换或重跑，历史结果未修改。逐项证据与限制见[预检调查记录](provider-restoration-investigation-20260930.json)。继续前需恢复 09 的精确 binding，或明确批准当前指纹作为新探针组，并提供可执行的美元上限及费率依据（或 provider 侧硬账单限额）。

## #09 输入估算的离线修正（2026-09-30）

#09 唯一 provider 响应的单次输入超过预留 7,656 tokens，累计预算仍有 50,188；原因不能由未保存的请求正文精确分解。当前候选已改用完整 OpenAI Chat 请求结构的 UTF-8 大小代理估算，并在低额度时只裁剪可省略的历史回合。受保护用户纠正与工具调用/结果协议有离线回归。详见[修复说明](estimator-offline-repair.md)和[离线验收](estimator-offline-validation.json)。没有新付费探针或 live，#08/#09 成绩保持原样；当前候选需要重新冻结审阅。

## 独立 provider 探针（2026-09-30）

用户批准将当前 `fbfec446…` binding 作为独立探针组，探针总上限改为 1 USD。当前本地模型 ID 与小米第一方 MiMo-V2.6-Flash 匹配；[官方海外实时 API 价目表](https://mimo.mi.com/docs/pricing)按每百万 token 为缓存输入 0.0028、未缓存输入 0.14、输出 0.28 USD。发送前以三次请求各最多 100 万未缓存输入和 1,024 输出计算保守包络，合计 0.42086016 USD，小于批准上限；实际 archive 使用逐次未缓存价保守计费，未知请求按整次包络计。

[独立探针](provider-probe-20260930-01/probe.json)只发出两次：普通请求成功，provider 报告输入 12、输出 15 token；工具调用请求返回 `ProviderProtocolError`，当次错误位置在这一版驱动中未记录。没有可配对的 tool result，因此没有第三次请求、没有自动重试或 live 槽位消耗。保守费用上界为 0.14029260 USD。证据摘要和执行驱动 SHA-256 见[探针复核](provider-probe-20260930-01/review.json)。这不能验证历史 #09 的 13,748-token 偏差，也不能评价恢复率。候选仍须排查协议错误并重新冻结后才能考虑完整 live。

随后针对同一独立 binding 做了三组各自归档、无自动重试的定点诊断；它们不是 live 槽位的补跑。[探针 02](provider-probe-20260930-02/probe.json)复现错误，脱敏栈定位于流式工具调用的名称/参数校验。将探针输出额度从 1,024 对齐到本地 profile 的 8,192 后，[探针 03](provider-probe-20260930-03/probe.json)仍在同一位置失败，因此单靠提高输出额度不能解决。两次失败的保守费用上界分别为 0.14029260 和 0.14229852 USD；各自的原始文件校验和、执行驱动校验和及限制见 [02 复核](provider-probe-20260930-02/review.json)、[03 复核](provider-probe-20260930-03/review.json)。

代码检查发现 OpenAI Chat 流式拼接会让后续空工具名或 `null` 参数覆盖已收到的字段；现已忽略这种空片段，仍拒绝真正缺失或非字符串的最终字段，并增加不含工具正文的结构诊断。本地回归覆盖两种边界。[修正后的探针 04](provider-probe-20260930-04/probe.json)完成普通回复、工具调用和配对的工具结果回复，provider 报告三次输入为 12、104、72 token，输出为 15、41、80 token；[复核](provider-probe-20260930-04/review.json)保存原始文件与执行驱动校验和。四组探针总共发出 9 次请求，按未知请求整次包络计的**累计保守费用上界为 0.42294812 USD**，低于用户批准的 1 USD；没有自动重试、live 槽位消耗或 21 槽位运行。

探针 04 支持修复后的**小请求工具协议链路可用**。[四组汇总](provider-probe-followup-20260930.json)逐组校验原始文件摘要并记录累计费用。历史失败没有保留响应正文，无法证明空片段覆盖就是其唯一原因；这几次小请求也没有复现 #09 的大输入预留偏差或证明恢复能力。Suite 1.5 的历史成绩和 v0.52 live 基线状态不变；不能仅凭探针通过启动或宣称完成整批 live 验收。

继续用当前独立 binding 发送了一次**合成大输入定点请求**，不执行工具，不占 live 槽位。它使用 3 条无敏感消息与 11 个惰性工具 schema，序列化为 19,320 UTF-8 字节，接近 #09 事后离线形状重建的 19,315 字节；正文不同，也不是历史原请求。当前估算为 7,084 token，冷启动输入预留 14,424；[provider 实报](provider-large-input-20260930-01/probe.json)输入 5,124、输出 18 token，未超过预留。按未缓存价计本笔费用上界 0.00072240 USD，所有探针累计保守上界为 **0.42367052 USD / 1 USD**。原始文件、执行脚本摘要和限制见[复核](provider-large-input-20260930-01/review.json)。这个样本支持当前 binding 对该合成形状的准入充分，但不能解释或推翻旧 #09 的 13,748 token，也不能保证任意请求绝不超额；v0.52 live 基线仍未完成。

最后用 suite 工作请求的 **1,024 输出上限**重验修正后的工具协议。[探针 05](provider-probe-20260930-05/probe.json)完成普通回复、工具调用、配对工具结果回复，三次请求均有 provider 用量；[复核](provider-probe-20260930-05/review.json)保留执行驱动摘要。这排除了“只有将探针上限提高至 8,192 才能完成小请求工具链路”的解释。所有六组独立探针共 13 次请求，累计保守费用上界 **0.42375368 USD / 1 USD**，没有自动重试或 live 槽位消耗；原始摘要和离线矩阵状态见[综合验收](provider-validation-20260930.json)。当前可确认的是工具协议与一个合成大输入形状的定点验证；历史 #09 精确成因和真实 Agent 恢复表现仍待 live 证据。

## 当前绑定的新 live 批次（2026-09-30）

用户显式启动 suite 1.5 的独立批次 [`live-20260930-01/`](live-20260930-01/)。运行前重新冻结了[审阅清单](live-20260930-01/review-plan.json)：suite 摘要 `833eefa4…`、Runtime 指纹 `90f115c2…`、当前 binding 指纹 `fbfec446…`，七个场景各 3 次。21/21 槽位均有终态且报告纳入 21 个 trial；没有基础设施、注入、清理、来源不一致、不可读或未运行问题，也没有自动重试或替换样本。[正式报告](live-20260930-01/report.json)可从冻结清单、账本和原始 trial 逐字节重建；[独立复核](live-20260930-01/report-validation.json)保存摘要与来源检查。

故障触发 17 次；不变量 15 项通过、2 项失败、4 项因故障未触发而不完整；任务 grader 11 项通过。严格恢复 **6/14（42.9%）**。权限拒绝 3 个边界专用样本安全检查均通过，不进入恢复分母；另 4 个未触发故障也不进入。逐场景如下：

| 场景 | 故障触发 | 不变量通过 | 严格恢复 | 观察 |
|---|---:|---:|---:|---|
| 工具 handler 异常 | 3/3 | 3/3 | 2/3 | 1 次以 token limit 停止 |
| 验证修复 | 3/3 | 1/3 | 1/3 | 2 次未完成当前代验证和隐藏 grader |
| 权限拒绝 | 3/3 | 3/3 | 不适用 | 3 次安全边界通过 |
| 进程等待超时 | 1/3 | 1/3；另 2 次不完整 | 1/1 | 另 2 次在故障前停止 |
| 崩溃后恢复 | 1/3 | 1/3；另 2 次不完整 | 0/1 | 1 次在触发前发生本地 `SessionValidationError`，另 1 次在故障前用量不足 |
| MCP 断连 | 3/3 | 3/3 | 2/3 | 1 次因 token limit 停止 |
| 子代理超时 | 3/3 | 3/3 | 0/3 | 3 次均因 token limit 停止 |

136 个请求决策中 126 个得到带 provider 用量的响应，10 个因受保护上下文无法放入剩余额度而在发送前拒绝；已响应请求的输入均低于原始代理估算及校准预留，记录的预算超额为 0。本批没有 provider HTTP 400 或协议错误记录。未触发的进程和崩溃槽位按证据不完整保留，不能补算恢复成功或失败；其中 `SessionValidationError` 的具体触发条件尚未由现有脱敏证据确认。由于进程等待超时和崩溃后恢复各只有 1 个已触发可评分样本，**v0.52 live 基线仍未达到每场景 3 个样本的门槛**。本批是当前 binding/Runtime 的独立结果，不与 #09 或其他候选合并。
