from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mini_agent.evaluation import benchmark
from mini_agent.evaluation.comparison import _execution_order, run_comparison
from mini_agent.evaluation.comparison_schema import PINNED_V052_REVISION
from mini_agent.evaluation.comparison_sources import preflight_source, source_checkout
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
        result = preflight_source(checkout)
        snapshot = benchmark.runtime_fingerprint()
        assert result["preflight"] == "passed"
        assert result["runtime_fingerprint"] != snapshot


def test_live_runner_refuses_without_explicit_flag_before_touching_paths(tmp_path):
    with pytest.raises(ValueError, match="--live"):
        run_comparison(tmp_path / "missing-plan.json", tmp_path / "should-not-exist", live=False)
    assert not (tmp_path / "should-not-exist").exists()
