# 阶段十四：Evaluation & Regression 实施计划

> 状态：2026-10-08 suite 1.6 候选已完成定点修复与离线验收（832 项测试、50/50 离线边界）；尚无该候选 live 成绩。最新 2026-09-30 suite 1.5 独立 live 批次完成 21/21 槽位，严格恢复 6/14；进程等待超时和崩溃后恢复各只有 1/3 次故障触发，故 v0.52 live 样本基线仍未完成。此前 #08/#09 和修复前 5/14 保留为独立历史结果。v0.50 已完成，v0.51 编码基线仍单独未完成，阶段十四整体尚未完成
> 建议版本范围：`v0.50` Evaluation Harness、`v0.51` Coding Benchmark、`v0.52` Reliability Evaluation、`v0.53` Regression & Comparison
> 能力前置：阶段十三轻量 Agent Collaboration（`v0.47`–`v0.49`）及此前的执行、验证、恢复、Memory、MCP 与 Skills
> 关联计划：`agent-collaboration-plan.md`、`reliable-execution-plan.md`、`session-persistence-resume-plan.md`、`adaptive-planning-plan.md`

## 2026-10-08 已观测失败的修复

用户授权修复。当前可执行候选升级为 suite 1.6，旧 1.5 继续只读重建。已定位 `SessionValidationError`：非法 `recover.caused_by_failure_id` 被拒后仍写成因果引用，导致 State 导出失败。拒绝记录现仅保留存在的引用，未知编号记为 null，且只允许 rejected 记录无因果 failure；不伪造失败、不推进 generation。

预算修复通过应用在 Runtime 创建/恢复时提供独立收尾提示实现；保留完整项目指令、用户及历史 system 约束，收尾只携带权威完成事实，不再重复调查工具回合。工作提示、校准、64k 上限和独立验证不变。验证修复场景补齐目录和搜索工具并重新冻结，不能把旧批次成绩移入新 suite。文件、测试与限制见[修复记录](../evaluation/baselines/v0.52/live-failure-repair-20261008.md)。本轮不发模型请求；须先通过离线验收，再决定是否开展定点 live。

## 2026-09-30 当前绑定 live 批次

用户显式启动一次独立 21 槽位批次，重新冻结的 suite 摘要为 `833eefa4…`、Runtime 指纹为 `90f115c2…`、binding 指纹为 `fbfec446…`。21/21 槽位终态可读，无基础设施、清理或来源错误；故障触发 17 次，不变量 15 通过、2 失败、4 不完整，grader 11 通过，严格恢复 6/14。136 个请求决策中 126 个得到 provider 用量响应，10 个因受保护上下文无法放入剩余额度而提前拒绝；126 个响应的输入没有超过原始代理估算或校准预留。报告可逐字节重建，详见[新批次报告](../evaluation/baselines/v0.52/live-20260930-01/report.json)和[核对记录](../evaluation/baselines/v0.52/live-20260930-01/report-validation.json)。

进程等待超时与崩溃后恢复均仅 1/3 次故障触发，不能补跑或替换另外四个槽位；其中一个崩溃槽位在触发前发生本地 `SessionValidationError`，具体触发条件尚未确认。工具异常恢复 2/3、验证修复 1/3、MCP 断连 2/3、子代理超时 0/3；权限拒绝 3/3 边界通过。完整 live 样本覆盖仍未达门槛。下一步应分别调查本地 session 保存异常与未触发槽位的执行路径，再决定是否需要修改实现；不按恢复率目标自动续跑下一批。

## 2026-09-30 独立 provider 探针

当前 binding 已经用户批准作为不同于 #09 的独立探针组，费用上限 1 USD，价格据小米第一方海外实时 API 表。脱敏探针在两次请求后因工具请求 `ProviderProtocolError` 停止：普通请求用量 12 输入 / 15 输出，第三次配对请求未发送；未重试、未占 live 槽位。费用保守上界约 0.1403 USD。详情见[探针复核](../evaluation/baselines/v0.52/provider-probe-20260930-01/review.json)。下一步先定位协议错误；证据不足时不整批重跑。

后续独立探针 02/03 把错误定位到流式工具调用的名称/参数校验，提高输出上限仍复现。适配器现忽略后续空字段覆盖；探针 04 完成三请求工具回灌链路，四组累计保守费用上界为 0.42294812 USD。详见[基线说明](../evaluation/baselines/v0.52/README.md)和[探针 04 复核](../evaluation/baselines/v0.52/provider-probe-20260930-04/review.json)。小请求协议检查通过；历史错误的唯一根因、大输入估算与 Agent 恢复尚未得到这次证据验证，因此继续保持 suite 1.5 live 基线未完成。

当前 binding 的一次合成大输入请求实报 5,124 输入 token，低于 7,084 代理估算与 14,424 冷启动预留；探针累计保守费用上界 0.42367052 USD。该请求接近 #09 的事后字节形状，但正文与 binding 不同，不能代替历史复现或 live 恢复样本。[复核记录](../evaluation/baselines/v0.52/provider-large-input-20260930-01/review.json)单列该限制。下一步仍须先重新冻结当前 Runtime 与 suite，评估是否需要一次受控 live 批次；旧 21 槽成绩不混合或补跑。

再以 suite 的 1,024 输出额度完成小请求工具回灌探针；六组探针累计保守费用上界 0.42375368 USD。suite 1.5 预检有效，修正后的 Runtime 离线矩阵 50/50 边界通过。综合数字见[验收摘要](../evaluation/baselines/v0.52/provider-validation-20260930.json)。这完成了定点协议与估算形状验证，尚未形成新的 live 恢复基线；下一步需在当前 Runtime 指纹下冻结审阅清单，再考虑独立 live 批次。

## suite 1.5 后的定点修复

2026-09-30 离线追加修正：#09 唯一 provider 响应实际输入 13,748，旧估算 2,918、预留 6,092；累计额度并未耗尽。OpenAI Chat 准入现估算完整请求 JSON 的 UTF-8 大小（含字段、工具 schema）并加明确余量，估算器版本进入校准 key；容量紧张时仅裁剪可省略历史，同时保护用户纠正。长历史晚期 19k/27k 回归、旧报告逐字节重建及离线矩阵均需通过。详见[本轮离线记录](../evaluation/baselines/v0.52/estimator-offline-repair.md)。本轮不发送付费探针，不把旧 suite 1.5 审阅清单当作新候选冻结依据。

暂停整批 paid live。已修正当前工作请求无法使用计划推进和验证预留的问题，补齐预算拒绝和工具参数 JSON 错误诊断，并添加长调查历史下的晚期恢复回归。具体文件、验收证据与限制见[定点修复记录](../evaluation/baselines/v0.52/stage-budget-repair.md)。本轮不创建 suite 1.6、不修改历史批次成绩、不补跑失败槽位。

下一次 live 前必须重新冻结候选运行时、请求策略与评分分类，并审阅新的清单；suite 1.5 的旧审阅清单不能授权已变化的候选。只运行一次独立批次，保留所有失败。后续仅凭可复现的实现缺陷继续修复；重复调查、执行顺序和模型恢复能力不足作为能力结果记录，不能以达到某个恢复率为理由无限循环。

## v0.50 实施记录

- 已新增 `mini_agent.evaluation` 的 schema、worker、runner、report 与四条命令：`validate`、显式 `run --live`、两次固定响应 `self-test`、只读重建 `report`。
- 实际 trial 使用 `AgentRuntime.run()`、`ParentRuntimePolicy`、`ToolExecutor`、六工具上限和非交互 PermissionGate；工具可见面与显式预授权分开，工具路径在 handler 前限定到 trial 工作区。
- Runner 为每次 trial 新建工作区、HOME、临时 Memory 路径，限制 fixture/工作区/日志/diff/result 大小，并在 Agent 进程停止后运行 grader。单次结果原子发布；live 与 fixture 不合并分母。
- 新增 scale 修复题和隐藏 grader。离线检查确认原始 fixture 不通过、已知正确修复通过；两次固定响应 trial 均从相同初始文件独立完成并通过 grader。
- 定向回归：`PYTHONPATH=src python -m pytest -q tests/test_evaluation_v050.py`，12 passed；CLI `validate` 和两次独立输出目录中的 `self-test` 均通过。
- 完整验收：`PYTHONPATH=src python -m pytest -q`，653 passed；`scripts/check_tutorials.py`、`scripts/check_tutorial_facts.py`、`scripts/check_readme.py` 和 `git diff --check` 均通过。
- 真实 `--live` 验收已完成：在沙箱内的首次 ProviderConnectionError 作为失败 live trial 保留；经网络权限批准后成功 trial `fecf1d04-48f2-4c86-bf42-e0588dabc1fa` 以 `agent_stop_reason=text`、State `done`、grader 通过和 cleanup complete 收束。成功 trial 有 4 次成功模型响应、3 次工具调用、provider usage 6,520 input / 243 output tokens，Agent 3,004 ms、grader 38 ms；价格快照缺失，成本仍为 null。
- live 原始结果目录：`/private/tmp/mini-agent-eval-v050-live`；保留两条 live trial，报告分母 2、通过 1、连接错误 1。该小样本仅用于验证链路，不作为编码能力基准。

## v0.51 实施记录

- 已新增冻结题集 `coding-benchmark@1.0`，固定顺序为 `pagination-boundary`、`orders-discount-receipt`、`cache-expiry-regression`、`config-priority-investigation`。每题均有独立初始文件、grader 和正确版本；配置调查题没有在任务正文中指出故障文件。
- 离线预检已逐题确认：四个原始版本均被拒绝，四个正确版本均通过。缓存题要求的 unittest 还必须在正确代码上通过，并在原始错误实现上以断言失败；分页、订单单侧修复、弱缓存测试、配置优先级错误变体均被 grader 拒绝。
- 已新增 Suite / SuiteRun 有界合同、题目和文件树摘要、ID/version 指纹、schema 2 suite TrialResult、12 槽位顺序编排、原子账本更新、启动失败/中断保留及逐题报告重建；suite run 固定 Git revision、运行时代码指纹和模型来源，并在 trial 前后检查实现来源；原 schema 1 Case/TrialRequest/单题 TrialResult 保持可读。
- 已新增 `validate-suite`、`run-suite --live --repeats 3`、`report-suite`，并更新第 51 课、操作手册、评测说明、中英文 README、CHANGELOG 和包版本。
- suite 1.0 经用户审阅后已顺序完成 12 次 live trial，原始记录见 `docs/evaluation/baselines/v0.51/`；未重跑或替换样本。2026-09-28 评审发现收据评分采用未公开的精确格式，旧成绩保留并标注争议；suite 已提升为 1.1，公开格式与参数拒绝要求。1.1 的真实基线尚未运行，不能沿用 1.0 成绩。
- 评审修复：临时清理只恢复 runner 自建只读参考目录权限，不跟随符号链接；中断先有界停止子进程并保存已启动 trial，再中断套件；来源不一致的 trial 保留但排除评分，阻止完整基线判定。
- 修复验收：评测定向测试 31 passed，完整 pytest 674 passed，suite 1.1 离线预检、教程结构、README 与 diff 检查通过；教程事实检查仅因用户尚未创建 `v0.51` tag 未通过，助手不代建 tag。未重新运行 live 或改写历史 trial。

## v0.52 实施记录

- 已冻结 `reliability-boundaries@1.0`：18 个场景，含 MCP stdio/loopback HTTP、3 个 durable commit 失败边界和 2 种恢复反馈变体；离线计划 50 槽位、live 子集 7 场景各 3 次，共 21 槽位。Suite、scenario、request 和 reliability TrialResult 使用独立 schema 1，并固定材料摘要。当时审阅候选 suite 摘要为 `8ea1fab57ec079a86b22fab140614dada88cc1e22e8e37f527e5c36b9d08904f`。
- 已新增具名有限故障驱动器、Runtime worker、Evidence 核验、只读专用报告、用户侧 `/resolve`/计划决定/保存动作提取，以及四条专用 CLI 命令。Live worker 使用 `AgentRuntime.run()`、`ParentRuntimePolicy`、标准工具和 Manager；崩溃恢复由新 Python 进程 claim 派生 session。没有修改正式 Runtime 默认行为或扩大权限。
- 当前候选 `offline-20260928-security-redaction` 已运行 50/50 槽位：全部 fault 实际触发，50/50 不变量通过；无基础设施错误、evidence incomplete 或未运行槽位。一次受限沙箱自测因 loopback HTTP fixture 无法启动而出现 8 个注入错误，独立保留于 `offline-20260928-sandbox-loopback-limited`，没有逐槽补跑。Fixture 不执行 Agent，因此 task grader、任务成功及恢复成功率为 null。
- 四批 live 证据均已保留。首批 `live-20260928-01` 的多个 fault 未触发。中间候选 `b0db6164…0125a3c` 的 `live-20260928-02` 计划 21 槽，18 个 trial 运行、3 个子代理调用因 provider HTTP 400 记录为基础设施错误；18 个 trial 故障触发 6 次、不变量通过 3 次、grader 通过 0 次，恢复成功 0/6。`live-20260928-03` 记录 8 个 trial、13 个基础设施错误，故障触发 5 次、不变量通过 3 次、grader 通过 0 次，恢复成功 0/5。脱敏修复后的 `live-20260928-04` 记录 17 个 trial、4 个基础设施错误，故障触发 9 次、不变量通过 7 次、grader 通过 1 次，恢复成功 0/9。均未自动补跑或替换。
- Live worker 未注册标准 `recover`/`request_replan` 控制工具和任务未公开精确触发动作的问题已在前序候选修正。live 批次复现出请求 artifact 将本地 provider 脱敏值持久化；前三批的请求副本均已脱敏，原始摘要证据分别保留在 `artifact-security-findings.json`，runner 已修正为不持久化脱敏值并在发布前扫描产物。修复版离线矩阵全通过；live 基线仍因四个场景未达到至少 3 个可评分触发样本而未完成。没有更改正式 Runtime 默认行为或扩张生产授权。Suite 1.1 编码基线仍单独未完成，v0.51 suite 1.0 争议成绩未更改。

### 2026-09-29 评测器修正

- 冻结套件升级为 `reliability-boundaries@1.1`；旧版 artifact 保留，当前固定摘要见套件与基线说明。
- `tests/fixtures/evaluation/reliability/`：补齐六个恢复场景的 initial/known_good 公开测试；权限拒绝改为安全终止边界任务；进程改为等待显式终止，重算材料与 suite 摘要。
- `evaluation/reliability_grader.py`：新增有界独立进程行为评分，支持等价正确代码；`reliability.py` 在首次 live 请求前以公开测试和行为评分共同校准。
- `evaluation/reliability_worker.py`：修正拒绝 attempt 的参数匹配、进程绑定与清理、请求前预算估算、工具回灌证据；保留执行阶段及脱敏异常位置/provider 诊断。崩溃恢复也区分评分器不可用和任务失败。
- `evaluation/reliability_schema.py` 保持旧归档可读、只允许当前套件新运行；`reliability_report.py` 排除纯边界权限场景的任务/恢复分母。
- `tests/test_evaluation_worker_v052.py` 驱动完整 live worker 装配，使用固定模型响应检查权限终止、慢响应下进程超时、读取异常恢复、验证失败修复、请求预算及 HTTP 诊断；不发送真实 API 请求。
- Suite 1.1 live 已顺序运行三批，完整数据和逐场景结果见 `docs/evaluation/baselines/v0.52/README.md`。最新批次 `live-20260929-03` 的 21 个槽位均有终态，20 个 trial 可评分、1 个 `mcp-disconnect` 槽位因 provider HTTP 400 作为基础设施错误排除；故障触发 13 次、不变量通过 12 次、grader 通过 9 次、恢复成功 5/10。`process-wait-timeout`、`crash-after-handler`、`mcp-disconnect`、`subagent-timeout` 均未达到各 3 个可评分触发样本，基线未完成，不自动补跑。
- `live-20260929-01` 的子代理异常诊断曾因读取错误的 `binding.profile.model` 属性而遮蔽原异常；改用 `model_id` 后保留旧批次并另开批次。`live-20260929-02` 进一步暴露 provider 不接受 JSON Schema 正则中的 Python `\Z`；`tools/delegation.py` 现对 provider schema 使用可移植 pattern，本地 UUID 参数校验仍使用严格 fullmatch。相关回归测试 43 passed，`git diff --check` 通过。
- `live-20260929-03` 暴露一条不同的 provider HTTP 400：服务端在 thinking mode 要求后续请求携带 `reasoning_content`，通用 OpenAI Chat 适配器没有保留该 provider 扩展字段。脱敏诊断已保留；该兼容问题尚未修复或用 live 验证，不属于 API 凭据错误。一次失败请求只计入基础设施槽，不自动重试。
- 修正验收：worker/schema/recovery/services 回归 58 passed；便携 UUID schema 修正相关测试 43 passed；套件 1.1 公开测试与行为评分预检通过，50/50 离线矩阵通过。最终全量 `PYTHONPATH=src python -m pytest -q` 为 734 passed。教程结构检查、README 检查和 `git diff --check` 通过；教程事实检查仍只因缺少用户手动创建的 `v0.52` tag 而未通过。

### 第三批 live 后修正（suite 1.2）

- `providers/openai_chat.py` 保留普通响应与流式分片的 `reasoning_content`，验证类型和长度，仅作为普通 assistant 协议数据传递；不显示到正文，不进入 State/Trace 摘要。新 `tests/test_reasoning_content_v052.py` 检查 HTTP 编解码、工具回合、上下文与 session 的完整往返，以及缺失/空值、坏类型和超限拒绝。
- `tests/fixtures/evaluation/reliability/` 纠正崩溃前置阶段；超时场景冻结完整子预算；四个复杂场景补齐父侧目录与搜索工具。合同升为 1.2，重算全部材料与 suite 摘要，旧 1.0/1.1 归档可读。
- `reliability_worker.py` 在 schema 和参数校验中同时强制公开子预算，拒绝第二次调查和真实子 provider 请求；未触发目标故障的不变量记为 incomplete，并保留前置停止原因。完整 worker 回归覆盖子超时领取与任务恢复、崩溃副作用和 pending 边界。
- 本次 suite 1.2 live 使用新目录 `docs/evaluation/baselines/v0.52/live-20260929-04/`，旧 trial/report 未修改；没有放宽生产 Runtime 的权限、恢复或完成条件。21 个槽位均有终态，无基础设施错误或自动重试。
- Live 结果：故障触发 19 次、不变量通过 17 项/失败 2 项/不完整 2 项、grader 通过 12 项，恢复成功 5/16。`crash-after-handler` 三次均正确触发且边界不变量通过，但都在当前 generation 验证前耗尽总 token 预算；`subagent-timeout` 三次均触发、结果领取和用量结算不变量通过，仅一次完整恢复；`mcp-disconnect` 三次均触发且没有 HTTP 400，仅一次恢复成功。`process-wait-timeout` 仅一次触发，其余两槽分别未完成进程启动或未到达等待点，因此 suite 1.2 live 基线仍不完整。完整逐场景数据与证据见 `docs/evaluation/baselines/v0.52/README.md` 和该 run 的 `report.json`。
- 修正验收：全量 pytest 749 passed；suite 1.2 预检通过，离线矩阵 50/50 通过，原始结果 `/private/tmp/mini-agent-reliability-v052-suite12/`。沙箱独立尝试的 8 个 loopback 注入错误保留。教程结构、README 与 diff 检查通过；事实检查仅缺少用户手动创建的 v0.52 tag。Suite 1.1 历史结果与 suite 1.2 新批次分开保留。

### 第四批 live 后修正（suite 1.3）

- `evaluation/reliability_worker.py`：相对进程目录按 trial workspace 解析，在参数 validator 中提前拒绝越界；包裹 handler 保留有界诊断且仍由 Executor 转换异常；首次运行和崩溃续跑共用评测提示词，正式 resume 指令发现不变。
- 新增 `evaluation/reliability_diagnostics.py`：记录逐请求上下文分类估算、schema 开销、provider 用量来源、输出上限、跨阶段累计预算和拒绝原因，不保存正文；原有预算与完成条件不变。
- `reliability_schema.py`、`reliability.py` 与冻结 fixture 升为 1.3，保留旧版本只读报告兼容；过程任务公开 cwd 语义，新摘要见基线说明。
- `tests/test_evaluation_worker_v052.py` 覆盖四种合法 cwd、非法 cwd 准入前拒绝、实际派生会话的新计划与验证；新增 `tests/test_evaluation_diagnostics_v052.py` 覆盖数字诊断、缺失用量、限量、路径和两阶段提示词一致性。
- 同步评测说明、基线说明、手册、教程、中英文 README 和 CHANGELOG；本次不运行付费 live，不修改旧 trial，不代用户操作 tag。
- 当前源码指纹对应的完整离线矩阵 50/50 通过；运行 ID `504b0c57-db4d-4bc9-8436-a12cffe7380c`，目录 `/private/tmp/mini-agent-reliability-v052-suite13-final/`。
- 验证：本轮 worker/diagnostics/schema/recovery 定点回归 59 passed，预检通过；全量两次均为 758 passed、1 failed，唯一失败为旧 `test_cleanup_cannot_succeed_when_wait_does_not_confirm_exit` 的清理阶段遇到 `PermissionError: [Errno 1] Operation not permitted`，沙箱外单独重跑也复现。具体系统拒绝原因未确认。`tests/test_process_management_v026.py` 保留全部失败断言，在 mock wait 后的收尾阶段增加真实子进程 3 秒有界等待；单项回归通过，生产进程清理条件不变。教程结构、README 与 diff 检查通过；事实检查仍缺少用户手动创建的 v0.52 tag。

- 最终验收：全量 pytest 759 passed，含进程测试的定点回归 77 passed；预检与当前源码离线矩阵 50/50 通过，历史报告重建保持原成绩。前述测试失败作为调查记录保留。
- Suite 1.3 首次 live 批次 `docs/evaluation/baselines/v0.52/live-20260929-05/` 完成 21/21 槽位，故障触发 20 次、不变量通过 18 项/失败 2 项/不完整 1 项，grader 通过 14 项，恢复成功 8/17；基础设施错误、未运行、不可读、清理失败和人工复核项均为 0。三次 `crash-after-handler` 均守住边界，但恢复阶段都耗尽共享 token 预算；`process-wait-timeout` 触发 2/3，唯一未触发槽在调用进程工具前耗尽预算。HTTP 400 未重现；不自动补跑或替换。Suite 1.3 live 基线因此仍未完成，完整报告见该批 `report.json`。

### 第五批 live 后预算修正（suite 1.4）

- `evaluation/reliability_diagnostics.py`：用标准库 Fraction 做校准与向上取整，初始 2 倍输入估算加 256 余量，只根据本 trial 的 provider 用量向上校准（额外 10%）；一次性预留输入和输出，记录分层拒绝原因。
- `evaluation/reliability_worker.py`：首次运行与续跑使用同一准入/输出上限，续跑继承前阶段真实用量与校准；超额响应的工具逐项回灌预算错误且不准入 handler，纯文本也不能完成。生产 Runtime、权限和完成条件不放宽。
- `reliability_schema.py`、`reliability.py` 与 18 个冻结场景升为 1.4，在 suite/预检公开并校验 request_budget_policy，重算全部摘要；旧 1.0～1.3 报告继续只读加载。
- `tests/test_evaluation_diagnostics_v052.py` 覆盖临界余额、已超额/已耗尽、输入低估、未知用量和跨阶段校准；`tests/test_evaluation_worker_v052.py` 覆盖完整崩溃续跑继承校准及响应超额后的工具/文本阻断。
- 预算修正和离线验收阶段没有增加预算、补跑或修改历史 live，也没有执行 tag 操作。保守估算不等于精确 tokenizer；上下文效率和执行顺序仍需后续处理。
- 验证：当前源码的离线矩阵 50/50 通过，运行 ID `cd7f12b3-7ffc-408b-8d27-e0e54b7e6b41`，目录 `/private/tmp/mini-agent-reliability-v052-suite14-final/`；定点回归 67 passed，全量 pytest 767 passed；套件预检及历史 1.1/1.2/1.3 报告重建通过（仍为 5/10、5/16、8/17）。教程结构、README、diff 检查通过，教程事实检查仅缺少用户手动创建的 v0.52 tag。沙箱内全量的 26 项回环端口失败和离线的 8 项注入错误单独保留，未替换槽位。
- Suite 1.4 live 批次 `docs/evaluation/baselines/v0.52/live-20260929-06/` 完成 21/21，21 次故障均触发；不变量 19 通过、2 失败，grader 15 通过，恢复成功 1/18（权限拒绝是边界专用样本）。没有基础设施错误、证据不完整、未运行、清理失败或人工复核项；来源一致。七个场景均有 3 个已触发且可评分样本，达到冻结覆盖门槛。
- 预算观察：144 个请求决策中 128 个实际发送并获得 provider 响应，16 个在发送前因 `insufficient_request_budget` 拒绝，`planned_output_limit=0`，未继续工具回合；128 个实际输入都高于原始估算，但无一个超过校准预留，输出也均未超出额度，记录预算超额为 0。三次崩溃恢复继承约 22k 前阶段累计 tokens 和校准状态。由于没有 provider 返回超额响应，响应后阻止工具/完成的分支未由本批 live 样本触发。拒绝导致 16 个 trial 以 `token_limit` 收束，显示保守准入与重复上下文/执行顺序问题仍需进一步处理；估算不构成任意 provider 请求绝不超额的保证。原始报告为该批 `report.json`，未重试、补跑或替换。

### 第六批 live 后系统修正（suite 1.5 候选）

本轮按已批准计划实施正式父任务预算、请求上下文视图与完成收尾，不提高 live 的 64,000 token 上限。Suite 1.5 摘要为 `833eefa4cb6e108439fb407c0981383fcd8b04724887e42c40ba9bcd65baa3a7`；随后按用户指示运行了新的独立 live，suite 1.4 的历史 1/18 成绩保持不变。

| 文件 | 已实施改动 |
|---|---|
| `src/mini_agent/budget.py` | 标准库 `TaskBudgetController`；主请求、摘要和收尾分别按冻结 binding/schema 校准；预留、真实结算、未知用量全额预留计费与 request ID 幂等。 |
| `src/mini_agent/config.py`、`config_example.py`、`agent.py` | 新增可选 `PARENT_TASK_TOKEN_BUDGET=None`；CLI 默认关闭，新任务可启用，已保存的上限优先。 |
| `src/mini_agent/state.py`、`session.py` | State 内可选预算账本参与会话完整性与原子提交；请求前与结算后提交失败即停止；未知 pending 请求恢复时只计费一次。纯查询完成阻塞项用于提醒和收尾检查。 |
| `src/mini_agent/runtime.py` | 通用累计预算准入、额度内输出、同 binding 摘要结算、收尾预留；超额后不执行 handler、不完成任务，工具逐项回灌错误。修复无持久化后台启动重复准入留下 pending attempt 的缺陷。 |
| `src/mini_agent/context.py`、`prompt.py` | 预算下保留最近两轮完整工具协议、原样保留入选推理字段和所有用户消息；原 history 不变。保护内容无法容纳时明确停止；已验证且无阻塞时使用收尾视图并省略工具 schema。任务有精确路径时先读取。 |
| `evaluation/reliability_worker.py`、`reliability_diagnostics.py` | Live 与崩溃恢复接入同一 Runtime 控制器；诊断只观察正式准入。工具次数、轮数、时间与权限约束保留。 |
| `evaluation/reliability_schema.py`、`reliability.py`、18 个 `scenario.json` 与 `suite.json` | 冻结 1.5 预算合同并重算摘要；旧 1.0～1.4 仍只读重建。 |
| `evaluation/reliability_report.py`、`cost_replay.py` | 新证据分别报告文件正确、当前代验证、模型完成三层；严格恢复成功率仍为主指标。旧证据没有新增字段时报告内容不变。 |
| `tests/test_task_budget_v052.py`、worker/diagnostics 测试 | 新增成本、校准、持久化失败、未知计费、完整恢复与收尾、协议裁剪和旧会话兼容回归。 |

校准在每个请求类别及模型/工具 schema 摘要下独立进行：少于两次纯 provider 观测时，输入预留为 `2 × raw + 256`；之后使用 `ceil((raw + 最近八次最大的正估算误差) × 1.10) + 256`。主执行最多输出 1024、摘要 512、收尾 512 tokens，仍受较小模型配置上限约束。输出至少 256（较小模型上限除外）才准入。按未完成步骤与验证义务预留收尾轮数；这不是自动推进计划或自动完成。

历史数字回放记录在[成本回放](../evaluation/baselines/v0.52/budget-repair-cost-replay.json)：144 个观察决策、16 个原拒绝、885,685 输入 / 44,417 输出 tokens。保持旧请求和旧余额，仅应用新预留规则，130 个决策可准入，已观察输入均不超过新预留；6 个历史输出超过新的输出额度。此回放不模拟裁剪后的上下文、未来模型回复或恢复成功，不能用于宣称恢复率提升。

实现阶段没有启动 live、不替换槽位、不操作 tag；修复验收阶段以离线回归、50 槽边界矩阵、预检、历史报告重建和文档检查为依据。之后收到明确运行指示，已单独执行 suite 1.5 live；该批未重试、补跑或替换任何槽位。

修复验收：完整测试 812 passed，定点回归 95 passed；最终源码离线矩阵 50/50 全触发、不变量全通过，运行 ID `831bf1bc-14b9-4886-98e9-344ea8ef4c1b`。教程结构、README 与 diff 检查通过；教程事实检查仅因缺少需用户手动创建的 v0.52 tag 未通过。此前 799 passed / 72 passed 是上一个候选的验收，不覆盖本轮。修复验收摘要见[stage-budget-repair-validation.json](../evaluation/baselines/v0.52/stage-budget-repair-validation.json)。

新 live 批次[`live-20260929-07/`](../evaluation/baselines/v0.52/live-20260929-07/)（run ID `45e3d375-d69f-4318-80b7-bf284c078def`）共计划 21 槽；19 个有可读 trial，另 2 个在 provider 响应调用校验时报 `provider_protocol_error`。错误记录没有 provider status 或 detail，根因未知，不据此归因 HTTP 400。17 个已触发且可评分 trial 的不变量均通过；权限边界专用 trial 不进入恢复分母，严格恢复成功 5/14（35.7%）。分层结果为 16 个任务 trial、10 个最终文件正确、5 个当前 generation 验证通过、5 个模型正常完成；5 个都同时满足三个条件。

按场景看，`verification-repair` 为 3/3 成功，`mcp-disconnect` 为 2/3，`tool-handler-exception` 为 0/2，`process-wait-timeout` 为 0/2，`crash-after-handler` 为 0/1，`subagent-timeout` 为 0/3。两个协议错误分别落在工具异常与 handler 后崩溃场景；进程等待场景另有一次未触发。三个场景没有足够的已触发可评分 trial，suite 1.5 live 基线保持未完成。

130 个请求决策中 117 个获得 provider 响应，11 个因受保护上下文无法放入剩余任务预算而拒绝，另 2 个是上述 provider 协议错误。实际输入有 114/117 次高于原始估算，但没有输入超过校准预留、没有输出超过额度，记录预算超额为 0。live 报告由冻结清单、账本和原始 trial 重建后与归档文件字节一致；证据见批次目录中的 `report.json`、`suite-run.json`、`review-plan.json` 和 `trials/`。

随后按修复记录建议重新预检，并分别启动新的 suite 1.5 live 批次。[`live-20260929-08/`](../evaluation/baselines/v0.52/live-20260929-08/)的 21 槽全部为 `provider_http_503`，没有进入恢复评分。provider 恢复后运行的[`live-20260929-09/`](../evaluation/baselines/v0.52/live-20260929-09/)有 20 个 `provider_connection_error` 和 1 个在故障注入前因 token limit 停止的 trial；3 个验证修复 trial 虽有注入命中记录，但对应模型请求失败，仍按基础设施错误排除。全部 21 个 trial 清理完成，两批的恢复分母均为 0，原始报告均通过字节一致重建。没有在任一批次中重试、补跑或替换槽位；新的恢复率仍未知，suite 1.5 基线保持未完成。

## 1. 目标与定位

前十三阶段回答了“Agent 能做什么、如何约束它”。最后一个阶段回答：**给它同一批真实任务，它能独立完成多少，失败后能恢复多少，新增能力是否改善结果，代价是多少？**

本阶段交付可重复运行的任务集、执行器、独立验收、结构化结果和比较报告。编码能力评测必须真实启动对应版本的 Agent，让它调用模型和工具、在隔离工作区修改代码；现有单元测试只能证明实现边界，不能代替任务成功率。离线 fixture 用于验证评测器本身和注入可控故障，不能冒充真实模型能力结果。

目标流程：

```text
冻结 testcase + 初始仓库 + 运行配置
→ 为一次 trial 创建隔离工作区
→ 启动真实 Agent，记录运行事实
→ 在 Agent 停止后运行独立验收
→ 保存结果、失败原因和可复核证据
→ 对同条件多次 trial 及不同配置做比较
```

阶段十四以评测和缺陷修复收口，不因评测发现能力缺口而默认增加 Sandbox、GUI、插件市场或多层 Agent Teams。

## 2. 范围与非目标

### 2.1 本阶段范围

- `v0.50`：定义 testcase、runner、trial result、指标和离线自测；真实运行路径从第一版就存在。
- `v0.51`：建立小而有代表性的编码任务集，覆盖修 bug、局部改动、多文件改动和独立测试验证；再按难度扩展。
- `v0.52`：用可控故障与恢复场景衡量权限、工具、进程、持久化、MCP 和子代理的可靠性。
- `v0.53`：固定任务与条件，比较版本、模型/provider，以及 Memory、Skill、Subagent 的有无；发布可复核的汇总报告。
- 核心指标：任务成功率、独立验证通过率、工具调用数、token/成本、耗时、故障恢复成功率、无效重复次数。每项必须有固定分母、计算规则与缺失值语义。

### 2.2 本阶段不做

- 不把 Agent 的最终文本、`done` 状态或自行运行的验证当作独立成功判定。
- 不为跑分放宽 PermissionGate、Plan、持久提交、恢复或子代理权限；评测配置只能选择既有受支持能力。
- 不让 live 模型调用进入默认离线测试或普通 CI；真实评测需显式启动并受预算控制。
- 不以单次成功展示宣称稳定提升；不同初始状态、权限或验收条件的运行不能直接合并比较。
- 不把模型费用估算写成已发生的精确账单，也不在结果中存储 API key、真实 endpoint 或认证头。

## 3. 先冻结的评测决策

### D1：Testcase 是可审阅的任务合同

每个 testcase 有稳定 ID、版本、用户任务、固定初始代码版本或 fixture、允许的工作区与运行预算、独立验收步骤、预期可观察行为和难度标签。验收规则在运行前冻结，不能看到某次 Agent 输出后临时改题。题目正文不给出隐藏验收脚本的答案；公开测试可作为任务材料，隐藏或保留测试只由评测器在结束后运行。

任务集应包含正例和有诊断价值的反例，例如需求不完整、权限拒绝、验证失败。任务需要能在同一初始状态上重置重跑；提交到仓库的 fixture 不含凭据、个人数据或必须联网的外部服务。真实项目任务若无法固定依赖或测试环境，先标为探索样本，不进入主成功率。

### D2：Runner 启动真实 Agent，评分器独立

一次 trial 从干净的隔离工作区启动一个真实 Agent 进程或等价的公开入口，使用真实 model binding 和既有工具、权限、预算边界。隔离工作区的建立与清理只针对 runner 自己创建的路径，不改变开发者当前工作树。评测器记录启动配置摘要、进程退出状态、Agent 终态和资源消耗，并在停止后检查改动与运行验收。

评分器在 Agent 进程之外运行，使用预先冻结的断言与测试。Agent 自行执行的 `purpose=verification` 是运行事实；独立评分的测试是评测事实，两者分别记录。若 Agent 超时、越界、被拒绝、异常退出或没有产出可评分结果，trial 仍要落盘为明确状态，不能从分母中消失。评测器自身故障单列为 `infrastructure_error`，修复后重跑，不算 Agent 成败。

### D3：隔离和权限是评测条件的一部分

每次运行有独立工作区、会话/Memory 路径及临时产物；默认不继承开发者的私有 Memory、Skills、References、MCP 配置和宽泛授权。需要某项能力的 testcase 显式提供经过检查的 fixture 与配置，并保存无凭据摘要。所有副作用工具仍由原 PermissionGate 决定；无人值守评测使用预先定义、限定范围的策略，缺少授权时记录拒绝，不自动回答 `allow`。

模型调用可依赖本地 `config_local.py` 的真实凭据，但结果文件只能存 profile/provider/protocol/fingerprint 等非敏感来源摘要。真实调用、模型用量和外部服务可能变化；离线 harness 测试不接触真实 API。故障注入只作用于 runner 创建的子进程、工作区或测试 Server，不中断开发者进程和真实外部服务。

### D4：结果既能汇总，也能解释失败

结果至少保存 suite/testcase 版本、代码修订、运行配置摘要、随机种子或重复序号、预算、开始/结束时间、Agent 终态、独立验收结果、分类失败原因、父子模型与工具用量，以及可定位的日志/状态证据路径。保留原始 trial 与汇总之间的引用，聚合报告不能覆盖单次事实。

统一指标口径：

| 指标 | 口径 |
|---|---|
| 成功率 | 独立验收全通过的 trial / 可评分 trial；同时报告未运行及评测基础设施故障数 |
| 验证通过率 | 通过预先声明的独立测试/检查的 trial 占比；与 Agent 自行验证分开 |
| tool calls | 父 Agent 与子代理分别计数，再给总数；拒绝和失败调用也有明确计数 |
| token / cost | input/output 分开，标明 provider、estimated 或 mixed；成本依赖显式价格快照，缺价则留空 |
| latency | 从任务交给 Agent 到其终止的墙钟时间；验收耗时单列 |
| 恢复成功率 | 经过预定故障且完成规定恢复流程并通过验收的 trial / 可评分恢复 trial |
| 无效重复次数 | 同一进展阶段内，相同工具与规范化参数重复且未产生新事实的调用次数；规则固定并保留样本 |

平均值之外报告分布或分位数，并展示原始次数与分母。超时、权限拒绝、预算耗尽、模型错误、Agent 代码错误和未通过验收分开统计；不能只保留成功样本。现有 State/Trace 是运行事实来源，不反向修改其完成判定以适配评分。

### D5：比较时固定条件，处理随机性

同一比较组固定 testcase 版本、初始代码、验收器、预算、权限、工作区初始化和运行顺序规则；每个配置运行多次，并保留每次原始结果。只有被比较的因素不同，才把差异解释为该因素的效果。报告同时给出绝对成功数、率、耗时与用量，不只报百分比；样本过小时只作观察，不宣称统计显著。

版本比较优先使用不可变 commit ID；tag 可由用户手动建立，但 runner 不创建、移动或删除 tag。不同版本可能不支持同一能力或结果字段，需先定义共同任务子集和字段映射，不兼容项标为不可比较。模型/provider 对比记录实际绑定摘要；模型版本漂移或调用失败需要单列。Memory、Skill、Subagent 消融使用显式配置与相同任务条件，不能通过修改核心安全逻辑“关闭”功能。Memory 关闭组与开启组必须规定相同初始记忆快照，避免跨 trial 污染。

### D6：人工只审定标准和争议样本

用户在正式批量评测前审阅代表性任务与成功标准；每次 trial 由 runner 自动执行和评分。需求存在多种合理解法、测试通过但质量可疑或自动验收无法覆盖的样本，进入人工复核队列，报告其数量与决定。人工决定必须附理由并留在结果中，不静默改写原始自动评分。未经复核的主观质量维度只展示案例，不折算进主成功率。

## 4. 版本切片与实施任务

### 4.1 `v0.50`：Evaluation Harness

1. 定义有版本的 testcase、suite、trial、运行配置和结果 schema；约束 ID、路径、命令、预算与输出大小，配置解析失败在真实调用前拒绝。
2. 实现隔离工作区创建、真实 Agent 启动、超时/取消、有界清理、独立验收及结果原子保存。提供显式 live 入口和默认离线自测入口。
3. 从现有 Runtime/State/Trace/usage 提取结构化事实；统一计数和缺失值，不解析终端文案推断成功。报告器能从多个 trial 重建汇总。
4. 用固定模型响应与小型本地 fixture 验证 runner 正常、失败、超时、拒绝、评分器故障、清理和重复运行；再用一个小任务做真实 Agent 试跑，记录实际成本与耗时。

验收：同一 testcase 可从相同初始状态重复运行；每次都有独立结果；真实模型确实被调用，独立验收确实在 Agent 停止后执行；失败 trial 不丢失。

### 4.2 `v0.51`：Coding Benchmark

1. 先建立少量有明确验收的任务：单文件 bug、多文件行为修改、带回归测试的修复、需要先调查再改动的任务。每题写明用户视角的问题和可观察的成功标准。
2. 固定任务仓库/fixture、依赖和验收命令；验证验收器能拒绝原始错误版本，并接受人工制作的正确修复，避免“始终通过”的题目。
3. 运行 Agent 多次，保存 diff、独立测试结果、失败类别、用量和耗时；审查过易、含糊、依赖环境或泄漏答案的题目。
4. 形成首份基线报告：逐题结果与汇总并列，列出自动测试无法判断、需要人工复核的案例。

验收：任务集覆盖至少上述四类编码工作；原始错误状态不能直接过关；报告能够回答“哪些题稳定完成，失败通常发生在哪里”。

### 4.3 `v0.52`：Reliability Evaluation

1. 定义可控故障矩阵：工具异常和拒绝、验证失败后的修复、后台进程超时、持久提交边界崩溃、恢复 issue 处理、MCP 断连/错误、子代理超时/取消/中断。
2. 每个场景冻结注入点、预期不变量和成功条件。崩溃后的不确定调用不得重放；权限拒绝不得变成隐式批准；子结果不得代替父验证。
3. 离线 fixture 精确触发故障并验证评测器识别；需要评价 Agent 决策与恢复能力的场景再运行真实 Agent。区分协议/运行时不变量测试与模型驱动恢复成功率。
4. 保存注入事实、原始 State/Trace 摘要、用户模拟反馈的来源、恢复步骤和最终独立验收；失败不得被重新运行自动抹去。

验收：每类关键边界至少有一个可复现案例；报告区分“系统正确拒绝危险动作”和“Agent 最终完成任务”，故障恢复成功率有清楚分母。

### 4.4 `v0.53`：Regression & Comparison

1. 固定用于比较的 suite 与共同任务子集，选择可复现的旧版/当前版 commit、模型/provider 配置及能力消融组合；先检查各组运行条件是否可比。
2. 按预先声明的重复次数运行并汇总成功、验证、调用、token、成本、延迟、恢复和重复指标；报告缺失用量与未完成 trial。
3. 对显著退步或意外提升抽查原始轨迹和改动，识别评分器问题、环境漂移及真实行为变化；修复缺陷后保留修复前报告，并在相同条件下重跑。
4. 发布可重跑的命令、配置模板、任务版本、结果 schema、原始 trial 引用和阶段结论；逐项说明哪些能力收益有数据支持、哪些尚无证据。

验收：至少完成旧版/当前版一组对比和一组 Memory、Skill 或 Subagent 消融；读者能定位每个汇总数字的 trial 与验收证据，并复现离线部分。

## 5. 文件与边界映射

| 位置 | 预计变化 | 责任 |
|---|---|---|
| `src/mini_agent/evaluation/` 或独立 `scripts/evaluation/` | 新增 | testcase、runner、评分器、结果、汇总；实施时选择最少侵入的入口 |
| `tests/fixtures/`、`tests/` | 新增 | 离线任务、模型/故障 fixture 与 harness 自测 |
| `docs/evaluation/` | 新增 | suite 说明、运行方法、指标口径、报告和复核记录 |
| `src/mini_agent/runtime.py`、`state.py`、`trace.py` | 原则上只读；必要时定点补结构化导出 | 不为跑分改变 Agent 决策或安全门槛 |
| `docs/tutorials/`、操作手册、`README.md`、`CHANGELOG.md` | 随实际版本更新 | 教学、使用与版本导航；遵守对应写作规范 |
| `AGENTS.md` | 仅运行时硬约束变化时更新 | 评测约定优先放在本计划和评测文档 |

## 6. 横向验收与交付

1. 默认测试完全离线；真实 Agent 评测由显式入口启动，并显示任务数、调用预算和预估上限。凭据只读本地配置，不写入 testcase 或结果。
2. 每个 trial 的工作区、会话、Memory 与测试 Server 互不污染；失败和中断后有界清理，未完成清理需报告路径或进程身份，不能标为成功。
3. 评分器独立于 Agent 的 `done`、自述和自身验证；评分标准先冻结后运行，任何人工覆核都有记录。
4. 报告保留所有有效 trial、未运行、超时、基础设施错误及缺失指标；跨版本或消融对比标明共同任务与配置差异。
5. 使用真实 Agent 的结果不得由离线模拟结果替代；离线故障测试与真实模型表现分开陈列。
6. 核心运行时继续只依赖标准库；评测外围如采用可选依赖，必须有标准库回退且不成为默认安装或核心依赖。
7. 每版完成后运行相关测试；交付前运行 `PYTHONPATH=src python -m pytest -q`、`PYTHONPATH=src python scripts/check_tutorials.py`、`PYTHONPATH=src python scripts/check_readme.py`。若真实 API 不可用，明确记录未运行的 live 验收，不能称阶段已完成。
8. 若结果显示退步，优先提交可复现 testcase 和最小修复；不因追求分数牺牲权限、恢复和协议完整性。Git tag 只能由用户手动操作。

## 7. 阶段级端到端场景

1. 同一 bug 修复题在干净工作区跑三次；Agent 真实调用模型、修改文件并终止，评分器独立运行测试，生成三条可追溯 trial 与汇总。
2. Agent 宣称完成且自行验证通过，但隐藏行为测试失败；最终评分为失败，并保留两种验证事实。
3. 一次模型调用失败、一次权限拒绝、一次 Agent 超时分别落成不同失败类别；基础设施测试命令失效不计作 Agent 失败。
4. 在持久工具边界注入崩溃，派生恢复会话并处理 issue；不确定调用不自动重放，恢复后的独立测试决定最终结果。
5. MCP 测试 Server 断连、子代理超时或结果未领取时，评测能说明安全边界是否守住以及任务是否最终完成。
6. 对同一 suite 比较两版代码及 Memory 开关；各组初始状态、权限与预算一致，报告逐题原始结果和汇总，不把一次波动写成确定提升。

## 8. 完成定义与项目收官

- [x] `v0.50` 有可离线自测、可显式 live 运行的统一 harness，结果 schema 与评分边界稳定。
- [ ] `v0.51` 有冻结且可重跑的编码任务集，真实 Agent 基线和独立验收报告可复核。
- [ ] `v0.52` 有可控故障矩阵，明确区分运行时安全不变量与 Agent 恢复能力。
- [ ] `v0.53` 的回归比较与 Memory 检索消融实现和离线验收完成；36 槽 live 对比仍待用户审阅清单并明确启动，完成数据报告后才能勾选。
- [ ] 用户审定代表性任务与成功标准；争议样本人工复核有记录，无需逐次手工验收。
- [ ] 文档、教程、变更记录与三项完整验证完成；live 评测实际执行，未执行项明确列为未完成。

满足以上条件后，项目以“实现能力—真实运行—独立验收—回归比较”的证据链收官。后续只按发现的缺陷和具体用户需求维护，不预设新的大型能力阶段。

## v0.53 实施与验收记录

实现细节与合同见[本版计划](v053-regression-comparison-plan.md)。比较结果使用独立 schema 1，不扩展编码题集和可靠性格式。固定三组为 v0.52 关闭 Memory 检索、v0.53 关闭检索、v0.53 开启检索；四题每组重复三次，共 36 槽。旧来源 commit 固定为 `9d498f91f7ca21caa7f4f842fba0a49075754f20`。完整 v0.53 commit 和同一模型别名只写入生成的审阅清单；价格缺项使用 `null`，不猜测账单。

- 新增 ComparisonSpec / ComparisonRun / ComparisonTrial、目标版本归档与 Runtime 接口预检、隔离 worker、顺序账本、独立 grader 共享入口、指标与报告重建 CLI。
- 四份 Memory 种子只包含接口、背景和调查方法；trial 使用目标版本 MemoryStore 和 MemoryRetriever，workspace、HOME、Memory 与临时目录彼此隔离。证据不保存注入正文。
- 固定响应自测运行实际目标版本 AgentRuntime，不请求 provider；另覆盖失败工具、权限拒绝、无效重复、证据损坏和旧 runner/benchmark 兼容性。
- Live 比较状态：未运行、36 槽全部未启动。本计划及离线验收不授权付费调用；待生成具体审阅清单后由用户审阅并明确启动。
- v0.51 `coding-benchmark@1.1` live 基线、v0.52 `reliability-boundaries@1.6` live 基线继续各自未完成；阶段十四整体不能因 v0.53 离线交付而标为完成。
