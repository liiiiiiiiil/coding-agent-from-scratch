"""Read-only admission replay against observed request costs.

Balances and requests remain the historical observations. This deliberately
does not simulate new model replies, new contexts or recovery success.
"""
from __future__ import annotations

import json
from pathlib import Path

from mini_agent.budget import TaskBudgetController, REQUEST_POLICY, request_key
from mini_agent.evaluation.reliability_report import build_reliability_report
from mini_agent.evaluation.reliability_schema import safe_relative


def replay_request_costs(run_dir):
    root = Path(run_dir).resolve(strict=True)
    report = build_reliability_report(root)  # validates frozen source and evidence
    ledger = json.loads((root / "suite-run.json").read_text())
    trials = []
    for slot in ledger["slots"]:
        if slot["status"] != "completed":
            continue
        path = root / safe_relative(slot["trial_path"], "trial_path") / "trial.json"
        trial = json.loads(path.read_text())
        controller = TaskBudgetController(64000)
        key = request_key(trial.get("model_binding_ref"),
                          trial["state_summary"].get("tool_schema_sha256"), "work")
        counts = dict(decisions=0, historical_refusals=0, admitted_at_observed_balance=0,
                      observed_input_above_new_reservation=0,
                      observed_output_above_new_cap=0, input_tokens=0, output_tokens=0)
        phases = trial["phases"]
        for phase in ("pre_crash", "agent", "recovery"):
            for row in phases.get(phase, {}).get("request_diagnostics", {}).get("requests", []):
                counts["decisions"] += 1
                counts["historical_refusals"] += row["status"] == "refused"
                # The observed balance belongs to the old execution. No new
                # admission is allowed to change later historical balances.
                controller.data["input_tokens"] = row["cumulative_tokens_before"]
                proposal = controller.proposal(row["reserved_input_tokens_estimated"], key)
                counts["admitted_at_observed_balance"] += proposal["admitted"]
                if row["usage_source"] == "provider":
                    actual_in, actual_out = row["input_tokens"], row["output_tokens"]
                    counts["input_tokens"] += actual_in
                    counts["output_tokens"] += actual_out
                    counts["observed_input_above_new_reservation"] += actual_in > proposal["input"]
                    counts["observed_output_above_new_cap"] += actual_out > proposal["output"]
                    samples = controller.data["samples"].setdefault(key, [])
                    samples.append([row["reserved_input_tokens_estimated"], actual_in])
                    del samples[:-8]
        trials.append({"trial_id": trial["trial_id"], "scenario_id": trial["scenario_id"], **counts})
    totals = {key: sum(row[key] for row in trials) for key in counts} if trials else {}
    return {
        "format": "mini_agent.request_cost_replay", "schema_version": 1,
        "source_suite_sha256": report["suite_sha256"], "policy": dict(REQUEST_POLICY),
        "balance_basis": "observed historical balance; unchanged raw request estimates",
        "limitations": "No future model replies, trimmed contexts, finalization eligibility or recovery success inferred.",
        "totals": totals, "trials": trials,
    }
