<div align="center">

# coding-agent-from-scratch

### A coding agent that grows step by step

Build a working AI agent from scratch with the Python standard library, one concept and one Git snapshot at a time.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/) [![Dependencies](https://img.shields.io/badge/core%20dependencies-zero-green)](#quick-start) [![Versions](https://img.shields.io/badge/versions-v0.01%E2%86%92ongoing-orange)](#learning-path) [![License](https://img.shields.io/badge/license-MIT-lightgrey)](./LICENSE)

**English** · **[中文](./README.md)**

</div>

---

For developers who want to understand how an LLM agent runs without a framework. Each lesson focuses on one concept added since the previous version, with source, diffs, and design trade-offs kept traceable.

**Current status**: The mainline version is `v0.53`; the latest lesson covers [regression comparison and capability benefit validation](./docs/tutorials/53-regression-comparison.md). Offline self-tests are complete; evaluation with real models remains incomplete. See the [evaluation status](./docs/evaluation/baselines/v0.53/README.md) and [Stage 14 plan](./docs/plans/evaluation-regression-plan.md). Read the tutorials on the default branch under `docs/tutorials/`; check out the lesson's declared tag when running its code.

Quick links: [run](#quick-start) · [learning path](#learning-path) · [tutorial guide](./docs/tutorials/README.md) · [manual](./docs/operation/manual.md)

## Quick Start

Requirements: Python 3.10+, Git, and a model service supporting the OpenAI Chat Completions protocol, with its API address, API key, and model name. The following commands use Bash/zsh.

**1. Get the code and install.**

```bash
git clone https://github.com/liiiiiiiiil/agent-from-scratch.git coding-agent-from-scratch
cd coding-agent-from-scratch
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

**2. Configure one model service.**

Create `src/mini_agent/config_local.py` with this minimal configuration and replace the first three placeholder values. Git ignores this file; keep real configuration local.

```python
BASE_URL = "https://gateway.example.invalid/v1"
API_KEY = "YOUR_API_KEY"
MODEL = "YOUR_MODEL_NAME"
MCP_SERVERS = []
```

Set `BASE_URL` to the service's API base address (usually ending in `/v1`), `API_KEY` to its key, and `MODEL` to a supported model name. MCP, a protocol for connecting external tools, is optional; keep `MCP_SERVERS = []` for your first run. If you use the `config_example.py` template instead, disable its placeholder MCP service first. See the [manual](./docs/operation/manual.md) for multiple model services, the Anthropic protocol, and MCP configuration.

**3. Run your first task.**

```bash
python -m mini_agent "calculate 123 * 456"
```

Optional multiline input: `python -m pip install -e '.[interactive]'`. A command-line argument supplies the first task; the process then enters the interactive loop. Leave with an empty line, `exit`, `quit`, or EOF. See the [manual](./docs/operation/manual.md) for PowerShell, no-install usage, and configuration details.

## Learning Path

<table width="100%">
  <thead>
    <tr><th>Version</th><th>Topic</th><th>Overview</th></tr>
  </thead>
  <tbody>
    <tr><th colspan="3"><a id="stage-1"></a>Stage 1 · Understand the Agent Loop</th></tr>
    <tr><td><strong>v0.01</strong></td><td><a href="./docs/tutorials/01-minimal-loop.md">Minimal agent loop</a></td><td>Build a minimal conversation loop and understand requests, replies, and completion.</td></tr>
    <tr><th colspan="3"><a id="stage-2"></a>Stage 2 · Tools and Safety</th></tr>
    <tr><td><strong>v0.02</strong></td><td><a href="./docs/tutorials/02-first-tool.md">First tool</a></td><td>Add a calculator tool and learn how the model requests tools and uses their results.</td></tr>
    <tr><td><strong>v0.03</strong></td><td><a href="./docs/tutorials/03-file-tools.md">File read/write tools</a></td><td>Let the Agent read and write real project files.</td></tr>
    <tr><td><strong>v0.04</strong></td><td><a href="./docs/tutorials/04-permission-gate.md">Permission gate</a></td><td>Add allow, deny, and ask permission decisions to tool calls.</td></tr>
    <tr><th colspan="3"><a id="stage-3"></a>Stage 3 · Mini Agent Milestone</th></tr>
    <tr><td><strong>v0.05</strong></td><td><a href="./docs/tutorials/05-streaming.md">Streaming output</a></td><td>Display model replies as they arrive to improve the waiting experience.</td></tr>
    <tr><td><strong>v0.06</strong> (patch <code>v0.06.1</code>)</td><td><a href="./docs/tutorials/06-concurrent-tool-calls.md">Concurrent tool_calls</a></td><td>Run independent tool calls concurrently within a turn to reduce waiting.</td></tr>
    <tr><td><strong>v0.07</strong></td><td><a href="./docs/tutorials/07-system-prompt.md">System prompt engineering</a></td><td>Organize identity, rules, and environment details into a layered system prompt.</td></tr>
    <tr><td><strong>v0.08</strong></td><td><a href="./docs/tutorials/08-file-operations.md">File operations complete</a></td><td>Add directory browsing, content search, and precise file editing.</td></tr>
    <tr><td><strong>v0.09</strong></td><td><a href="./docs/tutorials/09-permission-upgrade.md">Permission system upgrade</a></td><td>Refine tool permissions by file path and command content.</td></tr>
    <tr><td><strong>v0.10</strong></td><td><a href="./docs/tutorials/10-shell-execution.md">Shell execution</a></td><td>Let the Agent run commands and tests while handling timeouts and lengthy output.</td></tr>
    <tr><th colspan="3"><a id="stage-4"></a>Stage 4 · Context Management</th></tr>
    <tr><td><strong>v0.11</strong></td><td><a href="./docs/tutorials/11-context-architecture.md">Context architecture</a></td><td>Separate task state from conversation context so trimming history preserves execution facts.</td></tr>
    <tr><td><strong>v0.12</strong></td><td><a href="./docs/tutorials/12-token-budget-trimming.md">Budget and trimming</a></td><td>Estimate context usage and trim long histories by complete conversation turns.</td></tr>
    <tr><td><strong>v0.13</strong> (patches <code>v0.13.1</code>, <code>v0.13.2</code>)</td><td><a href="./docs/tutorials/13-context-compaction.md">Context compaction</a></td><td>Summarize older conversations to retain key information within limited context.</td></tr>
    <tr><th colspan="3"><a id="stage-5"></a>Stage 5 · Project-Aware Task Orchestration</th></tr>
    <tr><td><strong>v0.14</strong></td><td><a href="./docs/tutorials/14-project-instructions.md">Project instructions</a></td><td>Read project AGENTS.md files so the Agent follows project rules.</td></tr>
    <tr><td><strong>v0.15</strong></td><td><a href="./docs/tutorials/15-task-state.md">Todo and task state</a></td><td>Track plans and step progress in a dedicated task list.</td></tr>
    <tr><td><strong>v0.16</strong> (patch <code>v0.16.1</code>)</td><td><a href="./docs/tutorials/16-plan-driven-execution.md">Plan-driven execution</a></td><td>Connect planning, execution, and verification to check whether a task is complete.</td></tr>
    <tr><th colspan="3"><a id="stage-6"></a>Stage 6 · Reliable Execution</th></tr>
    <tr><td><strong>v0.17</strong></td><td><a href="./docs/tutorials/17-failure-model.md">Failure model</a></td><td>Record tool failures and execution details to support diagnosis and recovery.</td></tr>
    <tr><td><strong>v0.18</strong> (patch <code>v0.18.1</code>)</td><td><a href="./docs/tutorials/18-recovery-policy.md">Recovery policy</a></td><td>Choose retries, adjustments, or user assistance based on failures, then verify again.</td></tr>
    <tr><td><strong>v0.19</strong></td><td><a href="./docs/tutorials/19-checkpoint-rollback.md">Checkpoint / Rollback</a></td><td>Save a file's contents before editing and check for conflicts before restoring it.</td></tr>
    <tr><td><strong>v0.20</strong></td><td><a href="./docs/tutorials/20-repair-loop.md">Repair Loop</a></td><td>Build a bounded repair loop from failure diagnosis, controlled recovery, and independent verification.</td></tr>
    <tr><td><strong>v0.21</strong></td><td><a href="./docs/tutorials/21-trace-replay.md">Trace &amp; Replay</a></td><td>Replay execution, failures, recovery, and verification without changing the task.</td></tr>
    <tr><th colspan="3"><a id="stage-7"></a>Stage 7 · Structured Planning</th></tr>
    <tr><td><strong>v0.22</strong></td><td><a href="./docs/tutorials/22-plan-contract.md">Plan Contract</a></td><td>Define goals and steps in structured plans, tracking revisions separately from progress.</td></tr>
    <tr><td><strong>v0.23</strong></td><td><a href="./docs/tutorials/23-plan-mode-handoff.md">Read-only planning and handoff</a></td><td>Investigate without changes, submit a plan for user approval, and execute within tool permissions.</td></tr>
    <tr><td><strong>v0.24</strong></td><td><a href="./docs/tutorials/24-replanning-policy.md">Evidence-backed replanning and bounded stagnation</a></td><td>Revise plans using new evidence and stop when repeated actions make no progress.</td></tr>
    <tr><td><strong>v0.25</strong></td><td><a href="./docs/tutorials/25-plan-trace-evaluation.md">Plan trace and evaluation</a></td><td>Connect plan changes with execution evidence to review progress and record completeness.</td></tr>
    <tr><th colspan="3"><a id="stage-8"></a>Stage 8 · Background Processes and Task Boundaries</th></tr>
    <tr><td><strong>v0.26</strong></td><td><a href="./docs/tutorials/26-background-process-boundaries.md">Background process start and task boundaries</a></td><td>Start long-running background tasks so the Agent can continue working and clean up on exit.</td></tr>
    <tr><td><strong>v0.27</strong></td><td><a href="./docs/tutorials/27-process-observation.md">Observing background processes</a></td><td>Query background process status, read new output, and wait for results.</td></tr>
    <tr><td><strong>v0.28</strong></td><td><a href="./docs/tutorials/28-process-control.md">Controlling background processes</a></td><td>Let the Agent stop background processes with permission and confirm their exit.</td></tr>
    <tr><td><strong>v0.29</strong></td><td><a href="./docs/tutorials/29-interactive-process.md">Driving input waiting processes</a></td><td>Send text through a pipe to support simple interactions with background processes.</td></tr>
    <tr><th colspan="3"><a id="stage-9"></a>Stage 9 · Session Persistence and Safe Handoff</th></tr>
    <tr><td><strong>v0.30</strong></td><td><a href="./docs/tutorials/30-session-persistence.md">Session persistence and safe points</a></td><td>Save task state and conversations at safe points to prepare reliable records for resumption.</td></tr>
    <tr><td><strong>v0.31</strong></td><td><a href="./docs/tutorials/31-safe-resume.md">Safe resume from a complete safe point</a></td><td>Check saved records and workspace changes before safely resuming a session in a new process.</td></tr>
    <tr><td><strong>v0.32</strong></td><td><a href="./docs/tutorials/32-durable-tool-boundaries.md">Durable tool execution boundaries</a></td><td>Persist state around tool execution to identify where an interruption occurred.</td></tr>
    <tr><td><strong>v0.33</strong></td><td><a href="./docs/tutorials/33-crash-recovery.md">Crash recovery and uncertain-effect handoff</a></td><td>Distinguish unexecuted calls from uncertain outcomes after a crash and let the user decide what follows.</td></tr>
    <tr><th colspan="3"><a id="stage-10"></a>Stage 10 · Controlled subagent delegation</th></tr>
    <tr><td><strong>v0.34</strong></td><td><a href="./docs/tutorials/34-minimal-delegation.md">Minimal controlled subagent delegation</a></td><td>Delegate focused read-only investigations to isolated subagents and collect their reports.</td></tr>
    <tr><td><strong>v0.35</strong></td><td><a href="./docs/tutorials/35-shared-agent-runtime.md">Shared parent/child runtime loop</a></td><td>Share one runtime loop between parent and child agents while keeping state and permissions separate.</td></tr>
    <tr><td><strong>v0.36</strong></td><td><a href="./docs/tutorials/36-multi-provider.md">Multiple providers and one protocol boundary</a></td><td>Configure different model services for parent and child agents through a shared protocol interface.</td></tr>
    <tr><td><strong>v0.37</strong></td><td><a href="./docs/tutorials/37-subagent-lifecycle-budget.md">Subagent lifecycle and aggregate budgets</a></td><td>Track subagent lifecycles and manage aggregate budgets and cancellation requests.</td></tr>
    <tr><td><strong>v0.38</strong></td><td><a href="./docs/tutorials/38-parallel-delegation.md">Bounded parallel subagents</a></td><td>Run multiple read-only investigations with bounded concurrency and ordered result delivery.</td></tr>
    <tr><td><strong>v0.39</strong></td><td><a href="./docs/tutorials/39-durable-delegation.md">Durable delegation delivery</a></td><td>Persist subagent reports before delivering them to the parent so interruptions do not lose results.</td></tr>
    <tr><th colspan="3"><a id="stage-11"></a>Stage 11 · Workspace Memory &amp; References</th></tr>
    <tr><td><strong>v0.40</strong></td><td><a href="./docs/tutorials/40-persistent-memory.md">Lightweight persistent memory</a></td><td>Let the Agent explicitly save, read, revise, and delete long-term workspace memories.</td></tr>
    <tr><td><strong>v0.41</strong></td><td><a href="./docs/tutorials/41-memory-retrieval.md">Relevant memory retrieval</a></td><td>Retrieve task-relevant memories and add a few summaries to the model context.</td></tr>
    <tr><td><strong>v0.42</strong></td><td><a href="./docs/tutorials/42-local-references.md">Named local References</a></td><td>Search and read local reference material outside the workspace through configured names.</td></tr>
    <tr><th colspan="3"><a id="stage-12"></a>Stage 12 · MCP &amp; Skills</th></tr>
    <tr><td><strong>v0.43</strong></td><td><a href="./docs/tutorials/43-stdio-mcp-client.md">Minimal stdio MCP client</a></td><td>Connect a standalone client to a local MCP server, discover tools, and call them with user confirmation.</td></tr>
    <tr><td><strong>v0.44</strong></td><td><a href="./docs/tutorials/44-mcp-tools-runtime.md">MCP Tools in the parent Agent Runtime</a></td><td>Integrate external MCP tools into the Agent's existing permission and execution flow.</td></tr>
    <tr><td><strong>v0.45</strong></td><td><a href="./docs/tutorials/45-local-skills.md">Local Skills discovery and on-demand loading</a></td><td>Discover and load local skill instructions on demand to guide the Agent's work.</td></tr>
    <tr><td><strong>v0.46</strong></td><td><a href="./docs/tutorials/46-mcp-http-resources-prompts.md">Restricted HTTP MCP, text Resources, and Prompts</a></td><td>Connect to HTTP MCP servers and let users select resources and preview prompt templates.</td></tr>
    <tr><th colspan="3"><a id="stage-13"></a>Stage 13 · Lightweight Agent collaboration</th></tr>
    <tr><td><strong>v0.47</strong></td><td><a href="./docs/tutorials/47-agent-profiles.md">Named Subagent roles</a></td><td>Configure read-only subagent roles with specific tools, models, and skills.</td></tr>
    <tr><td><strong>v0.48</strong></td><td><a href="./docs/tutorials/48-background-subagents.md">In-process background Subagents</a></td><td>Run read-only subagent investigations in the background while the parent continues and collects reports later.</td></tr>
    <tr><td><strong>v0.49</strong></td><td><a href="./docs/tutorials/49-resumable-child-session.md">Resumable child sessions</a></td><td>Continue completed child investigations after collecting results, with child session saving and resumption.</td></tr>
    <tr><th colspan="3"><a id="stage-14"></a>Stage 14 · Agent task evaluation</th></tr>
    <tr><td><strong>v0.50</strong></td><td><a href="./docs/tutorials/50-evaluation-harness.md">Independent evaluation harness</a></td><td>Run Agent tasks in isolated workspaces, grade them independently, and preserve execution records.</td></tr>
    <tr><td><strong>v0.51</strong></td><td><a href="./docs/tutorials/51-coding-benchmark.md">Repeated coding benchmark runs</a></td><td>Repeat fixed coding tasks and use independent tests to evaluate consistency.</td></tr>
    <tr><td><strong>v0.52</strong></td><td><a href="./docs/tutorials/52-reliability-evaluation.md">Fault injection and recovery evaluation</a></td><td>Inject faults to evaluate safety boundaries separately from task recovery.</td></tr>
    <tr><td><strong>v0.53</strong></td><td><a href="./docs/tutorials/53-regression-comparison.md">Regression comparison and capability benefit validation</a></td><td>Compare versions and memory retrieval settings to identify regressions and benefits.</td></tr>
  </tbody>
</table>

After `v0.10`, the Agent can inspect a project, search and modify files, run commands and tests, and control high-risk operations through permissions.

## Project Structure

```text
src/mini_agent/   runtime, config, state, permissions, context, and tools
skills/           project-local Skill workflow instructions
tests/             tests and smoke tests
docs/tutorials/    stage navigation and version-sliced lessons
docs/operation/    latest-version runbook
docs/plans/        roadmap and feature plans
docs/governance/   writing rules and decision records
examples/          example input/output files
```

## Design and Documentation

- Core LLM calls, the agent loop, tools, permissions, and state use only the standard library; UX enhancements are optional dependencies.
- The tool layer turns handler failures into results for the model; runtime constraints are summarized in [`AGENTS.md`](./AGENTS.md).
- [Tutorial guide](./docs/tutorials/README.md) · [manual](./docs/operation/manual.md) · [context architecture](./docs/operation/context-architecture.md) · [roadmap](./docs/plans/teaching-repo-plan.md) · [CHANGELOG](./CHANGELOG.md)

## Contributing

Issues and PRs are welcome. Before adding a lesson, read the [tutorial authoring guide](./docs/governance/tutorial-authoring.md), [README authoring guide](./docs/governance/readme-authoring.md), and `AGENTS.md`; the tutorial guide links the template and author entry points.

## License

MIT — see [LICENSE](./LICENSE)

<!-- Keywords: agent tutorial, LLM agent, coding agent, Python agent, function calling, build agent from scratch, AI agent, agent loop, tool calling -->
