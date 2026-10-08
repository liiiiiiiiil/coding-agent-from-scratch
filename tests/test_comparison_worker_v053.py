from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evaluation import benchmark
from mini_agent.evaluation.comparison_worker import _Observations, _memory_seed_digest, run_request
from mini_agent.evaluation.comparison import _semantic_memory


SUITE_PATH = Path(__file__).parent / "fixtures/evaluation/benchmark/suite.json"
MEMORY_ROOT = Path(__file__).parent / "fixtures/evaluation/comparison/memories"


def _seeds():
    suite = benchmark.load_suite(SUITE_PATH)
    rows = []
    for item in suite.cases:
        raw = json.loads((MEMORY_ROOT / f"{item.case.case_id}.json").read_text(encoding="utf-8"))
        semantic = _semantic_memory(raw)
        rows.append({key: semantic[key] for key in ("case_id", "title", "body", "tags", "source")})
    return rows, _memory_seed_digest(rows)


def test_actual_runtime_uses_memory_retrieval_switch_without_exposing_bodies(tmp_path):
    suite = benchmark.load_suite(SUITE_PATH)
    case = suite.cases[0]
    seeds, digest = _seeds()
    results = {}
    for enabled in (False, True):
        workspace = tmp_path / f"workspace-{enabled}"
        from mini_agent.evaluation.runner import _copy_fixture
        _copy_fixture(Path(case.case.case_dir) / case.case.fixture_dir, workspace)
        memory_dir = tmp_path / f"memory-{enabled}"
        memory_dir.mkdir()
        request = {
            "schema_version": 1, "trial_id": "fixture-trial", "run_kind": "fixture",
            "case": case.case.to_dict(), "workspace": str(workspace),
            "responses": [{"role": "assistant", "content": "done", "tool_calls": []}],
            "memory_retrieval_enabled": enabled, "memory_dir": str(memory_dir),
            "memory_seeds": seeds, "memory_material_sha256": digest,
            "model_profile": "fixture", "max_total_tokens": 64000,
            "started_marker": str(tmp_path / f"started-{enabled}.marker"),
        }
        results[enabled] = run_request(request)
        assert results[enabled]["agent_started"] is True
        assert results[enabled]["token_source"] == "fixture"
        assert results[enabled]["subagent_calls"] == 0
        assert not (workspace / "memory").exists()
        serialized = json.dumps(results[enabled], ensure_ascii=False)
        assert all(seed["body"] not in serialized for seed in seeds)
    assert results[False]["memory_evidence"]["retrieval_attempts"] == 0
    assert results[False]["memory_evidence"]["retrieval_occurred"] is False
    assert results[True]["memory_evidence"]["retrieval_attempts"] >= 1
    assert results[True]["memory_evidence"]["retrieval_occurred"] is True


def test_invalid_repeat_counter_requires_same_tool_arguments_and_result(tmp_path):
    observer = _Observations(tmp_path)
    state = SimpleNamespace(snapshot=lambda: {"status": "running", "verification_required": False})
    runtime = SimpleNamespace(context=SimpleNamespace(state=state))

    def call(tool, output):
        return SimpleNamespace(tool=tool, arguments={"path": "src/a.py"}, output=output,
                               error_kind=None, exit_code=0, outcome="succeeded", permission="allow")

    observer.observe_round(runtime, (), (call("read_file", {"content": "x"}),))
    observer.observe_round(runtime, (), (call("read_file", {"content": "x"}),))
    observer.observe_round(runtime, (), (call("grep", {"content": "x"}),))
    observer.observe_round(runtime, (), (call("read_file", {"content": "y"}),))
    assert observer.invalid_repeats == 1


def test_new_structured_state_fact_starts_a_new_repeat_stage(tmp_path):
    observer = _Observations(tmp_path)
    snapshot = {"current_generation_id": 1}
    state = SimpleNamespace(snapshot=lambda: snapshot)
    runtime = SimpleNamespace(context=SimpleNamespace(state=state))
    call = SimpleNamespace(tool="read_file", arguments={"path": "src/a.py"}, output={"content": "x"},
                           error_kind=None, exit_code=0, outcome="succeeded", permission="allow")
    observer.observe_round(runtime, (), (call,))
    observer.observe_round(runtime, (), (call,))
    snapshot["current_generation_id"] = 2
    observer.observe_round(runtime, (), (call,))
    assert observer.invalid_repeats == 1
