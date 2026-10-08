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
    assert report["batch_complete"] is True
    assert report["comparison_complete"] is False
    assert edge["comparable"] is False
    assert "one_or_more_paired_samples_not_scorable" in edge["incomparability_reasons"]
    assert edge["paired_slots"][0]["experiment_slot_status"] == "infrastructure_error"


def _edit_trials(run_dir, update):
    from mini_agent.evaluation.comparison_schema import sha256_bytes
    path = run_dir / 'comparison-run.json'
    run = json.loads(path.read_text())
    for slot in run['slots']:
        if not slot['trial_path']:
            continue
        trial_path = run_dir / slot['trial_path']
        trial = json.loads(trial_path.read_text())
        update(trial)
        raw = (json.dumps(trial, sort_keys=True, indent=2) + '\n').encode()
        trial_path.write_bytes(raw)
        slot['trial_sha256'] = sha256_bytes(raw)
    path.write_text(json.dumps(run, sort_keys=True, indent=2) + '\n')


def test_all_provider_failures_preserve_raw_grading_but_have_no_capability_score(tmp_path):
    run_dir = write_fixture_run(tmp_path / 'run')
    def fail(trial):
        trial['agent'].update(error_kind='ProviderConnectionError', successful_responses=0,
                              stop_reason='agent_error', tool_calls=0)
        trial['grader'].update(passed=False, result={'passed': False})
    _edit_trials(run_dir, fail)
    report = build_comparison_report(run_dir)
    assert report['batch_complete'] is True
    assert report['comparison_complete'] is False
    assert report['report_rules_version'] == '2'
    for group in report['groups'].values():
        assert group['raw_grader'] == {'passed': 0, 'denominator': 4}
        assert group['scorable_trials'] == 0
        assert group['infrastructure_errors'] == 4
        assert group['independent_acceptance']['rate'] is None
        assert group['strict_task_success']['denominator'] == 0
    for edge in report['edges']:
        assert not edge['comparable'] and edge['conditions_match']
        assert edge['scorable_pairs'] == 0
        for case in edge['per_case'].values():
            assert case['pass_rate_percentage_point_delta'] is None
            assert case['input_token_delta'] is None
            assert case['agent_median_duration_ms']['delta'] is None
    assert 'None' not in render_markdown(report)
    assert len(report['manual_review_queue']) == 12


def test_partial_failure_uses_only_shared_eligible_pairs(tmp_path):
    run_dir = write_fixture_run(tmp_path / 'run')
    def fail_one(trial):
        if trial['group_id'] == 'current-off' and trial['case_id'] == 'pagination-boundary':
            trial['agent']['error_kind'] = 'ProviderTimeoutError'
            trial['agent']['input_tokens'] = 999999
    _edit_trials(run_dir, fail_one)
    report = build_comparison_report(run_dir)
    edge = report['edges'][0]
    assert edge['scorable_pairs'] == 3
    assert edge['grader_regressions'] == []
    case = edge['per_case']['pagination-boundary']
    assert case['baseline_grader']['denominator'] == 0
    assert case['experiment_grader']['denominator'] == 0
    assert case['input_token_delta'] is None
    assert report['groups']['old-off']['scorable_trials'] == 4
    assert report['groups']['current-off']['scorable_trials'] == 3


@pytest.mark.parametrize('error,stop', [
    ('RuntimeError', 'agent_error'), (None, 'timeout'),
    (None, 'token_limit'), (None, 'tool_call_limit'), (None, 'blocked'),
])
def test_agent_failures_remain_in_capability_denominator(tmp_path, error, stop):
    run_dir = write_fixture_run(tmp_path / 'run')
    def fail(trial):
        trial['agent'].update(error_kind=error, stop_reason=stop, state_status='failed')
        trial['grader'].update(passed=False, result={'passed': False})
    _edit_trials(run_dir, fail)
    report = build_comparison_report(run_dir)
    assert report['comparison_complete'] is True
    for group in report['groups'].values():
        assert group['scorable_trials'] == 4
        assert group['infrastructure_errors'] == 0
        assert group['strict_task_success']['passed'] == 0


def test_infrastructure_count_is_per_slot_and_not_per_error(tmp_path):
    from mini_agent.evaluation.comparison_metrics import group_metrics
    run_dir = write_fixture_run(tmp_path / 'run')
    run = json.loads((run_dir / 'comparison-run.json').read_text())
    slot = run['slots'][0]
    trial = json.loads((run_dir / slot['trial_path']).read_text())
    slot['status'] = 'infrastructure_error'
    trial['agent']['error_kind'] = 'ProviderConnectionError'
    trial['grader']['error_kind'] = 'grader_not_run'
    assert group_metrics([slot], [trial], {})['infrastructure_errors'] == 1


def test_independent_report_output_preserves_source_and_refuses_overwrite(tmp_path):
    run_dir = write_fixture_run(tmp_path / 'run')
    before = {p.relative_to(run_dir): p.read_bytes() for p in run_dir.rglob('*') if p.is_file()}
    report = build_comparison_report(run_dir)
    output = tmp_path / 'corrected'
    paths = write_derived_reports(run_dir, report, output=output)
    assert all(p.parent == output for p in paths)
    published = json.loads(paths[0].read_text())
    relative = published['source_evidence']['relative_directory']
    assert (output / relative).resolve() == run_dir.resolve()
    assert '[diff](<../run/trials/' in paths[1].read_text()
    assert report['source_evidence']['run_id'] == report['run_id']
    with pytest.raises(FileExistsError):
        write_derived_reports(run_dir, report, output=output)
    with pytest.raises(ValueError, match='outside'):
        write_derived_reports(run_dir, report, output=run_dir / 'corrected')
    assert before == {p.relative_to(run_dir): p.read_bytes() for p in run_dir.rglob('*') if p.is_file()}


def test_cli_independent_report_output(tmp_path, capsys):
    from mini_agent.evaluation.__main__ import main
    run_dir = write_fixture_run(tmp_path / 'run')
    output = tmp_path / 'derived'
    assert main(['report-comparison', str(run_dir), '--output', str(output)]) == 1  # fixture regression
    assert (output / 'report.json').is_file()
    assert not (run_dir / 'report.json').exists()
    capsys.readouterr()


def test_paired_cost_and_missing_usage_do_not_compare_unmatched_totals(tmp_path):
    from mini_agent.evaluation.comparison_metrics import paired_cost_delta
    run_dir = write_fixture_run(tmp_path / 'run')
    def change_usage(trial):
        if trial['case_id'] == 'pagination-boundary' and trial['group_id'] == 'current-off':
            trial['agent']['input_tokens'] = 200
    _edit_trials(run_dir, change_usage)
    report = build_comparison_report(run_dir)
    case = report['edges'][0]['per_case']['pagination-boundary']
    assert case['input_token_delta'] == 100
    assert case['cost_delta_usd'] == '0.000125000000'
    def remove_usage(trial):
        if trial['case_id'] == 'pagination-boundary' and trial['group_id'] == 'current-off':
            trial['agent']['input_tokens'] = None
            trial['agent']['duration_ms'] = None
    _edit_trials(run_dir, remove_usage)
    case = build_comparison_report(run_dir)['edges'][0]['per_case']['pagination-boundary']
    assert case['input_token_delta'] is None
    assert case['cost_delta_usd'] is None
    assert case['agent_median_duration_ms']['delta'] is None
    trial = json.loads(next((run_dir / 'trials').glob('*/trial.json')).read_text())
    assert paired_cost_delta([trial], [trial], {}) is None
