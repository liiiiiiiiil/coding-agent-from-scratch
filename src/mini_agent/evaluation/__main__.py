"""CLI for validating cases, running trials, and rebuilding reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

from mini_agent.evaluation.report import build_report
from mini_agent.evaluation.report import build_suite_report
from mini_agent.evaluation.runner import (
    EvaluationRunner, _copy_fixture, run_grader_for_workspace,
)
from mini_agent.evaluation.schema import load_case, validate_fixture_directory
from mini_agent.evaluation.benchmark import load_suite, run_suite, suite_plan, validate_suite_baselines
from mini_agent.evaluation.reliability import (
    reliability_plan, run_reliability, validate_reliability,
)
from mini_agent.evaluation.reliability_report import build_reliability_report
from mini_agent.evaluation.reliability_schema import load_suite as load_reliability_suite


def _default_case() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "evaluation" / "smoke" / "case.json"


def _fixture_responses() -> tuple[dict, ...]:
    import json as json_module

    return (
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "fixture-edit-1",
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "arguments": json_module.dumps({
                        "path": "src/scale.py",
                        "old_string": "return value + 2",
                        "new_string": "return value * 2",
                    }),
                },
            }],
        },
        {"role": "assistant", "content": "已按要求修复函数。", "tool_calls": []},
    )


def _verify_smoke_grader(case) -> None:
    baseline = Path(tempfile.mkdtemp(prefix="mini-agent-eval-baseline-"))
    repaired = Path(tempfile.mkdtemp(prefix="mini-agent-eval-repaired-"))
    try:
        _copy_fixture(Path(case.case_dir) / case.fixture_dir, baseline)
        _copy_fixture(Path(case.case_dir) / case.fixture_dir, repaired)
        baseline_passed, baseline_error = run_grader_for_workspace(case, baseline, timeout=case.grader_timeout_seconds)
        if baseline_error is not None or baseline_passed is not False:
            raise RuntimeError("smoke grader must reject the original fixture")
        target = repaired / "src" / "scale.py"
        target.write_text("def scale(value):\n    return value * 2\n", encoding="utf-8")
        repaired_passed, repaired_error = run_grader_for_workspace(case, repaired, timeout=case.grader_timeout_seconds)
        if repaired_error is not None or repaired_passed is not True:
            raise RuntimeError("smoke grader must accept the known correct repair")
    finally:
        shutil.rmtree(baseline, ignore_errors=True)
        shutil.rmtree(repaired, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m mini_agent.evaluation")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="校验题目、fixture 与评分脚本")
    validate.add_argument("case_json")

    run = commands.add_parser("run", help="运行一次 Agent trial")
    run.add_argument("case_json")
    run.add_argument("--live", action="store_true", help="明确启用真实模型调用")
    run.add_argument("--output", required=True, help="独立 trial 结果目录")

    self_test = commands.add_parser("self-test", help="运行两次固定响应的离线试跑")
    self_test.add_argument("--output", required=True, help="独立 trial 结果目录")

    report = commands.add_parser("report", help="从原始 trial JSON 重建汇总")
    report.add_argument("output_dir")

    validate_suite = commands.add_parser("validate-suite", help="校验固定题集摘要和原始/正确版本评分")
    validate_suite.add_argument("suite_json")

    run_suite_command = commands.add_parser("run-suite", help="按固定顺序运行编码题集")
    run_suite_command.add_argument("suite_json")
    run_suite_command.add_argument("--live", action="store_true", help="明确启用真实模型调用")
    run_suite_command.add_argument("--repeats", type=int, default=None, help="每题重复次数，默认使用 suite 配置")
    run_suite_command.add_argument("--output", required=True, help="新的独立 suite run 目录")

    report_suite = commands.add_parser("report-suite", help="从 suite-run 和原始 trial 重建报告")
    report_suite.add_argument("suite_run_dir")

    validate_reliability_command = commands.add_parser("validate-reliability", help="校验冻结可靠性矩阵、任务、注入和 grader")
    validate_reliability_command.add_argument("suite_json")

    self_test_reliability = commands.add_parser("self-test-reliability", help="运行全部离线故障边界，每个参数变体两次")
    self_test_reliability.add_argument("--output", required=True, help="新的独立可靠性 run 目录")

    run_reliability_command = commands.add_parser("run-reliability", help="按冻结顺序运行 21 个 live reliability trial")
    run_reliability_command.add_argument("suite_json")
    run_reliability_command.add_argument("--live", action="store_true", help="明确启用真实模型调用")
    run_reliability_command.add_argument("--repeats", type=int, default=None, help="v1.0 固定为 3")
    run_reliability_command.add_argument("--output", required=True, help="新的独立 run 目录")

    report_reliability = commands.add_parser("report-reliability", help="从可靠性原始结果和证据只读重建报告")
    report_reliability.add_argument("run_dir")

    validate_comparison_command = commands.add_parser("validate-comparison", help="校验 v0.53 冻结比较合同")
    validate_comparison_command.add_argument("spec_json")

    plan_comparison_command = commands.add_parser("plan-comparison", help="冻结比较条件并生成审阅清单")
    plan_comparison_command.add_argument("spec_json")
    plan_comparison_command.add_argument("--output", required=True, help="新的审阅清单目录")

    self_test_comparison_command = commands.add_parser("self-test-comparison", help="用固定响应离线运行 36 槽比较矩阵")
    self_test_comparison_command.add_argument("--output", required=True, help="新的离线自测目录")

    run_comparison_command = commands.add_parser("run-comparison", help="按审阅清单顺序运行真实比较")
    run_comparison_command.add_argument("review_plan_json")
    run_comparison_command.add_argument("--live", action="store_true", help="明确启用真实模型调用")
    run_comparison_command.add_argument("--output", required=True, help="新的独立比较目录")

    report_comparison_command = commands.add_parser("report-comparison", help="从比较原始结果重建 JSON/Markdown 报告")
    report_comparison_command.add_argument("run_dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = EvaluationRunner()
    try:
        if args.command == "validate":
            case = runner.validate(args.case_json)
            fixture_count, fixture_bytes = validate_fixture_directory(Path(case.case_dir) / case.fixture_dir)
            grader = Path(case.case_dir) / case.grader_script
            print(json.dumps({
                "valid": True,
                "case_id": case.case_id,
                "version": case.version,
                "max_rounds": case.max_rounds,
                "agent_timeout_seconds": case.agent_timeout_seconds,
                "grader_timeout_seconds": case.grader_timeout_seconds,
                "allowed_tools": list(case.allowed_tools),
                "authorized_tools": list(case.authorized_tools),
                "fixture_files": fixture_count,
                "fixture_bytes": fixture_bytes,
                "grader_bytes": grader.stat().st_size,
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "run":
            if not args.live:
                raise ValueError("真实试跑必须显式传 --live")
            case = runner.validate(args.case_json)
            runner.validate_live_configuration(case)
            print(
                f"LIVE trial: case={case.case_id}@{case.version}; rounds<={case.max_rounds}; "
                f"agent_timeout={case.agent_timeout_seconds}s; grader_timeout={case.grader_timeout_seconds}s"
            )
            print("Task: " + case.task)
            print("Authorized tools: " + ", ".join(case.authorized_tools))
            trial_dir = runner.run_case(case, args.output, run_kind="live", live_confirmed=True)
            print(f"Trial saved: {trial_dir}")
            print((trial_dir / "trial.json").read_text(encoding="utf-8"))
            return 0
        if args.command == "self-test":
            case = runner.validate(_default_case())
            _verify_smoke_grader(case)
            results = []
            for _ in range(2):
                results.append(runner.run_case(
                    case, args.output, run_kind="fixture", responses=_fixture_responses(),
                ))
            raw = [json.loads((path / "trial.json").read_text(encoding="utf-8")) for path in results]
            if not all(item["success"] and item["run_kind"] == "fixture" for item in raw):
                print(json.dumps({"self_test": "failed", "trials": [str(path) for path in results]}, indent=2))
                return 1
            print(json.dumps({
                "self_test": "passed",
                "trials": [str(path) for path in results],
                "trial_ids": [item["trial_id"] for item in raw],
                "report": build_report(args.output),
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "report":
            print(json.dumps(build_report(args.output_dir), ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate-suite":
            suite = load_suite(args.suite_json)
            baselines = validate_suite_baselines(suite)
            print(json.dumps({
                "valid": True, "suite_id": suite.suite_id, "version": suite.version,
                "suite_sha256": suite.suite_sha256, "planned_trials_default": len(suite_plan(suite)),
                "cases": [
                    {
                        "case_id": item.case.case_id, "case_path": str(item.case_path),
                        "task": item.case.task, "max_rounds": item.case.max_rounds,
                        "agent_timeout_seconds": item.case.agent_timeout_seconds,
                        "grader_timeout_seconds": item.case.grader_timeout_seconds,
                        "authorized_tools": list(item.case.authorized_tools),
                        "initial_sha256": item.initial_sha256,
                        "grader_sha256": item.grader_sha256,
                        "known_good_sha256": item.known_good_sha256,
                        "regression_test_required": item.regression_test_required,
                    }
                    for item in suite.cases
                ],
                "offline_baselines": baselines,
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "run-suite":
            if not args.live:
                raise ValueError("真实模型套件运行必须显式传 --live")
            suite = load_suite(args.suite_json)
            plan = suite_plan(suite, args.repeats)
            # All topic, oracle, and provider checks happen before the first worker.
            validate_suite_baselines(suite)
            bindings = [runner.validate_live_configuration(item.case)[0] for item in suite.cases]
            if any(value != bindings[0] for value in bindings[1:]):
                raise ValueError("coding suite 的所有题目必须使用同一个冻结 model binding")
            print(
                f"LIVE suite: {suite.suite_id}@{suite.version}; suite_sha256={suite.suite_sha256}; "
                f"planned_trials={len(plan)}; repeats_per_case={len(plan) // len(suite.cases)}"
            )
            for item in suite.cases:
                case = item.case
                count = sum(slot["case_id"] == case.case_id for slot in plan)
                print(
                    f"- {case.case_id}: {count} trial(s); rounds<={case.max_rounds}; "
                    f"agent_timeout={case.agent_timeout_seconds}s; grader_timeout={case.grader_timeout_seconds}s; "
                    f"authorized_tools={','.join(case.authorized_tools)}"
                )
                print("  Task: " + case.task)
            ledger = run_suite(
                suite, args.output, repeats=args.repeats, run_kind="live", live_confirmed=True,
                runner=runner,
            )
            print(f"Suite run saved: {ledger.parent}")
            print(json.dumps(build_suite_report(ledger.parent), ensure_ascii=False, indent=2))
            return 0
        if args.command == "report-suite":
            print(json.dumps(build_suite_report(args.suite_run_dir), ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate-reliability":
            print(json.dumps(validate_reliability(args.suite_json), ensure_ascii=False, indent=2))
            return 0
        if args.command == "self-test-reliability":
            suite_path = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "evaluation" / "reliability" / "suite.json"
            suite = load_reliability_suite(suite_path)
            output = run_reliability(suite, args.output, run_kind="fixture")
            report_value = build_reliability_report(output)
            fixture = report_value["cohorts"]["fixture"]
            passed = (
                report_value["status"] == "completed"
                and fixture["recorded_trials"] == fixture["planned_slots"]
                and fixture["invariant_passed"] == fixture["recorded_trials"]
                and all(item["injection_errors"] == 0 and item["fault_not_triggered"] == 0
                        and item["evidence_incomplete"] == 0
                        for item in report_value["scenarios"].values())
            )
            print(json.dumps({"self_test": "passed" if passed else "incomplete_or_failed",
                              "run_dir": str(output), "report": report_value}, ensure_ascii=False, indent=2))
            return 0 if passed else 1
        if args.command == "run-reliability":
            if not args.live:
                raise ValueError("可靠性真实运行必须显式传 --live")
            suite = load_reliability_suite(args.suite_json)
            plan = reliability_plan(suite, run_kind="live", repeats=args.repeats)
            print(
                f"LIVE reliability suite: {suite.suite_id}@{suite.version}; "
                f"suite_sha256={suite.suite_sha256}; planned_trials={len(plan)}; repeats=3"
            )
            for scenario_id in suite.live_scenario_ids:
                scenario = next(item for item in suite.scenarios if item.scenario_id == scenario_id)
                scenario_slots = [item for item in plan if item["scenario_id"] == scenario_id]
                print(f"- {scenario_id}: {len(scenario_slots)} trial(s); rounds<={scenario.budget['max_rounds']}; "
                      f"tools<={scenario.budget['tool_calls']}; wall<={scenario.budget['wall_seconds']}s; "
                      f"parent_tokens<={scenario.budget['parent_tokens']}; child_tokens<={scenario.budget['child_tokens']}; "
                      f"tools={','.join(scenario.allowed_tools)}")
                print("  Task: " + scenario.task)
                print("  Permission rules: " + json.dumps(scenario.permission_rules, ensure_ascii=False))
                print("  Simulated user feedback: " + json.dumps(scenario.feedback_strategy, ensure_ascii=False))
            output = run_reliability(suite, args.output, run_kind="live", live_confirmed=True,
                                     repeats=args.repeats)
            report_value = build_reliability_report(output)
            print(f"Reliability run saved: {output}")
            print(json.dumps(report_value, ensure_ascii=False, indent=2))
            return 0
        if args.command == "report-reliability":
            print(json.dumps(build_reliability_report(args.run_dir), ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate-comparison":
            from mini_agent.evaluation.comparison import validate_comparison
            print(json.dumps(validate_comparison(args.spec_json), ensure_ascii=False, indent=2))
            return 0
        if args.command == "plan-comparison":
            from mini_agent.evaluation.comparison import plan_comparison
            output = plan_comparison(args.spec_json, args.output)
            print(f"Review plan saved: {output / 'comparison-plan.json'}")
            return 0
        if args.command == "self-test-comparison":
            from mini_agent.evaluation.comparison import self_test_comparison
            summary = self_test_comparison(args.output)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary.get("self_test") == "passed" else 1
        if args.command == "run-comparison":
            if not args.live:
                raise ValueError("真实比较运行必须显式传 --live")
            from mini_agent.evaluation.comparison import run_comparison
            from mini_agent.evaluation.comparison_report import build_comparison_report, write_derived_reports
            ledger = run_comparison(args.review_plan_json, args.output, live=True)
            report_value = build_comparison_report(ledger.parent)
            write_derived_reports(ledger.parent, report_value)
            print(f"Comparison run saved: {ledger.parent}")
            print(json.dumps(report_value, ensure_ascii=False, indent=2))
            regressions = any(edge["grader_regressions"] for edge in report_value["edges"])
            return 0 if report_value["comparison_complete"] and not regressions else 1
        if args.command == "report-comparison":
            from mini_agent.evaluation.comparison_report import build_comparison_report, write_derived_reports
            report_value = build_comparison_report(args.run_dir)
            paths = write_derived_reports(args.run_dir, report_value)
            print(json.dumps({"report_files": [str(path) for path in paths], "report": report_value}, ensure_ascii=False, indent=2))
            regressions = any(edge["grader_regressions"] for edge in report_value["edges"])
            return 0 if report_value["comparison_complete"] and not regressions else 1
    except Exception as error:
        print(f"evaluation error ({type(error).__name__}): {error}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
