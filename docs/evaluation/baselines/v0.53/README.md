# v0.53 比较基线

## 正式 live 批次

**运行状态：36/36 槽位均有终态；能力比较结论不成立，v0.53 live 验收仍未完成。** 批次 `67583764-8595-4c68-ad4d-8f2254f23bdf` 于 2026-10-08 按审阅清单运行，冻结的当前版本源码为 `807b95bf7b1e51cc6d622a8a519b447c77c7fbb5`。原始账本、比较合同、计划、每个 trial 及重建报告归档在 [`live-20261008-01/`](live-20261008-01/)。

每组 12 个 trial 都只发起 1 次模型请求，36 次均以 `ProviderConnectionError` 结束；成功响应和工具调用均为 0。独立 grader 对每个未修改的 trial workspace 都运行并返回未通过，清理结果为 36/36 完整。由于 Agent 未收到任何模型响应，grader 的 0/36 不能解释为版本能力退步或 Memory 无收益。已保存证据只含脱敏错误类别，没有底层连接异常详情；不据此推断具体网络或 provider 原因。

结构性批次状态是 `completed`，36/36 槽位均保留，未重试、补跑或替换。两条边的冻结条件检查和配对覆盖均通过，各覆盖 12 对；这只表示合同条件及槽位配对完整，不表示有可解释的能力样本。Agent token 来源均为 `estimated`，没有成功响应的 provider 用量；价格快照缺价，成本为 `null`。这些 token 和耗时不能当作实际账单或正常模型运行代价。Memory 开启组在 12 个 trial 中均发生一次摘要检索，但没有模型响应，无法评价记忆收益。

正式报告可从归档输入重建：[`report.json`](live-20261008-01/report.json) 与 [`report.md`](live-20261008-01/report.md)。本次报告记录的是评测链路和 provider 失败事实，不构成版本回归或 Memory 消融结论。若要获得能力比较结果，应先查明并修复连接失败，再冻结新的运行清单和 run ID；本批次不续跑，也不替换槽位。

## 2026-10-08 评分与诊断修正

原归档报告错误地把正常返回的 grader 布尔结果直接视为能力评分资格，导致全批连接失败时仍标记 `comparison_complete=true`、比较边可比较和变化为 0。原报告及 trial、账本全部保留，不能继续用原报告的这些字段解释能力。

[修正版 Markdown](live-20261008-01-report-corrected/report.md) 与 [JSON](live-20261008-01-report-corrected/report.json) 使用报告规则 2，引用原 run ID 和四份输入文件摘要。36/36 槽位均有终态，`batch_complete=true`；36 个基础设施失败，有效能力样本与两条边有效配对均为 0，`comparison_complete=false`，能力通过率和差异为 `null`。grader 的原始 0/36 失败事实继续展示。原归档 150 个文件在生成修正版前后逐项 SHA-256 一致，逐文件摘要见 [归档核对记录](live-20261008-01-report-corrected/archive-verification.json)。

比较 worker 新增有界、白名单的异常原因链诊断，只保留错误类别、已识别的底层异常类别、整数 errno 和 TLS 校验码。旧结果没有该字段，不能补造连接原因。本轮没有网络探针或模型请求，也未修复或验证连接根因；新 live 仍需重新冻结并明确启动。

## 离线记录

固定响应自测目录 `/private/tmp/mini-agent-v053-comparison-offline-20261008-03/` 不是 live 样本，也不计入能力成功率：36/36 fixture trial 有终态，独立 grader 36/36 通过，两条比较边通过条件校验；Agent 严格任务成功为 0/36，因为固定响应触发 `blocked`。失败、拒绝和重复边界探针分别观测到工具失败 1 次、权限拒绝 1 次和无效重复 1 次。

此前诊断运行 `/private/tmp/mini-agent-v053-comparison-offline-20261008-02/` 保留为独立失败记录：要求测试文件的题目缺少空 `tests/` 目录，缓存回归题 grader 未通过。修复 runner 后在新目录重跑了完整 fixture 自测；诊断结果未混入正式 live 批次。

记忆材料只包含背景、接口约定和调查方法。每个 trial 重建独立 MemoryStore；证据不保存注入正文。比较只适用于冻结的 `coding-benchmark@1.1` 四题、模型绑定和自动摘要检索，不代表跨任务记忆学习或其他能力。三次重复仅为描述性观察，不宣称统计显著。

阶段十四整体仍未完成：v0.51 `coding-benchmark@1.1` live 基线和 v0.52 `reliability-boundaries@1.6` live 基线继续分别单列为未完成。

修复验收：定向比较回归 37 passed；完整离线 pytest 871 passed。教程结构、README 和 `git diff --check` 通过；教程事实检查仅缺少用户手动创建的 `v0.53` tag。首次沙箱内全量为 26 failed、841 passed、3 skipped，失败来自回环端口绑定限制；获准在沙箱外运行的最终全量全部通过。未发送付费模型请求。
