from __future__ import annotations

import copy

import pytest

from mini_agent.evaluation.comparison_schema import (
    MAX_COMPARISON_SLOTS, validate_comparison_spec,
)
from tests.comparison_helpers_v053 import comparison_spec


def test_comparison_spec_is_strict_and_normalized():
    raw = comparison_spec()
    parsed = validate_comparison_spec(raw)
    assert parsed.value == raw
    assert parsed.digest

    unknown = copy.deepcopy(raw)
    unknown["unexpected"] = True
    with pytest.raises(ValueError, match="未知字段"):
        validate_comparison_spec(unknown)

    sensitive = copy.deepcopy(raw)
    sensitive["groups"][0]["api_key"] = "must-not-persist"
    with pytest.raises(ValueError, match="敏感配置"):
        validate_comparison_spec(sensitive)


def test_comparison_edges_must_allow_exactly_the_declared_differences():
    raw = comparison_spec()
    raw["groups"][1]["model_profile"] = "other-profile"
    with pytest.raises(ValueError, match="条件差异"):
        validate_comparison_spec(raw)


def test_model_provider_comparison_can_be_declared_by_local_profile_alias():
    raw = comparison_spec()
    raw["groups"][1]["model_profile"] = "second-local-profile"
    raw["groups"][2]["model_profile"] = "second-local-profile"
    raw["edges"][0]["allowed_changes"] = ["source_revision", "model_profile"]
    parsed = validate_comparison_spec(raw)
    assert parsed.value["groups"][1]["model_profile"] == "second-local-profile"

    raw = comparison_spec()
    raw["edges"][1]["allowed_changes"] = ["source_revision", "memory_retrieval_enabled"]
    with pytest.raises(ValueError, match="条件差异"):
        validate_comparison_spec(raw)


def test_comparison_resources_and_duplicate_identities_are_bounded():
    raw = comparison_spec()
    raw["groups"][2]["group_id"] = raw["groups"][1]["group_id"]
    with pytest.raises(ValueError, match="group_id 重复"):
        validate_comparison_spec(raw)

    raw = comparison_spec()
    raw["repeats"] = 10
    raw["groups"] = raw["groups"] + [
        {**raw["groups"][1], "group_id": f"extra-{index}"}
        for index in range(3, 8)
    ]
    raw["memory_materials"] = [
        {"case_id": f"case-{index}", "path": f"tests/fixtures/case-{index}.json", "semantic_sha256": f"{index:064x}"}
        for index in range(16)
    ]
    digest = __import__("mini_agent.evaluation.comparison_schema", fromlist=["sha256_json"]).sha256_json([
        {"case_id": item["case_id"], "semantic_sha256": item["semantic_sha256"]}
        for item in raw["memory_materials"]
    ])
    for group in raw["groups"]:
        group["memory_material_sha256"] = digest
    assert len(raw["groups"]) * len(raw["memory_materials"]) * raw["repeats"] > MAX_COMPARISON_SLOTS
    with pytest.raises(ValueError, match="总槽位超过"):
        validate_comparison_spec(raw)


def test_price_uses_bounded_decimal_strings_and_full_commit_ids():
    raw = comparison_spec()
    raw["price_snapshot"]["input_usd_per_million"] = 0.1
    with pytest.raises(ValueError, match="定点十进制"):
        validate_comparison_spec(raw)
    raw = comparison_spec()
    raw["groups"][0]["source_revision"] = "abc123"
    with pytest.raises(ValueError, match="完整 commit ID"):
        validate_comparison_spec(raw)
