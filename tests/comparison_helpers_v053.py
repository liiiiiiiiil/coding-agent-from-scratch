from __future__ import annotations

import json
from pathlib import Path
import uuid

from mini_agent.evaluation.comparison_schema import (
    PINNED_V052_REVISION, comparison_execution_order, sha256_bytes, sha256_json,
    validate_comparison_spec, validate_comparison_trial,
)


SUITE_SHA = "73f9d068d7dc19e482a74ab2f674921c4dd2e5467516c4f3a95736cafcd21469"
MATERIAL_SHA = "a" * 64
CURRENT_REVISION = "b" * 40
CASE_ID = "pagination-boundary"


def comparison_spec():
    case_ids = [
        "pagination-boundary", "orders-discount-receipt",
        "cache-expiry-regression", "config-priority-investigation",
    ]
    raw = {
        "schema_version": 1,
        "comparison_id": "test-comparison-v053",
        "version": "1.0",
        "suite": {
            "suite_id": "coding-benchmark", "version": "1.1",
            "sha256": SUITE_SHA, "path": "tests/fixtures/evaluation/benchmark/suite.json",
        },
        "groups": [
            {"group_id": "old-off", "source_revision": PINNED_V052_REVISION,
             "model_profile": "fixture", "memory_retrieval_enabled": False,
             "memory_material_sha256": MATERIAL_SHA},
            {"group_id": "current-off", "source_revision": CURRENT_REVISION,
             "model_profile": "fixture", "memory_retrieval_enabled": False,
             "memory_material_sha256": MATERIAL_SHA},
            {"group_id": "current-on", "source_revision": CURRENT_REVISION,
             "model_profile": "fixture", "memory_retrieval_enabled": True,
             "memory_material_sha256": MATERIAL_SHA},
        ],
        "edges": [
            {"baseline_group": "old-off", "experiment_group": "current-off", "allowed_changes": ["source_revision"]},
            {"baseline_group": "current-off", "experiment_group": "current-on", "allowed_changes": ["memory_retrieval_enabled"]},
        ],
        "repeats": 1,
        "budget": {"max_tool_calls": 48, "max_total_tokens": 64000},
        "memory_materials": [
            {"case_id": case_id, "path": f"tests/fixtures/evaluation/comparison/memories/{case_id}.json", "semantic_sha256": f"{index + 1:x}" * 64}
            for index, case_id in enumerate(case_ids)
        ],
        "price_snapshot": {
            "currency": "USD", "source": "fixture", "effective_date": "not-applicable",
            "input_usd_per_million": "1.25", "output_usd_per_million": "2.5",
        },
        "metrics_rules_version": "1",
    }
    raw["groups"][0]["memory_material_sha256"] = sha256_json([
        {"case_id": item["case_id"], "semantic_sha256": item["semantic_sha256"]}
        for item in raw["memory_materials"]
    ])
    for group in raw["groups"]:
        group["memory_material_sha256"] = raw["groups"][0]["memory_material_sha256"]
    return validate_comparison_spec(raw).value


def write_fixture_run(root: Path, *, slot_status=None):
    """Build a tiny integrity-linked run for read-only report tests."""
    from mini_agent.evaluation.comparison_schema import validate_comparison_run

    spec = comparison_spec()
    current_fingerprint = sha256_bytes(b"current-runtime")
    old_fingerprint = sha256_bytes(b"old-runtime")
    source_snapshots = {
        "old-off": {"revision": PINNED_V052_REVISION, "runtime_fingerprint": old_fingerprint, "package_version": "0.52.0"},
        "current-off": {"revision": CURRENT_REVISION, "runtime_fingerprint": current_fingerprint, "package_version": "0.53.0"},
        "current-on": {"revision": CURRENT_REVISION, "runtime_fingerprint": current_fingerprint, "package_version": "0.53.0"},
    }
    from mini_agent.evaluation.benchmark import comparison_suite_summary, load_suite
    suite = load_suite(Path(__file__).parent / "fixtures/evaluation/benchmark/suite.json")
    suite_summary = comparison_suite_summary(suite)
    case_ids = [item["case_id"] for item in suite_summary["cases"]]
    order = comparison_execution_order(case_ids, spec["groups"], spec["repeats"])
    binding_ref = None
    plan = {
        "schema_version": 1, "plan_id": str(uuid.uuid4()), "comparison_id": spec["comparison_id"],
        "spec": spec, "spec_sha256": sha256_json(spec), "suite_summary": suite_summary,
        "source_snapshots": source_snapshots, "adapter_sha256": sha256_bytes(b"adapter"),
        "environment": {
            "python_version": "3.x", "python_implementation": "CPython",
            "platform_system": "TestOS", "platform_release": "1", "platform_machine": "test",
        }, "model_binding_ref": binding_ref,
        "execution_order": order,
        "memory_material_summary": {
            "semantic_digest": spec["groups"][0]["memory_material_sha256"],
            "materials": [{"case_id": item["case_id"], "semantic_sha256": item["semantic_sha256"]} for item in spec["memory_materials"]],
        },
        "offline_baseline_outcomes": [
            outcome for case_id in case_ids
            for outcome in ({"case_id": case_id, "tree": "initial", "passed": False},
                            {"case_id": case_id, "tree": "known_good", "passed": True})
        ],
    }
    plan["plan_sha256"] = sha256_json(plan)
    root.mkdir(parents=True)
    (root / "comparison-plan.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (root / "comparison-spec.json").write_text(json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (root / "suite.json").write_bytes(suite.suite_bytes)

    run_id = str(uuid.uuid4())
    slots = []
    regression_values = {("old-off", CASE_ID): True, ("current-off", CASE_ID): False}
    for index, entry in enumerate(order):
        group = next(item for item in spec["groups"] if item["group_id"] == entry["group_id"])
        current = group["source_revision"] == CURRENT_REVISION
        memory_on = group["memory_retrieval_enabled"]
        passed_value = regression_values.get((group["group_id"], entry["case_id"]), True)
        trial_id = str(uuid.uuid4())
        artifacts = {}
        trial_dir = root / "trials" / entry["slot_id"]
        trial_dir.mkdir(parents=True)
        for name, filename, content in (
            ("diff", "diff.patch", b"diff\n"),
            ("agent_log", "agent.log", b"agent finished\n"),
            ("grader_log", "grader.log", b"grader finished\n"),
        ):
            (trial_dir / filename).write_bytes(content)
            artifacts[name] = {"path": f"trials/{entry['slot_id']}/{filename}", "sha256": sha256_bytes(content), "size_bytes": len(content)}
        trial = {
            "schema_version": 1, "trial_id": trial_id, "run_id": run_id,
            "slot_id": entry["slot_id"], "group_id": group["group_id"], "case_id": entry["case_id"],
            "repetition": entry["repetition"], "source_revision": group["source_revision"],
            "source_fingerprint": current_fingerprint if current else old_fingerprint,
            "suite_sha256": SUITE_SHA, "model_binding_ref": binding_ref,
            "memory_retrieval_enabled": memory_on,
            "memory_material_sha256": group["memory_material_sha256"],
            "memory_evidence": {"retrieval_occurred": memory_on, "retrieval_attempts": int(memory_on), "failure_categories": [], "material_sha256": group["memory_material_sha256"]},
            "agent": {
                "started": True, "stop_reason": "text", "state_status": "done", "error_kind": None,
                "llm_calls": 2, "successful_responses": 2, "tool_calls": 1,
                "tool_outcomes": {"succeeded": 1, "failed": 0, "denied": 0, "invalid": 0, "timeout": 0},
                "permission_denials": 0, "input_tokens": 100, "output_tokens": 20,
                "token_source": "fixture", "duration_ms": 1000 + index,
                "invalid_repeat_count": 0, "subagent_calls": 0,
            },
            "grader": {"passed": passed_value, "error_kind": None, "duration_ms": 10, "exit_code": 0, "result": {"passed": passed_value}},
            "cleanup": {"complete": True, "issue": None}, "evidence": {"artifacts": artifacts},
        }
        trial_bytes = json.dumps(validate_comparison_trial(trial).value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        (trial_dir / "trial.json").write_bytes(trial_bytes)
        status = slot_status[index] if slot_status and index < len(slot_status) else "completed"
        slots.append({**entry, "status": status, "trial_path": f"trials/{entry['slot_id']}/trial.json" if status == "completed" else None,
                      "trial_sha256": sha256_bytes(trial_bytes) if status == "completed" else None,
                      "error_kind": None if status == "completed" else "not_run"})
    run_status = "completed"
    if any(slot["status"] == "interrupted" for slot in slots):
        run_status = "interrupted"
    run = {
        "schema_version": 1, "run_id": run_id, "comparison_id": spec["comparison_id"],
        "spec_sha256": sha256_json(spec), "plan_sha256": plan["plan_sha256"],
        "suite": suite_summary, "frozen_sources": source_snapshots,
        "environment": plan["environment"], "model_binding_ref": binding_ref,
        "execution_order": order, "run_kind": "fixture", "status": run_status,
        "started_at": "2026-10-08T00:00:00Z", "updated_at": "2026-10-08T00:00:01Z", "slots": slots,
    }
    run_bytes = json.dumps(validate_comparison_run(run).value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    (root / "comparison-run.json").write_bytes(run_bytes)
    return root
