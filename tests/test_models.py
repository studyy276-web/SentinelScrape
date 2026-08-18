"""Tests for SentinelResponse shared data contract model."""

import json
from app.models.response import SentinelResponse

REQUIRED_CONTRACT_FIELDS = {
    "collector_id",
    "url",
    "schema",
    "extracted_data",
    "trust_score",
    "status",
    "failure_signature",
    "failed_fields",
    "healing_source",
    "compute_tier",
    "healing_attempts",
    "verification_result",
    "cost_ledger",
    "ai_answer",
}


def test_required_contract_fields_exist():
    """Verify all 14 required contract fields exist on the SentinelResponse model."""
    model_field_names = set(SentinelResponse.model_fields.keys())
    assert REQUIRED_CONTRACT_FIELDS.issubset(
        model_field_names
    ), f"Missing fields: {REQUIRED_CONTRACT_FIELDS - model_field_names}"
    assert model_field_names == REQUIRED_CONTRACT_FIELDS


def test_sentinel_response_default_instantiation():
    """Verify SentinelResponse can be instantiated with default values."""
    instance = SentinelResponse(url="https://example.com/item/123")
    assert instance.url == "https://example.com/item/123"
    assert instance.status == "pending"
    assert instance.trust_score == 0.0
    assert instance.failed_fields == []
    assert instance.cost_ledger == {}
    assert instance.healing_attempts == 0


def test_sentinel_response_full_instantiation():
    """Verify SentinelResponse can be instantiated with all custom values."""
    payload = {
        "collector_id": "c_12345",
        "url": "https://example.com/product/abc",
        "schema": {"title": "str", "price": "float"},
        "extracted_data": {"title": "Sample Item", "price": 49.99},
        "trust_score": 0.95,
        "status": "success",
        "failure_signature": None,
        "failed_fields": [],
        "healing_source": "primary_scrape",
        "compute_tier": "tier_1",
        "healing_attempts": 0,
        "verification_result": {"is_valid": True},
        "cost_ledger": {"tokens": 120, "estimated_usd": 0.0004},
        "ai_answer": "The item is priced at $49.99.",
    }
    instance = SentinelResponse(**payload)
    assert instance.collector_id == "c_12345"
    assert instance.trust_score == 0.95
    assert instance.extracted_data["title"] == "Sample Item"


def test_sentinel_response_dict_and_json_serialization():
    """Verify SentinelResponse serializes correctly to dict and JSON format."""
    instance = SentinelResponse(
        collector_id="col_test_99",
        url="https://example.com/data",
        schema={"name": "str"},
        extracted_data={"name": "Widget"},
        trust_score=0.88,
        status="healed",
        failure_signature="SIG_MISSING_PRICE",
        failed_fields=["price"],
        healing_source="gemini_fallback",
        compute_tier="tier_2",
        healing_attempts=1,
        verification_result={"passed": True},
        cost_ledger={"api_calls": 2},
        ai_answer="Widget found.",
    )

    data_dict = instance.model_dump()
    assert isinstance(data_dict, dict)
    assert set(data_dict.keys()) == REQUIRED_CONTRACT_FIELDS
    assert data_dict["collector_id"] == "col_test_99"
    assert data_dict["schema"] == {"name": "str"}
    assert data_dict["failed_fields"] == ["price"]

    json_str = instance.model_dump_json()
    assert isinstance(json_str, str)
    parsed_json = json.loads(json_str)
    assert set(parsed_json.keys()) == REQUIRED_CONTRACT_FIELDS
    assert parsed_json["trust_score"] == 0.88
    assert parsed_json["status"] == "healed"
