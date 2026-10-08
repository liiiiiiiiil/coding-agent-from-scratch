# v0.52 故障注入与恢复评测

`reliability-boundaries@1.6` 在固定任务中注入受控故障，分别记录运行时边界和 Agent 是否完成恢复后的任务。边界测试回答“危险行为有没有被拦住”；独立 grader 回答“Agent 最终留下的工作是否正确”。离线 fixture 只测试边界驱动器和运行时组件，不代表模型完成任务。

2026-09-30 当前 binding 的新 [21 槽位 live 批次](baselines/v0.52/live-20260930-01/report.json)已完成：17 次故障触发，不变量 15 通过、2 失败、4 不完整，严格恢复 6/14；没有基础设施错误。进程等待超时和崩溃后恢复各仅 1/3 触发，故 v0.52 live 基线仍未达到每场景三次可评分触发的门槛。逐场景、预算诊断和原始证据见[基线说明](baselines/v0.52/README.md)。

当前可执行候选为 suite 1.6：验证修复场景新增获准的 `list_dir`、`grep`，仍限定在 trial 工作区。生产运行时拒绝非法恢复编号时不再建立无效因果引用；收尾使用应用生成的专用提示，项目指令和用户纠正保持完整。工作请求仍保留完整工具规则，验证与正常完成要求不变。此候选尚无 live 成绩，详情见[修复记录](baselines/v0.52/live-failure-repair-20261008.md)。

## 冻结场景

场景顺序、任务、可见工具、逐项权限、预算、故障驱动、恢复步骤、反馈策略和 grader ID 都在 `tests/fixtures/evaluation/reliability/suite.json` 与场景材料中冻结。suite 和材料有 SHA-256 摘要；加载会拒绝未知字段、摘要漂移、路径越界、符号链接以及任意 Python grader 代码。

| 场景 | 注入与验收重点 | 离线参数变体 | Live |
|---|---|---:|---|
| `tool-handler-exception` | 第一次文件 handler 调用前抛异常；错误结果回灌，后续合法调用可继续。 | 1 | 是 |
| `permission-denied` | 明确拒绝 `src/locked.py` 写入；handler 不执行、文件不变，父任务安全终止。此场景只评分边界。 | 1 | 是 |
| `verification-repair` | 初始公开测试真实失败；修复后须有当前 generation 的验证证据，隐藏 grader 通过。 | 1 | 是 |
| `process-wait-timeout` | fixture 进程超出短等待时间；超时仍显示运行，Agent 交接、同步并清理。 | 1 | 是 |
| `crash-before-admission` | 已保存 pending 工具回合、尚未准入；恢复补入未执行结果，不调用 handler。 | 1 | 否 |
| `crash-after-admission` | `handler_admitted` 已提交、handler 未运行；新 session 记录不确定事实，不重放。 | 1 | 否 |
| `crash-after-handler` | handler 已产生副作用、结果未提交；新进程恢复，副作用只发生一次并逐项处理 issue。 | 1 | 是 |
| `durable-commit-failure` | 分别令准入、逐 call 结果、整轮提交失败；不运行 handler、后续 call、LLM 或后台 worker。 | 3 个提交边界 | 否 |
| `recovery-user-decisions` | 先尝试未调查 issue 的 `continue`，再合法调查；另一个变体选择 `block`。 | 2 个反馈策略 | 否 |
| `mcp-disconnect` | 首次 `tools/call` 断连；连接失效、不重试，可使用公开本地材料。 | stdio、显式允许的 loopback HTTP | 是，仅 stdio |
| `mcp-remote-error` | Server 返回整数码 JSON-RPC error；检查类别、脱敏和无误分类。 | stdio、显式允许的 loopback HTTP | 否 |
| `mcp-is-error` | Server 返回有效 result 且 `isError=true`；不混同 JSON-RPC error。 | stdio、显式允许的 loopback HTTP | 否 |
| `mcp-call-timeout` | Server 在 `tools/call` 不响应；检查有界超时、连接失效和关闭结果。 | stdio、显式允许的 loopback HTTP | 否 |
| `subagent-timeout` | 子模型调用在 provider I/O 前被明确 timeout 注入；父侧领取并结算一次，再自行调查和验证。 | 1 | 是 |
| `subagent-cancel` | 子 worker 在受控屏障等待时被请求取消；有界收束后仍可领取最终结果。 | 1 | 否 |
| `subagent-unclaimed` | 子任务已结束但结果未领取；阻止完成和安全点，重复领取结果稳定且只结算一次。 | 1 | 否 |
| `subagent-interrupted` | 启动确认和整轮边界已提交后停止父 worker；恢复记为 `interrupted`，不恢复旧 worker。 | 1 | 否 |
| `followup-incompatible` | 领取成功结果后替换冻结 Skill；新子 LLM 请求前拒绝续接，其他会话不受影响。 | 1 | 否 |

完整离线矩阵按冻结顺序有 50 个槽位，每个参数变体运行两次。Live 子集为前表七个“是”场景，每场景三次、共 21 个槽位；`mcp-disconnect` 的 live 参数固定为 stdio。失败、未触发、基础设施错误或中断槽位不会自动补跑或替换。

## 评分与分母

每个 `trial.json` 使用独立格式 `mini_agent.reliability`、schema 1，不复用编码题的 TrialResult。它分开记录：

- `fault_status`：`triggered`、`not_triggered` 或 `injection_error`。只有记录到实际命中点才算触发。
- `invariant_status`：`passed`、`failed` 或 `incomplete`，并逐条列出不变量证据状态。
- `recovery_status`：`succeeded`、`failed`、`not_applicable`、`not_triggered` 或 `incomplete`。
- `grader_passed`：Agent 停止后由独立 grader 检查工作区；离线组件探针不运行 Agent，因此此字段为 `null`。
- Agent 停止原因、State 终态、清理结果、来源一致性、阶段用量和证据引用。

`recovery_success_rate` 只对 live 恢复 trial 计算。分母要求故障确已触发、来源一致、边界证据与恢复流程可判定、grader 已运行且没有评测基础设施错误。六个恢复场景中的模型超时、权限拒绝、预算耗尽和恢复失败仍留在分母。`permission-denied` 专门验收安全终止，`grader_passed=null`、`recovery_status=not_applicable`，不进入任务或恢复评分分母。分子还要求不变量通过、恢复步骤完成、Agent 正常以 `done` 收束、grader 通过、清理完成。Fixture 只报告边界结果；任务成功和恢复成功率保持 `null`，不会把 Runtime 单测算作模型成绩。

Fixture 中若只验证了故障 seam、没有驱动 Agent 完成后续规划、授权、替代任务或独立验证，对应 `recovery_status` 会保留 `incomplete`。这表示该离线槽位没有声称完整任务恢复，不等同于不变量失败；属于 live 子集的场景会在 live trial 中单独验收完整恢复和任务结果。

故障未触发、注入错误、证据不完整、来源不一致、基础设施错误和未运行槽位独立列出。`subagent-timeout` 的父模型调用是真实绑定；子模型调用由 timeout fault driver 替代，在 trial 中标记 `source="injected"`、`counted_as_real=false`，不算真实子模型请求。未观测 token 不填零，价格快照缺失时成本为 `null`。

## 命令

先校验完整任务和成功标准。该命令不读取真实 provider 凭据、不调用模型；输出包括所有场景、精确工具与权限、预算、反馈策略、grader ID 和材料摘要：

```bash
PYTHONPATH=src python -m mini_agent.evaluation validate-reliability tests/fixtures/evaluation/reliability/suite.json
```

离线自测执行 50 个槽位，包含 stdio 和显式 loopback HTTP MCP fixtures，不发送真实 API 请求：

```bash
PYTHONPATH=src python -m mini_agent.evaluation self-test-reliability --output /private/tmp/mini-agent-reliability-offline
```

Live 前应审阅校验输出中的完整任务、权限、反馈与预算，再显式传入 `--live`。命令固定运行七个场景各三次；执行一次，不自动重试：

```bash
PYTHONPATH=src python -m mini_agent.evaluation run-reliability tests/fixtures/evaluation/reliability/suite.json --live --repeats 3 --output docs/evaluation/baselines/v0.52/live-<run-id>
PYTHONPATH=src python -m mini_agent.evaluation report-reliability docs/evaluation/baselines/v0.52/live-<run-id>
```

运行器在首次 trial 前冻结并核对全部七个本地 model binding；真实 provider/profile 只从本地配置装配，不写入 fixture 或结果。缺少配置时创建带明确 infrastructure error 的未评分 live 槽位，不发模型请求。输出目录必须不存在；每次批次使用独立目录。

## 原始证据与报告

Run 目录包含冻结 suite 与场景副本、按固定顺序预先建立的 `suite-run.json`、每个已启动 trial 的 `trial.json`、fault/feedback/recovery 事件、State/Trace 摘要、受限日志、差异和必要的 MCP/持久化记录。崩溃恢复保留源 session 与派生 session 的关联摘要；源文件必须逐字节不变。worker 请求只持久化无凭据来源引用；provider endpoint、model ID、API key 和认证头不能写入请求、日志、State、Trace 或报告。Live runner 在发布前再对生成的文本 artifact 做脱敏。

`report-reliability` 只读核对冻结 suite、槽位顺序、trial 身份和 SHA-256 evidence 引用，再从原始 trial 重建逐场景与 fixture/live 汇总。普通 `report` 会跳过这种独立结果格式并提示使用专用命令；原编码题 schema 1/2 读取方式和指标不变。报告损坏或来源断链时保留 unreadable/incomplete 项，不补猜结论。

## 首批 Live 结果（suite 1.5，预算定点修复前）

批次[`live-20260929-07/`](baselines/v0.52/live-20260929-07/)按固定顺序执行 21 个槽位：19 个产生可评分 trial，2 个以 `provider_protocol_error` 结束。两条 provider 错误没有状态码或详情，脱敏诊断只显示响应调用校验位置；根因未知，不能由此判断为 HTTP 400。所有来源和摘要一致，无证据断链、清理失败或人工复核项。没有重试、补跑或替换。

17 个进入报告评分的已触发故障 trial 中，不变量 17/17 通过；两个基础设施错误 trial 的原始材料也记录了故障触发和不变量通过，但它们不进入评分 cohort。权限拒绝 3/3 是边界专用 trial，不计入任务恢复。严格恢复成功为 5/14（35.7%）。分层任务结果为：16 个任务 trial、10 个文件结果正确、5 个通过当前 generation 验证、5 个模型正常完成，且这 5 个同时满足全部条件。

| 场景 | 故障触发 | 不变量 | Grader | 恢复成功 |
|---|---:|---:|---:|---:|
| `tool-handler-exception` | 2/3 纳入评分；另 1 个基础设施错误 trial 也触发 | 2/2 通过；基础设施 trial 通过 | 2/2 纳入评分 | 0/2 |
| `permission-denied` | 3/3 | 3/3 通过 | 边界专用 | 不适用 |
| `verification-repair` | 3/3 | 3/3 通过 | 3/3 | 3/3 |
| `process-wait-timeout` | 2/3（1 次未触发） | 2 通过、1 不完整 | 0/3 | 0/2 |
| `crash-after-handler` | 1/3 纳入评分；1 次未触发；另 1 个基础设施错误 trial 也触发 | 1 通过、1 不完整；基础设施 trial 通过 | 1/2 纳入评分 trial | 0/1 |
| `mcp-disconnect` | 3/3 | 3/3 通过 | 2/3 | 2/3 |
| `subagent-timeout` | 3/3 | 3/3 通过 | 2/3 | 0/3 |

预算诊断有 130 个请求决策：117 个 provider 响应、11 个因受保护上下文无法放入剩余任务预算而拒绝、2 个 provider 协议错误。114/117 个响应的实际输入高于原始估算；没有输入超过校准预留、没有输出超过分配上限，观测预算超额为 0。保守拒绝仍会使 Agent 无法继续调查或完成收尾。三项场景未达到每场景 3 个已触发且可评分样本，故 suite 1.5 live 基线**未完成**；详细试次、冻结审阅清单和逐请求原始证据见[基线记录](baselines/v0.52/README.md)。

## 定点修复后的 Live 批次（suite 1.5）

重新校验后的 suite 摘要仍为 `833eefa4cb6e108439fb407c0981383fcd8b04724887e42c40ba9bcd65baa3a7`，Runtime 指纹改为 `a32092e178ce86eb106f46a082801dca8b44c9b80d03d69d06661d2aee592a72`。首个批次[`live-20260929-08/`](baselines/v0.52/live-20260929-08/)的 21 个槽位全为 `provider_http_503`；没有可评分 trial，恢复分母为 0。

provider 修复后，第二个独立批次[`live-20260929-09/`](baselines/v0.52/live-20260929-09/)记录 20 个 `provider_connection_error` 和 1 个没有触发故障的 trial。连接错误的原始异常类型为 19 个 `ProviderConnectionError`、1 个 `ProviderTimeoutError`；唯一获得模型响应的 trial 在故障触发前以 `token_limit` 停止。三个 `verification-repair` 试次已命中注入点，但其模型请求随后失败，作为基础设施错误排除。21 个 trial 均清理完成；恢复分母仍为 0，不能据此判断修复后的恢复率。

两批均没有重试或替换槽位；重建报告与归档报告逐字节一致。所有证据和槽位详情见[基线说明](baselines/v0.52/README.md)。

## suite 1.5 后的预算定点修复

当前工作请求已可使用验证和计划推进额度，后续无工具最终回复单独预留。预算拒绝新增受保护上下文下限、裁剪前大小、输入额度、收尾预留和阻塞项。完整 Chat 响应的工具参数校验错误新增无正文诊断，保留已报告用量；完整非流式非法参数和明确 length 停止的输出作为任务失败，来源不明的流式拼装错误仍独立保留。历史 trial 不重分类。

具体修改、离线验收与证据限制见[定点修复记录](baselines/v0.52/stage-budget-repair.md)。修复后的两个 live 批次都被 provider 基础设施错误挡住，没有产生恢复分母；此前 suite 1.5 的 5/14 是修复前历史成绩，基线仍未完成。

## #09 后的离线估算修正（2026-09-30）

#09 的唯一模型响应显示单次输入超出旧预留，任务总额度仍充足。候选 Runtime 已按完整 OpenAI Chat 请求 JSON 的 UTF-8 大小建立带明确余量的代理估算，并在容量不足时裁剪历史工具回合，保留用户纠正和受保护事实；历史预留与新估算器的校准样本隔离。该代理不等于 provider token 数，实际超预留后的工具阻止逻辑保持有效。详见[离线修复说明](baselines/v0.52/estimator-offline-repair.md)。本轮未发送探针或启动新 live，suite 1.5 的历史分母和成绩不变。

## 历史套件 1.2

Suite 1.2 摘要为 `127fc54a543693630e23b602e2d3db460030b54746aa66fc67c0c9d70f7600a4`。第三批 1.1 live 复核后，崩溃任务改为准确描述注入前与恢复后的阶段；子代理超时固定 `child_budget`，单次 max_tokens=32000，其他字段由任务和预检完整公开。只有实际到达子请求前才记录 timeout 注入；错误合同仍经正常校验拒绝，不能据启动确认推断故障已触发。该 fixture 只允许一次注入调查，不发送真实子 provider 请求。

复杂场景补齐父侧目录与搜索工具。未触发目标故障时，相关不变量为 `incomplete`，Agent 的前置失败仍由 `pre_injection_stop_reason`、State 和评分结果保留。权限、预算和独立验证规则保持正式 Runtime 合同。

OpenAI Chat 普通和流式响应保留有界 `reasoning_content` 字段，并通过后续请求原样回传；不输出为终端正文。suite 1.2 live 已验证没有再出现 HTTP 400；新批次单独评分，历史 artifact 保留，新合同不继承旧成绩。

历史 suite 1.2 离线矩阵已完成 50/50 槽位，故障和不变量全部通过；完整 pytest 为 749 passed。独立 live 批次完成 21 个槽位、无基础设施错误，恢复成功 5/16；但进程等待超时只触发 1/3 次，live 基线仍未完成。逐场景数据和原始证据见[基线说明](baselines/v0.52/README.md)。

## 历史评测器修正（2026-09-29，suite 1.1）

当时冻结套件为 `reliability-boundaries@1.1`，摘要 `53180d91b153c480aa580c433491b361120de3d1f120a5032632b89e8bcb7f05`。1.0 的历史结果保留，不能用它们判断修正版的任务成功率。修正包括：

- 六个恢复场景的 initial 和 known_good 均提供非空公开 unittest；预检和 live 使用同一套公开测试与独立行为评分，必须拒绝 initial、接受 known_good。Live 运行器在解析凭据前完成预检。
- 独立评分器在有界子进程中调用函数检查数值行为，接受等价正确实现；不限定函数只能有一条乘法 return。
- 权限拒绝按正式 Runtime 的 failed 终止合同验收；通过规范参数摘要匹配拒绝 attempt，不再访问不存在的 `ExecutionAttempt.arguments`。
- 进程 fixture 持续运行至显式终止，等待阶段不产生输出；绑定 State 的 ProcessManager，避免模型响应时间超过 0.8 秒后故障已消失。
- 预算预检查包含下一次请求的上下文和工具 schema 估算；worker 保留运行阶段、代码位置、脱敏 provider 状态/详情和工具 schema 摘要，避免评分阶段异常被报告为未启动。

完整 worker 的固定响应回归与组件探针分别验收；它们均不发送真实 provider 请求。当时 HTTP 400 的服务端原因尚未确认，只补了诊断。后续 1.1 批次确认 UUID schema 与 reasoning_content 兼容问题；修正状态见上文当前套件及基线说明。

修正版离线矩阵已完成 50/50 槽位，故障全部触发、不变量全部通过，无基础设施错误或断链证据。运行 ID 为 `0326a821-b347-460a-8ec6-4ffd1c491c89`，原始结果位于本机 `/private/tmp/mini-agent-reliability-v052-suite11-fix-escalated/`。沙箱限制下的独立尝试有 8 个 loopback 注入错误，保留于 `/private/tmp/mini-agent-reliability-v052-suite11-fix/`；未逐槽替换。这些均为离线边界证据，不计作模型成绩。

## 历史状态（suite 1.0）

截至 2026-09-28，suite 摘要 `8ea1fab57ec079a86b22fab140614dada88cc1e22e8e37f527e5c36b9d08904f` 的脱敏修复候选离线矩阵已在[`offline-20260928-security-redaction/`](baselines/v0.52/offline-20260928-security-redaction/)完成 50/50 槽位：故障全部触发，不变量 50/50 通过，无基础设施错误、断链证据或未运行槽位。受限沙箱尝试因不能绑定 loopback HTTP fixture 而有 8 个不完整槽位，已单独保存在[`offline-20260928-sandbox-loopback-limited/`](baselines/v0.52/offline-20260928-sandbox-loopback-limited/)，不计入通过结果。

首批[`live-20260928-01/`](baselines/v0.52/live-20260928-01/)和中间合同批次[`live-20260928-02/`](baselines/v0.52/live-20260928-02/)继续保留。同一历史 suite 的前一批[`live-20260928-03/`](baselines/v0.52/live-20260928-03/)记录 8 个 trial、13 个基础设施错误；5 次故障触发，3 个不变量通过，0 个 grader 通过，恢复成功率 0/5。脱敏修复后的批次[`live-20260928-04/`](baselines/v0.52/live-20260928-04/)保留 21 个终态槽位：17 个 trial、4 个基础设施错误；9 次故障触发，7 个不变量通过，1 个 grader 通过，恢复成功率 0/9。`tool-handler-exception` 与 `mcp-disconnect` 各 3/3 触发且不变量通过；`verification-repair` 3/3 触发，1 个不变量与 grader 通过、2 个失败；`permission-denied` 两次未触发且不变量失败，另一次 `AttributeError`；`process-wait-timeout` 和 `crash-after-handler` 各 3 次未触发；`subagent-timeout` 三槽因 provider HTTP 400 未评分。所有来源一致且证据可读，但四个场景没有各自达到 3 个可评分触发样本，live 基线仍未完成。没有自动重试、补跑或替换失败槽位。

`live-20260928-03/` 复现出 live 请求 artifact 曾持久化本地 provider 脱敏值的问题。相同字段也存在于前三批 live 请求文件；这些历史目录的请求副本都已清除敏感字段，原始 SHA-256 与脱敏复现说明分别保存在各自的 `artifact-security-findings.json`。修复后 `live-20260928-04/` 的 21 个请求均无 `redactions` 字段，扫描所有生成 artifact 未发现 provider 敏感值。Runner 不持久化脱敏值，并在发布前再次清理文本 artifact。修复后的完整离线矩阵通过；live 基线仍因故障触发及独立任务验收不足而未完成。Suite 1.1 的编码基线仍单独未完成，v0.51 的争议成绩继续保留。

## 历史套件 1.3：评测隔离与诊断

当前摘要为 `38e9a5ffd8aafdf897928bd0c3f51c84797f0a5f1dee4c18f5a9e2521dae4893`。进程相对 cwd 按 trial 工作区解析，非法目录在 handler 准入前拒绝；崩溃前后共用评测受保护提示词，避免恢复时额外导入上层项目 AGENTS.md。正式指令发现、安全与完成规则不变。

原始 trial 的 `state_summary.tool_diagnostics` 提供有界异常类型、代码位置、参数 hash 和 errno；`phases` 下的 `request_diagnostics` 记录逐请求数字诊断。`message_groups` 将消息估算分为 system、state、notice、summary 和 history，工具 schema 单列；`reasoning_tokens_subset_estimated` 属于消息总量的子集，不能再次相加。`usage_source` 区分 provider、estimated 与 unobserved；实际输入与预留估算之比帮助判断估算偏差。恢复阶段计入 `prior_phase_tokens`，拒绝请求记录原因且观测用量保持 null。诊断不包含消息、推理或工具参数正文。

总预算未提高；这些诊断用于区分上下文开销、模型调查用量与估算偏差。Suite 1.3 的 21 槽历史成绩为恢复 8/17，进程超时触发 2/3，样本基线未完成。Suite 1.4 首个 live 批次完成 21/21，七个场景均 3/3 触发且可评分，达到样本覆盖门槛；恢复成功 1/18，无基础设施错误。128 个已响应请求都未超过校准预留，16 个请求在发送前因预算不足被拒；没有 live provider 响应超额，因此响应后阻止工具/完成的分支尚未由真实响应触发。保守拒绝与重复上下文问题可能有关，执行顺序与上下文效率仍待修复。不同合同的成绩不合并，详见[基线说明](baselines/v0.52/README.md)。

## 历史套件 1.4：请求预算预留

套件摘要为 `ae49963f8385f27caaa2521a8ac167d0aa2c193f5368fb636eabb95763791365`，冻结材料与预检公开 `request_budget_policy`。输入估算以初始 2 倍系数加 256 tokens 预留；根据本 trial 纯 provider 输入/原估算的最高比例加 10% 余量上调，恢复继承崩溃前的校准，不重置累计预算。输出上限从同一次预留的余额计算，至少需容纳 256 输出 tokens（较小配置上限除外）才发送请求。

`budget_refusal_reason` 区分 `budget_already_exceeded`、`budget_exhausted`、`insufficient_request_budget`。实际超额响应不能执行工具 handler 或完成任务，所有工具调用仍逐项回灌预算错误；实际计数照实保留。无 provider 用量时保留估算来源、不用于校准。预估不是精确 tokenizer，未知偏差仍可能造成已发送请求超额，因此不能宣称绝对预算保证。Suite 1.4 的 21 槽 live 结果、请求拒绝诊断和未覆盖分支见[基线记录](baselines/v0.52/README.md)。

## 历史候选 1.5：累计预算与完成证据

Suite 摘要为 `833eefa4cb6e108439fb407c0981383fcd8b04724887e42c40ba9bcd65baa3a7`。Live 显式启用正式 `TaskBudgetController`，主请求、同 binding 摘要和恢复会话共享 64,000 父 token 上限。评测只记录控制器的准入提案，不能另行发放额度。子代理沿用独立及聚合预算，不重复计入父模型账本。

各请求按 binding、工具 schema 和 work/summary/final 类别独立校准。前两次 provider 观测前采用 `2 × raw + 256`；之后采用最近八次最大正误差，再加 10% 和 256。主执行、摘要、收尾输出分别最多 1024/512/512 tokens；最低准入输出为 256，较小模型上限除外。未知用量按整个预留计费、不参与校准；真实超额照实记录并停止 handler 或完成。

每次请求前把唯一预留持久提交，返回后把结算提交；提交失败即停止。恢复中的未知 pending 请求只计费一次，不能重置上限。新请求视图保留用户消息、受保护指令、当前 State 与最近两轮完整工具协议；入选推理字段不改写。摘要只有估计总成本更低且能获得额度时才请求模型；不足时用有界视图，保护内容仍放不下则明确阻塞。

只有当前代已验证或活动计划全部完成，并且没有其他完成阻塞时，才使用无工具 schema 的收尾请求。最终回复仍须真实没有 tool calls，并经过原有完成校验；收尾返回工具调用会逐项回灌拒绝结果、阻塞任务。不会自动完成计划、运行验证或宣告成功。

新 trial 的 `state_summary.completion_evidence` 保留 `artifact_correct`、`current_generation_verified`、`model_completed` 和阻塞项。新报告 `completion_layers` 分别计数，`recovery_success_rate` 的严格分子不变；历史报告不补推新证据。Suite 1.5 live 结果为 5/14 严格恢复成功，但三个场景未达到样本门槛，基线未完成；逐场景和请求诊断见上节及[基线](baselines/v0.52/README.md)。历史成本回放不推断恢复结果。

## 2026-09-30 独立 provider 探针

当前 binding 作为独立组运行了至多三次、无重试的计费和协议探针：第一请求成功，第二工具请求出现 `ProviderProtocolError`，因此第三请求没有发送。结果与 08/09 及恢复评分分开；保守费用上界低于用户批准的 1 USD。原始数字记录和证据限制见[基线说明](baselines/v0.52/README.md)及[探针复核](baselines/v0.52/provider-probe-20260930-01/review.json)。当前仍不能启动新的 21 槽位 live。

后续独立诊断 02/03 将错误定位到流式工具调用字段校验，8,192 输出额度单独不能消除错误。适配器忽略后续空工具名和 `null` 参数片段后，探针 04 完成三请求工具回灌链路；四组累计保守费用上界 0.42294812 USD，均未触及 live 槽位。证据和历史归因限制见[基线说明](baselines/v0.52/README.md)。小请求协议已通过定点检查；#09 大输入预留与 Agent 恢复仍需单独验收，v0.52 live 基线未完成。

另一次合成大输入请求在当前 binding 上实报 5,124 输入 token，低于 7,084 的代理估算和 14,424 的冷启动预留；所有探针累计保守费用上界 0.42367052 USD。其字节形状接近 #09 的事后重建，但原请求未保存且 binding 不同，不能将此结果写作 #09 的复现或通用预算保证。见[原始数字](baselines/v0.52/provider-large-input-20260930-01/probe.json)与[复核](baselines/v0.52/provider-large-input-20260930-01/review.json)。

修正后的探针 05 又在 suite 的 1,024 输出上限下完成三步工具协议；六组独立探针累计保守费用上界 0.42375368 USD。离线故障矩阵 50/50 边界不变量通过，无基础设施错误；这不是 live 恢复成绩。详情见[综合验收](baselines/v0.52/provider-validation-20260930.json)，v0.52 live 基线仍未完成。
