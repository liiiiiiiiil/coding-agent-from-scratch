from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_agent.evaluation.comparison_report import (
    build_comparison_report, render_markdown, write_derived_reports,
)
from tests.comparison_helpers_v053 import write_fixture_run


def test_report_rebuild_is_deterministic_and_includes_paired_evidence(tmp_path):
    run_dir = write_fixture_run(tmp_path / "run")
    first = build_comparison_report(run_dir)
    second = build_comparison_report(run_dir)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(second, ensure_ascii=False, sort_keys=True)
    assert first["batch_complete"] is True
    assert first["edges"][0]["grader_regressions"][0]["case_id"] == "pagination-boundary"
    pair = first["edges"][0]["paired_slots"][0]
    assert pair["baseline_evidence"]["artifacts"]["diff"].startswith("trials/")
    assert "null" in json.dumps(first["recovery_success_rate"])
    markdown = render_markdown(first)
    assert "old-off->current-off" in markdown
    assert "三次重复" in markdown
    json_path, markdown_path = write_derived_reports(run_dir, first)
    first_json = json_path.read_bytes()
    first_markdown = markdown_path.read_bytes()
    write_derived_reports(run_dir, build_comparison_report(run_dir))
    assert json_path.read_bytes() == first_json
    assert markdown_path.read_bytes() == first_markdown


def test_report_rejects_modified_raw_artifact_digest(tmp_path):
    run_dir = write_fixture_run(tmp_path / "run")
    artifact = run_dir / "trials" / next((run_dir / "trials").iterdir()).name / "diff.patch"
    artifact.write_bytes(b"replaced evidence\n")
    with pytest.raises(ValueError, match="artifact digest"):
        build_comparison_report(run_dir)


def test_report_preserves_missing_slot_and_reports_incomparable_edge(tmp_path):
    run_dir = write_fixture_run(tmp_path / "run", slot_status=("completed", "infrastructure_error", "completed"))
    report = build_comparison_report(run_dir)
    edge = report["edges"][0]
    assert report["batch_complete"] is False
    assert edge["comparable"] is False
    assert "one_or_more_paired_samples_not_scorable" in edge["incomparability_reasons"]
    assert edge["paired_slots"][0]["experiment_slot_status"] == "infrastructure_error"
