<div align="center">

# coding-agent-from-scratch

### 逐步生长的编程 Agent：使用 Python 从零开始构建一个能干活的 AI Agent

从最小的 agent loop 开始，按 Git tag 和能力阶段逐步加入工具、安全、上下文和可靠执行能力。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/) [![Dependencies](https://img.shields.io/badge/core%20dependencies-zero-green)](#快速开始) [![Versions](https://img.shields.io/badge/versions-v0.01%E2%86%92ongoing-orange)](#学习路径) [![License](https://img.shields.io/badge/license-MIT-lightgrey)](./LICENSE)

**[English](./README_EN.md)** · **中文**

</div>

---

适合想用 Python 标准库理解 LLM agent 如何运行的开发者。每课聚焦一个版本相对上一版新增的核心概念，源码、diff 和设计取舍都可追溯。

**当前状态**：主线版本为 `v0.53`，最新课程为[第 53 课：回归比较与能力收益验证](./docs/tutorials/53-regression-comparison.md)。离线自测已完成，真实模型评测尚未完成；详情见[评测状态](./docs/evaluation/baselines/v0.53/README.md)和[阶段十四计划](./docs/plans/evaluation-regression-plan.md)。教程以默认分支 `docs/tutorials/` 为准；运行某课时再切换该课声明的代码 tag。

快速入口：[运行](#快速开始) · [学习路径](#学习路径) · [学习指南](./docs/tutorials/README.md) · [完整手册](./docs/operation/manual.md)

## 快速开始

要求：Python 3.10+、Git，以及支持 OpenAI Chat Completions 协议的模型服务地址、API 密钥和模型名称。以下命令适用于 Bash/zsh。

**1. 获取代码并安装。**

```bash
git clone https://github.com/liiiiiiiiil/agent-from-scratch.git coding-agent-from-scratch
cd coding-agent-from-scratch
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

**2. 配置一个模型服务。**

新建 `src/mini_agent/config_local.py`，写入下面的最小配置，并替换前三项占位值。该文件已被 Git 忽略，真实配置只保存在本地。

```python
BASE_URL = "https://gateway.example.invalid/v1"
API_KEY = "YOUR_API_KEY"
MODEL = "YOUR_MODEL_NAME"
MCP_SERVERS = []
```

`BASE_URL` 填服务的 API 基础地址（通常以 `/v1` 结尾），`API_KEY` 填该服务的密钥，`MODEL` 填服务支持的模型名称。首次体验不需要 MCP（连接外部工具的协议），保持 `MCP_SERVERS = []` 即可；若改用 `config_example.py` 模板，也请先关闭其中的占位 MCP 服务。多模型服务、Anthropic 协议和 MCP 配置见[完整手册](./docs/operation/manual.md)。

**3. 运行第一条任务。**

```bash
python -m mini_agent "帮我算一下 123 * 456"
```

可选：`python -m pip install -e '.[interactive]'` 启用多行终端输入。命令行参数只是首条任务，处理后仍进入交互循环；用空行、`exit`、`quit` 或 EOF 退出。PowerShell、免安装运行和配置细节见[完整手册](./docs/operation/manual.md)。

## 学习路径

<table width="100%">
  <thead>
    <tr><th>版本</th><th>主题</th><th>简介</th></tr>
  </thead>
  <tbody>
    <tr><th colspan="3"><a id="stage-1"></a>阶段一 · 理解 Agent Loop</th></tr>
    <tr><td><strong>v0.01</strong></td><td><a href="./docs/tutorials/01-minimal-loop.md">最简 agent loop</a></td><td>建立最小对话循环，理解请求、回复与结束条件。</td></tr>
    <tr><th colspan="3"><a id="stage-2"></a>阶段二 · 工具与安全</th></tr>
    <tr><td><strong>v0.02</strong></td><td><a href="./docs/tutorials/02-first-tool.md">第一个工具</a></td><td>接入计算工具，理解模型如何请求工具并使用执行结果。</td></tr>
    <tr><td><strong>v0.03</strong></td><td><a href="./docs/tutorials/03-file-tools.md">文件读写工具</a></td><td>让 Agent 读取和写入真实项目文件。</td></tr>
    <tr><td><strong>v0.04</strong></td><td><a href="./docs/tutorials/04-permission-gate.md">权限闸门</a></td><td>为工具调用加入允许、拒绝和询问三种授权方式。</td></tr>
    <tr><th colspan="3"><a id="stage-3"></a>阶段三 · Mini Agent 里程碑</th></tr>
    <tr><td><strong>v0.05</strong></td><td><a href="./docs/tutorials/05-streaming.md">流式输出</a></td><td>逐块接收并显示模型回复，改善等待时的交互体验。</td></tr>
    <tr><td><strong>v0.06</strong>（补丁 <code>v0.06.1</code>）</td><td><a href="./docs/tutorials/06-concurrent-tool-calls.md">并发 tool_calls</a></td><td>并发执行同一轮的多个独立工具调用，减少等待时间。</td></tr>
    <tr><td><strong>v0.07</strong></td><td><a href="./docs/tutorials/07-system-prompt.md">系统提示词工程化</a></td><td>分层组织身份、规则和环境信息，明确 Agent 的工作方式。</td></tr>
    <tr><td><strong>v0.08</strong></td><td><a href="./docs/tutorials/08-file-operations.md">文件操作补全</a></td><td>补齐目录浏览、内容搜索和精确编辑能力。</td></tr>
    <tr><td><strong>v0.09</strong></td><td><a href="./docs/tutorials/09-permission-upgrade.md">权限系统升级</a></td><td>按文件路径和命令内容细化工具权限。</td></tr>
    <tr><td><strong>v0.10</strong></td><td><a href="./docs/tutorials/10-shell-execution.md">shell 执行</a></td><td>让 Agent 执行命令和测试，并处理超时与过长输出。</td></tr>
    <tr><th colspan="3"><a id="stage-4"></a>阶段四 · 上下文管理</th></tr>
    <tr><td><strong>v0.11</strong></td><td><a href="./docs/tutorials/11-context-architecture.md">上下文架构</a></td><td>分离任务状态与对话上下文，避免裁剪历史时丢失执行事实。</td></tr>
    <tr><td><strong>v0.12</strong></td><td><a href="./docs/tutorials/12-token-budget-trimming.md">预算与裁剪</a></td><td>估算上下文用量，按完整对话轮次裁剪超长历史。</td></tr>
    <tr><td><strong>v0.13</strong>（补丁 <code>v0.13.1</code>、<code>v0.13.2</code>）</td><td><a href="./docs/tutorials/13-context-compaction.md">上下文压缩</a></td><td>用摘要压缩旧对话，在有限上下文中保留关键信息。</td></tr>
    <tr><th colspan="3"><a id="stage-5"></a>阶段五 · 项目感知与任务编排</th></tr>
    <tr><td><strong>v0.14</strong></td><td><a href="./docs/tutorials/14-project-instructions.md">项目级指令</a></td><td>读取项目中的 AGENTS.md，让 Agent 遵循项目规则。</td></tr>
    <tr><td><strong>v0.15</strong></td><td><a href="./docs/tutorials/15-task-state.md">任务清单与状态</a></td><td>用独立的任务清单记录计划和步骤进度。</td></tr>
    <tr><td><strong>v0.16</strong>（补丁 <code>v0.16.1</code>）</td><td><a href="./docs/tutorials/16-plan-driven-execution.md">计划驱动执行</a></td><td>把计划、执行和验证串成闭环，检查任务是否真正完成。</td></tr>
    <tr><th colspan="3"><a id="stage-6"></a>阶段六 · 可靠执行</th></tr>
    <tr><td><strong>v0.17</strong></td><td><a href="./docs/tutorials/17-failure-model.md">失败模型</a></td><td>记录工具执行失败的原因和过程，为诊断与恢复提供依据。</td></tr>
    <tr><td><strong>v0.18</strong>（补丁 <code>v0.18.1</code>）</td><td><a href="./docs/tutorials/18-recovery-policy.md">受限恢复策略</a></td><td>根据失败事实选择重试、调整或求助，并重新验证结果。</td></tr>
    <tr><td><strong>v0.19</strong></td><td><a href="./docs/tutorials/19-checkpoint-rollback.md">单文件检查点与回滚</a></td><td>保存单个文件修改前的内容，检查冲突后恢复原状。</td></tr>
    <tr><td><strong>v0.20</strong></td><td><a href="./docs/tutorials/20-repair-loop.md">修复循环</a></td><td>把失败诊断、受限恢复和独立验证组成有次数上限的修复循环。</td></tr>
    <tr><td><strong>v0.21</strong></td><td><a href="./docs/tutorials/21-trace-replay.md">任务轨迹回放</a></td><td>只读回放任务的执行、失败、恢复和验证过程。</td></tr>
    <tr><th colspan="3"><a id="stage-7"></a>阶段七 · 结构化计划</th></tr>
    <tr><td><strong>v0.22</strong></td><td><a href="./docs/tutorials/22-plan-contract.md">Plan Contract（结构化计划）</a></td><td>用结构化计划明确目标和步骤，分别记录计划修订与执行进度。</td></tr>
    <tr><td><strong>v0.23</strong></td><td><a href="./docs/tutorials/23-plan-mode-handoff.md">只读规划与用户交接</a></td><td>先只读调查，再提交计划供用户审批，获准后按权限执行。</td></tr>
    <tr><td><strong>v0.24</strong></td><td><a href="./docs/tutorials/24-replanning-policy.md">证据驱动重规划与停滞收口</a></td><td>根据新证据调整计划，并在重复操作无法推进时停止。</td></tr>
    <tr><td><strong>v0.25</strong></td><td><a href="./docs/tutorials/25-plan-trace-evaluation.md">计划轨迹回放与验收</a></td><td>串联计划变更与执行证据，回看任务如何推进并检查记录完整性。</td></tr>
    <tr><th colspan="3"><a id="stage-8"></a>阶段八 · 后台进程与任务边界</th></tr>
    <tr><td><strong>v0.26</strong></td><td><a href="./docs/tutorials/26-background-process-boundaries.md">后台进程启动与任务边界</a></td><td>启动后台长时间任务，让 Agent 继续工作并在退出时清理进程。</td></tr>
    <tr><td><strong>v0.27</strong></td><td><a href="./docs/tutorials/27-process-observation.md">观察后台进程</a></td><td>查询后台进程状态、读取新增输出，并等待运行结果。</td></tr>
    <tr><td><strong>v0.28</strong></td><td><a href="./docs/tutorials/28-process-control.md">控制后台进程</a></td><td>让 Agent 按权限终止后台进程，并确认进程已经退出。</td></tr>
    <tr><td><strong>v0.29</strong></td><td><a href="./docs/tutorials/29-interactive-process.md">驱动等待输入的后台进程</a></td><td>通过文本管道向后台进程发送输入，完成简单交互。</td></tr>
    <tr><th colspan="3"><a id="stage-9"></a>阶段九 · 会话持久化与安全交接</th></tr>
    <tr><td><strong>v0.30</strong></td><td><a href="./docs/tutorials/30-session-persistence.md">会话持久化与安全点</a></td><td>在安全时机保存任务状态和对话，为后续恢复留下可靠记录。</td></tr>
    <tr><td><strong>v0.31</strong></td><td><a href="./docs/tutorials/31-safe-resume.md">从完整安全点恢复会话</a></td><td>检查保存记录和工作区变化，在新进程中安全恢复会话。</td></tr>
    <tr><td><strong>v0.32</strong></td><td><a href="./docs/tutorials/32-durable-tool-boundaries.md">持久化工具执行边界</a></td><td>持久记录工具执行前后的状态，明确中断发生的位置。</td></tr>
    <tr><td><strong>v0.33</strong></td><td><a href="./docs/tutorials/33-crash-recovery.md">崩溃恢复与不确定副作用交接</a></td><td>区分崩溃后未执行和执行结果不明的调用，由用户决定后续处理。</td></tr>
    <tr><th colspan="3"><a id="stage-10"></a>阶段十 · 受控子代理委派</th></tr>
    <tr><td><strong>v0.34</strong></td><td><a href="./docs/tutorials/34-minimal-delegation.md">最小受控子代理委派</a></td><td>把明确的只读调查交给独立子代理，并收集调查报告。</td></tr>
    <tr><td><strong>v0.35</strong></td><td><a href="./docs/tutorials/35-shared-agent-runtime.md">共享父子运行循环</a></td><td>让父子 Agent 共用运行循环，同时保持各自的状态和权限独立。</td></tr>
    <tr><td><strong>v0.36</strong></td><td><a href="./docs/tutorials/36-multi-provider.md">多 provider 与统一协议适配</a></td><td>为父子 Agent 配置不同模型服务，并统一不同协议的调用方式。</td></tr>
    <tr><td><strong>v0.37</strong></td><td><a href="./docs/tutorials/37-subagent-lifecycle-budget.md">子代理生命周期与聚合预算</a></td><td>跟踪子代理的运行状态，管理总预算和取消请求。</td></tr>
    <tr><td><strong>v0.38</strong></td><td><a href="./docs/tutorials/38-parallel-delegation.md">有界并行子代理</a></td><td>限制并发数量，让多个只读子代理同时调查并有序返回结果。</td></tr>
    <tr><td><strong>v0.39</strong></td><td><a href="./docs/tutorials/39-durable-delegation.md">持久委派交付</a></td><td>先保存子代理报告，再可靠交付给父 Agent，避免中断后丢失结果。</td></tr>
    <tr><th colspan="3"><a id="stage-11"></a>阶段十一 · 工作区 Memory &amp; References</th></tr>
    <tr><td><strong>v0.40</strong></td><td><a href="./docs/tutorials/40-persistent-memory.md">轻量持久 Memory</a></td><td>让 Agent 显式保存、查阅、修订和删除工作区的长期记忆。</td></tr>
    <tr><td><strong>v0.41</strong></td><td><a href="./docs/tutorials/41-memory-retrieval.md">相关记忆检索</a></td><td>检索与当前任务相关的记忆，将少量摘要补入模型上下文。</td></tr>
    <tr><td><strong>v0.42</strong></td><td><a href="./docs/tutorials/42-local-references.md">具名本地 References</a></td><td>通过配置的名称搜索和读取工作区外的本地参考资料。</td></tr>
    <tr><th colspan="3"><a id="stage-12"></a>阶段十二 · MCP &amp; Skills</th></tr>
    <tr><td><strong>v0.43</strong></td><td><a href="./docs/tutorials/43-stdio-mcp-client.md">最小 stdio MCP 客户端</a></td><td>用独立客户端连接本地 MCP 服务，发现工具并经用户确认后调用。</td></tr>
    <tr><td><strong>v0.44</strong></td><td><a href="./docs/tutorials/44-mcp-tools-runtime.md">MCP Tool 接入父 Agent Runtime</a></td><td>把外部 MCP 工具接入 Agent，并沿用现有的权限和执行流程。</td></tr>
    <tr><td><strong>v0.45</strong></td><td><a href="./docs/tutorials/45-local-skills.md">本地 Skills 发现与按需加载</a></td><td>发现并按需加载本地技能说明，为 Agent 提供任务指导。</td></tr>
    <tr><td><strong>v0.46</strong></td><td><a href="./docs/tutorials/46-mcp-http-resources-prompts.md">受限 HTTP MCP、文本 Resource 与 Prompt</a></td><td>连接 HTTP MCP 服务，并由用户选择参考资料和预览提示模板。</td></tr>
    <tr><th colspan="3"><a id="stage-13"></a>阶段十三 · 轻量 Agent 协作</th></tr>
    <tr><td><strong>v0.47</strong></td><td><a href="./docs/tutorials/47-agent-profiles.md">具名子代理角色</a></td><td>为只读子代理配置角色，明确工具范围、模型和可用技能。</td></tr>
    <tr><td><strong>v0.48</strong></td><td><a href="./docs/tutorials/48-background-subagents.md">进程内后台子代理</a></td><td>让子代理在后台只读调查，父 Agent 继续工作并随后领取报告。</td></tr>
    <tr><td><strong>v0.49</strong></td><td><a href="./docs/tutorials/49-resumable-child-session.md">可续接子会话</a></td><td>续接已完成并领取结果的子调查，并支持保存和恢复子会话。</td></tr>
    <tr><th colspan="3"><a id="stage-14"></a>阶段十四 · Agent 任务评测</th></tr>
    <tr><td><strong>v0.50</strong></td><td><a href="./docs/tutorials/50-evaluation-harness.md">独立评测链路</a></td><td>在隔离工作区运行 Agent 任务，通过独立检查评分并保存过程记录。</td></tr>
    <tr><td><strong>v0.51</strong></td><td><a href="./docs/tutorials/51-coding-benchmark.md">重复运行编码基准</a></td><td>重复运行固定编码任务，通过独立测试评估 Agent 的稳定表现。</td></tr>
    <tr><td><strong>v0.52</strong></td><td><a href="./docs/tutorials/52-reliability-evaluation.md">故障注入与恢复评测</a></td><td>主动注入故障，分别检验 Agent 的安全边界和任务恢复能力。</td></tr>
    <tr><td><strong>v0.53</strong></td><td><a href="./docs/tutorials/53-regression-comparison.md">回归比较与能力收益验证</a></td><td>对比不同版本和记忆检索开关，判断是否出现退步或带来收益。</td></tr>
  </tbody>
</table>

完成 `v0.10` 后，Agent 已能读取项目、搜索和修改文件、执行命令、运行测试，并通过权限机制控制高风险操作。

## 项目结构

```text
src/mini_agent/   核心运行时、配置、状态、权限、上下文和工具
skills/           项目级本地 Skill 工作流说明
tests/             测试与 smoke test
docs/tutorials/    分阶段、按版本的教程
docs/operation/    最新版运行手册
docs/plans/        路线图与功能计划
docs/governance/   写作规范与决策记录
examples/          示例输入输出文件
```

## 设计与文档

- 核心 LLM 调用、agent loop、工具、权限和状态只用 Python 标准库；外围交互增强为可选依赖。
- 工具层处理 handler 错误并把结果回灌模型；核心 loop 保持清晰，具体约束见 [`AGENTS.md`](./AGENTS.md)。
- [学习指南](./docs/tutorials/README.md) · [完整手册](./docs/operation/manual.md) · [上下文架构](./docs/operation/context-architecture.md) · [路线图](./docs/plans/teaching-repo-plan.md) · [CHANGELOG](./CHANGELOG.md)

## 贡献

欢迎提交 Issue / PR。新增课程前请读[教程作者规范](./docs/governance/tutorial-authoring.md)、[主 README 编写规范](./docs/governance/readme-authoring.md)和 `AGENTS.md`；作者入口与模板见[学习指南](./docs/tutorials/README.md)。

## License

MIT — 见 [LICENSE](./LICENSE)

<!-- 关键词 / Keywords: agent tutorial, agent 教程, LLM agent, coding agent, Python agent, function calling, 从零构建 agent, AI agent, agent loop, tool calling -->
