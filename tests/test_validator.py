"""Unit and integration tests for Sentinel Validator, Trust Gate, and Baseline Anomaly Detection."""

import pytest
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState
from app.orchestrator.interfaces import StubAIService
from app.validation.anomaly import AnomalyDetector
from app.validation.baseline import BaselineStore
from app.validation.rules import (
    validate_availability,
    validate_boolean,
    validate_integer,
    validate_non_empty_string,
    validate_number,
    validate_price,
    validate_rating,
    validate_string,
)
from app.validation.validator import SentinelValidator


class TestSentinelValidator:
    """Test suite covering the 20 required validation scenarios."""

    # 1. All fields present and valid -> 100 score
    def test_all_fields_present_and_valid_yields_100_score(self):
        validator = SentinelValidator()
        schema = {
            "title": {"type": "string", "required": True},
            "price": {"type": "price", "required": True},
            "rating": {"type": "rating", "required": True},
        }
        data = {
            "title": "Ergonomic Mechanical Keyboard",
            "price": "$129.99",
            "rating": 4.8,
        }

        result = validator.validate_data(data, schema)
        assert result["trust_score"] == 100.0
        assert result["passed"] is True
        assert result["fields_expected"] == 3
        assert result["fields_present"] == 3
        assert result["fields_valid"] == 3
        assert result["failed_fields"] == []
        assert result["failure_signature"] is None

    # 2. One expected field missing
    def test_one_expected_field_missing(self):
        validator = SentinelValidator()
        schema = {
            "title": {"type": "string", "required": False},
            "price": {"type": "price", "required": False},
            "rating": {"type": "rating", "required": False},
            "brand": {"type": "string", "required": False},
        }
        # 3 out of 4 present and valid
        data = {
            "title": "Wireless Mouse",
            "price": "$49.99",
            "rating": 4.5,
        }

        result = validator.validate_data(data, schema)
        # (3/4) * (3/3) * 100 = 75.0
        assert result["trust_score"] == 75.0
        assert result["fields_expected"] == 4
        assert result["fields_present"] == 3
        assert result["fields_valid"] == 3

    # 3. One field invalid
    def test_one_field_invalid(self):
        validator = SentinelValidator()
        schema = {
            "title": {"type": "string", "required": True},
            "price": {"type": "price", "required": True},
            "rating": {"type": "rating", "required": True},
            "in_stock": {"type": "boolean", "required": True},
        }
        # 4 present, 3 valid, rating is invalid (> 5.0)
        data = {
            "title": "Monitor Stand",
            "price": "$39.99",
            "rating": "invalid_rating_value",
            "in_stock": True,
        }

        result = validator.validate_data(data, schema)
        # (4/4) * (3/4) * 100 = 75.0
        assert result["trust_score"] == 75.0
        assert result["passed"] is False
        assert "rating" in result["failed_fields"]
        assert "INVALID_TYPE:rating" in result["failure_signature"]

    # 4. Missing required field
    def test_missing_required_field(self):
        validator = SentinelValidator()
        schema = {
            "properties": {
                "title": {"type": "string", "required": True},
                "price": {"type": "price", "required": True},
            },
            "required": ["price"],
        }
        data = {"title": "Desk Lamp"}  # price is missing

        result = validator.validate_data(data, schema)
        assert result["passed"] is False
        assert "price" in result["failed_fields"]
        assert result["failure_signature"] == "MISSING_FIELD:price"

    # 5. Empty string
    def test_empty_string_fails_non_empty_string_rule(self):
        validator = SentinelValidator()
        schema = {"product_name": {"type": "non-empty string", "required": True}}
        data = {"product_name": "   "}

        result = validator.validate_data(data, schema)
        assert result["passed"] is False
        assert "product_name" in result["failed_fields"]
        assert result["failure_signature"] == "EMPTY_FIELD:product_name"

    # 6. Invalid number
    def test_invalid_number(self):
        validator = SentinelValidator()
        schema = {"stock_count": {"type": "number", "required": True}}
        data = {"stock_count": "not_a_number"}

        result = validator.validate_data(data, schema)
        assert result["passed"] is False
        assert "stock_count" in result["failed_fields"]
        assert "INVALID_TYPE:stock_count" in result["failure_signature"]

    # 7. Invalid rating
    def test_invalid_rating_out_of_bounds(self):
        validator = SentinelValidator()
        schema = {"rating": {"type": "rating", "required": True}}
        data = {"rating": 9.5}  # standard rating out of [0, 5]

        result = validator.validate_data(data, schema)
        assert result["passed"] is False
        assert "rating" in result["failed_fields"]
        assert "INVALID_FIELD:rating" in result["failure_signature"]

    # 8. Invalid price
    def test_invalid_price(self):
        validator = SentinelValidator()
        schema = {"price": {"type": "price", "required": True}}
        data = {"price": "Free / Contact Us"}

        result = validator.validate_data(data, schema)
        assert result["passed"] is False
        assert "price" in result["failed_fields"]
        assert "INVALID_FIELD:price" in result["failure_signature"]

    # 9. Invalid boolean
    def test_invalid_boolean(self):
        validator = SentinelValidator()
        schema = {"is_available": {"type": "boolean", "required": True}}
        data = {"is_available": "maybe"}

        result = validator.validate_data(data, schema)
        assert result["passed"] is False
        assert "is_available" in result["failed_fields"]
        assert "INVALID_TYPE:is_available" in result["failure_signature"]

    # 10. Zero expected fields
    def test_zero_expected_fields(self):
        validator = SentinelValidator()
        result = validator.validate_data(extracted_data={"item": "Test"}, schema={})
        assert result["passed"] is False
        assert result["trust_score"] == 0.0
        assert result["fields_expected"] == 0
        assert result["failure_signature"] == "NO_EXPECTED_FIELDS"

    # 11. Zero present fields
    def test_zero_present_fields(self):
        validator = SentinelValidator()
        schema = {
            "title": {"type": "string", "required": True},
            "price": {"type": "price", "required": True},
        }
        result = validator.validate_data(extracted_data={}, schema=schema)
        assert result["passed"] is False
        assert result["trust_score"] == 0.0
        assert result["fields_present"] == 0
        assert result["fields_expected"] == 2
        assert "MISSING_FIELD:price" in result["failure_signature"]
        assert "MISSING_FIELD:title" in result["failure_signature"]

    # 12. Multiple failures
    def test_multiple_failures(self):
        validator = SentinelValidator()
        schema = {
            "title": {"type": "non-empty string", "required": True},
            "price": {"type": "price", "required": True},
            "rating": {"type": "rating", "required": True},
        }
        data = {
            "title": "",  # EMPTY_FIELD
            "price": "not_price",  # INVALID_FIELD
            # rating missing -> MISSING_FIELD
        }

        result = validator.validate_data(data, schema)
        assert result["passed"] is False
        assert len(result["failed_fields"]) == 3
        assert "EMPTY_FIELD:title" in result["failure_signature"]
        assert "INVALID_FIELD:price" in result["failure_signature"]
        assert "MISSING_FIELD:rating" in result["failure_signature"]

    # 13. Deterministic failure_signature
    def test_deterministic_failure_signature(self):
        validator = SentinelValidator()
        schema = {
            "z_field": {"type": "price", "required": True},
            "a_field": {"type": "non-empty string", "required": True},
            "m_field": {"type": "rating", "required": True},
        }
        data = {
            "z_field": "invalid",
            "a_field": "",
            "m_field": "bad",
        }

        result1 = validator.validate_data(data, schema)
        result2 = validator.validate_data(data, schema)

        assert result1["failure_signature"] == result2["failure_signature"]
        # Alphabetically ordered deterministic combined tags
        expected_sig = "EMPTY_FIELD:a_field, INVALID_FIELD:z_field, INVALID_TYPE:m_field"
        assert result1["failure_signature"] == expected_sig

    # 14. First run becomes baseline
    def test_first_run_becomes_baseline(self):
        store = BaselineStore()
        validator = SentinelValidator(baseline_store=store)
        schema = {"title": "string", "price": "price"}
        data = {"title": "Noise-Cancelling Headphones", "price": 199.99}

        result = validator.validate_data(data, schema, key="https://example.com/headphones")
        assert result["passed"] is True
        assert result["is_baseline"] is True
        assert store.has_baseline("https://example.com/headphones") is True

    # 15. First run does not trigger historical anomaly
    def test_first_run_does_not_trigger_historical_anomaly(self):
        store = BaselineStore()
        detector = AnomalyDetector()
        validator = SentinelValidator(baseline_store=store, anomaly_detector=detector)
        schema = {"title": "string", "price": "price"}
        data = {"title": "High End GPU", "price": 1499.00}

        result = validator.validate_data(data, schema, key="https://example.com/gpu")
        assert result["passed"] is True
        assert result["anomalies"] == []
        assert result["is_baseline"] is True

    # 16. Normal historical result passes
    def test_normal_historical_result_passes(self):
        store = BaselineStore()
        validator = SentinelValidator(baseline_store=store)
        schema = {"title": "string", "price": "price"}
        key = "https://example.com/item"

        # Baseline: price = 100
        validator.validate_data({"title": "Sneakers", "price": 100.0}, schema, key=key)

        # Normal subsequent extraction: price = 105 (+5% change, well under 50% threshold)
        result2 = validator.validate_data({"title": "Sneakers", "price": 105.0}, schema, key=key)
        assert result2["passed"] is True
        assert result2["is_baseline"] is False
        assert result2["anomalies"] == []

    # 17. Suspicious price jump fails
    def test_suspicious_price_jump_fails(self):
        store = BaselineStore()
        validator = SentinelValidator(baseline_store=store)
        schema = {"title": "string", "price": "price"}
        key = "https://example.com/laptop"

        # Baseline: price = 100
        validator.validate_data({"title": "Laptop", "price": 100.0}, schema, key=key)

        # Subsequent extraction: price = 190 (+90% jump > 50% threshold)
        result = validator.validate_data({"title": "Laptop", "price": 190.0}, schema, key=key)
        assert result["passed"] is False
        assert len(result["anomalies"]) > 0
        assert "ANOMALY:PRICE_JUMP" in result["failure_signature"]

    # 18. Suspicious product-count collapse fails
    def test_suspicious_product_count_collapse_fails(self):
        store = BaselineStore()
        validator = SentinelValidator(baseline_store=store)
        schema = {"products": "list"}
        key = "https://example.com/catalog"

        # Baseline: 100 items
        baseline_data = {"products": [f"Item {i}" for i in range(100)]}
        validator.validate_data(baseline_data, schema, key=key)

        # Subsequent extraction: 10 items (90% drop > 50% threshold)
        collapsed_data = {"products": [f"Item {i}" for i in range(10)]}
        result = validator.validate_data(collapsed_data, schema, key=key)

        assert result["passed"] is False
        assert len(result["anomalies"]) > 0
        assert "ANOMALY:COUNT_COLLAPSE" in result["failure_signature"]

    # 19. Verification result structure is correct
    def test_verification_result_structure_is_correct(self):
        validator = SentinelValidator()
        schema = {"title": "string", "price": "price"}
        data = {"title": "Coffee Maker", "price": "$79.99"}

        result = validator.validate_data(data, schema)
        required_keys = {
            "passed",
            "trust_score",
            "fields_expected",
            "fields_present",
            "fields_valid",
            "failed_fields",
            "failure_signature",
            "is_baseline",
            "anomalies",
            "details",
        }
        assert required_keys.issubset(result.keys())
        assert isinstance(result["passed"], bool)
        assert isinstance(result["trust_score"], float)
        assert isinstance(result["fields_expected"], int)
        assert isinstance(result["fields_present"], int)
        assert isinstance(result["fields_valid"], int)
        assert isinstance(result["failed_fields"], list)

    # 20. Unverified result cannot reach AI_READY in state machine integration
    def test_unverified_result_cannot_reach_ai_ready(self):
        class BadDataCollector:
            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                context.extracted_data = {"title": "", "price": "invalid_price"}
                return context

        validator = SentinelValidator()
        ai_service = StubAIService()
        orchestrator = SentinelOrchestrator(
            collector=BadDataCollector(),
            validator=validator,
            ai_service=ai_service,
        )

        ctx = OrchestrationContext(
            url="https://example.com/unverified-item",
            schema={"title": "non-empty string", "price": "price"},
        )
        result = orchestrator.run(ctx)

        assert result.status != OrchestratorState.AI_READY
        assert result.is_verified() is False
        assert result.ai_answer is None
        assert ai_service.calls == 0

    # Additional rule tests for completeness
    def test_individual_rule_validators(self):
        assert validate_string("abc")[0] is True
        assert validate_string(123)[0] is False

        assert validate_non_empty_string("hello")[0] is True
        assert validate_non_empty_string("   ")[0] is False

        assert validate_number(42)[0] is True
        assert validate_number("42.5")[0] is True
        assert validate_number("bad")[0] is False

        assert validate_integer(10)[0] is True
        assert validate_integer("10")[0] is True
        assert validate_integer("10.5")[0] is False

        assert validate_boolean(True)[0] is True
        assert validate_boolean("yes")[0] is True
        assert validate_boolean("random")[0] is False

        assert validate_price("$19.99")[0] is True
        assert validate_price(-5)[0] is False
        assert validate_price("free")[0] is False

        assert validate_rating(4.5)[0] is True
        assert validate_rating(6.0)[0] is False
        assert validate_rating("bad")[0] is False

        assert validate_availability("in_stock")[0] is True
        assert validate_availability("")[0] is False
