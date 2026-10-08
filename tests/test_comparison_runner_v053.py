from __future__ import annotations

import hashlib
from pathlib import Path
import uuid

import pytest

from mini_agent.evaluation import benchmark
from mini_agent.evaluation.comparison import (
    _case_by_id, _execution_order, _fixture_spec, _invoke_trial,
    _load_memory_materials, _load_suite, load_fixture_responses, run_comparison,
)
from mini_agent.evaluation.comparison_schema import PINNED_V052_REVISION
from mini_agent.evaluation.comparison_sources import (
    REPOSITORY_ROOT, preflight_source, source_checkout, source_tree_fingerprint,
)
from tests.comparison_helpers_v053 import comparison_spec


SUITE_PATH = Path(__file__).parent / "fixtures/evaluation/benchmark/suite.json"


def test_execution_order_rotates_group_position_per_case_and_repetition():
    suite = benchmark.load_suite(SUITE_PATH)
    spec = comparison_spec()
    spec["repeats"] = 3
    order = _execution_order(suite, __import__("mini_agent.evaluation.comparison_schema", fromlist=["validate_comparison_spec"]).validate_comparison_spec(spec))
    assert len(order) == 36
    assert len({item["slot_id"] for item in order}) == 36
    by_case_rep = {}
    for item in order:
        by_case_rep.setdefault((item["case_id"], item["repetition"]), []).append(item["group_id"])
    for groups in by_case_rep.values():
        assert len(groups) == 3
        assert len(set(groups)) == 3
    case_ids = [item.case.case_id for item in suite.cases]
    for repetition in (1, 2, 3):
        first_case = case_ids[0]
        groups = by_case_rep[(first_case, repetition)]
        expected_shift = (repetition - 1) % 3
        assert groups == ["old-off", "current-off", "current-on"][expected_shift:] + ["old-off", "current-off", "current-on"][:expected_shift]


def test_v052_is_materialized_from_its_commit_and_preflighted_without_current_modules():
    with source_checkout(PINNED_V052_REVISION) as checkout:
        assert (checkout / "src/mini_agent/runtime.py").is_file()
        assert not (checkout / "src/mini_agent/evaluation/comparison.py").exists()
        assert all(not ancestor.is_symlink() for ancestor in (checkout, *checkout.parents))
        archived_suite = benchmark.load_suite(
            checkout / "tests/fixtures/evaluation/benchmark/suite.json",
            require_pinned=True,
        )
        result = preflight_source(checkout)
        snapshot = benchmark.runtime_fingerprint()
        assert result["preflight"] == "passed"
        assert result["runtime_fingerprint"] != snapshot
        assert archived_suite.suite_id == "coding-benchmark"
        assert archived_suite.version == "1.1"


def test_live_runner_refuses_without_explicit_flag_before_touching_paths(tmp_path):
    with pytest.raises(ValueError, match="--live"):
        run_comparison(tmp_path / "missing-plan.json", tmp_path / "should-not-exist", live=False)
    assert not (tmp_path / "should-not-exist").exists()


def test_trial_publishes_complete_artifact_contract_before_validation(tmp_path):
    spec = _fixture_spec()
    suite = _load_suite(spec)
    seeds, _summary = _load_memory_materials(spec, suite)
    responses = load_fixture_responses()
    group = next(item for item in spec.value["groups"] if item["group_id"] == "current-off")
    slot = next(item for item in _execution_order(suite, spec) if item["group_id"] == "current-off")
    source = REPOSITORY_ROOT
    result, relative, digest = _invoke_trial(
        run_id=str(uuid.uuid4()),
        slot=slot,
        spec=spec,
        suite_case=_case_by_id(suite, slot["case_id"]),
        group=group,
        source_root=source,
        source_fingerprint=source_tree_fingerprint(source / "src"),
        seeds=seeds,
        output_root=tmp_path / "run",
        fixture_actions=responses[slot["case_id"]]["success"],
        run_kind="fixture",
        binding_payload=None,
        redactions=(),
    )

    assert set(result["evidence"]["artifacts"]) == {"diff", "agent_log", "grader_log"}
    assert relative == f"trials/{slot['slot_id']}/trial.json"
    assert len(digest) == 64
