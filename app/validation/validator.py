"""Deterministic Sentinel Validator implementation with Trust Gate evaluation."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.orchestrator.context import OrchestrationContext
from app.validation.anomaly import AnomalyDetector, extract_item_count_from_data, extract_price_from_data
from app.validation.baseline import BaselineStore
from app.validation.rules import get_rule_validator

logger = logging.getLogger(__name__)


class SentinelValidator:
    """Deterministic validator evaluating schema compliance, trust scores, and historical anomalies."""

    def __init__(
        self,
        baseline_store: Optional[BaselineStore] = None,
        anomaly_detector: Optional[AnomalyDetector] = None,
        fail_on_anomaly: bool = True,
        min_pass_score: float = 100.0,
    ):
        self.baseline_store = baseline_store or BaselineStore()
        self.anomaly_detector = anomaly_detector or AnomalyDetector(fail_on_anomaly=fail_on_anomaly)
        self.min_pass_score = min_pass_score

    def parse_schema(self, schema: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Parses various machine-readable schema formats into a normalized dictionary.
        
        Normalized form:
        {
            "field_name": {
                "type": "string",
                "required": True,
                "rule": "string"
            }
        }
        """
        if not schema or not isinstance(schema, dict):
            return {}

        normalized: Dict[str, Dict[str, Any]] = {}
        
        # Format 1: Standard JSON schema / dictionary with "properties"
        if "properties" in schema and isinstance(schema["properties"], dict):
            global_required = set(schema.get("required", []))
            for field, spec in schema["properties"].items():
                if isinstance(spec, dict):
                    field_type = spec.get("type", "string")
                    is_req = spec.get("required", field in global_required) or (field in global_required)
                    normalized[field] = {
                        "type": field_type,
                        "required": bool(is_req),
                        "rule": spec.get("rule", field_type),
                    }
                elif isinstance(spec, str):
                    normalized[field] = {
                        "type": spec,
                        "required": field in global_required,
                        "rule": spec,
                    }
            return normalized

        # Format 2: Flat dictionary of field -> type or field -> spec
        global_required = set(schema.get("required", [])) if "required" in schema and isinstance(schema["required"], list) else set()
        for field, spec in schema.items():
            if field in ("required", "$schema") or (field == "type" and isinstance(spec, str) and spec in ("object", "array") and len(schema) > 1):
                continue
            if isinstance(spec, dict):
                field_type = spec.get("type", spec.get("rule", "string"))
                is_req = spec.get("required", field in global_required) or (field in global_required)
                normalized[field] = {
                    "type": field_type,
                    "required": bool(is_req),
                    "rule": spec.get("rule", field_type),
                }
            elif isinstance(spec, str):
                normalized[field] = {
                    "type": spec,
                    "required": (field in global_required) if global_required else True,
                    "rule": spec,
                }

        return normalized

    def validate_data(
        self,
        extracted_data: Any,
        schema: Optional[Dict[str, Any]],
        key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validates extracted data against schema and baseline history."""
        parsed_schema = self.parse_schema(schema)
        fields_expected = len(parsed_schema)

        # Edge case: No expected fields in schema
        if fields_expected == 0:
            return {
                "passed": False,
                "trust_score": 0.0,
                "fields_expected": 0,
                "fields_present": 0,
                "fields_valid": 0,
                "failed_fields": ["schema_empty"],
                "failure_signature": "NO_EXPECTED_FIELDS",
                "is_baseline": False,
                "anomalies": [],
                "details": {},
            }

        # Extracted data is None or non-dict
        if extracted_data is None or not isinstance(extracted_data, dict):
            failed_fields = list(parsed_schema.keys())
            missing_signatures = [f"MISSING_FIELD:{f}" for f in sorted(failed_fields)]
            return {
                "passed": False,
                "trust_score": 0.0,
                "fields_expected": fields_expected,
                "fields_present": 0,
                "fields_valid": 0,
                "failed_fields": failed_fields,
                "failure_signature": ", ".join(missing_signatures),
                "is_baseline": False,
                "anomalies": [],
                "details": {},
            }

        fields_present = 0
        fields_valid = 0
        failed_fields: List[str] = []
        failure_tags: List[str] = []
        field_details: Dict[str, Any] = {}

        for field_name, spec in parsed_schema.items():
            rule_name = spec["rule"]
            is_required = spec["required"]
            validator_fn = get_rule_validator(rule_name)

            if field_name in extracted_data and extracted_data[field_name] is not None:
                val = extracted_data[field_name]
                fields_present += 1
                is_val_valid, err_reason = validator_fn(val)

                if is_val_valid:
                    fields_valid += 1
                    field_details[field_name] = {"present": True, "valid": True, "value": val}
                else:
                    failed_fields.append(field_name)
                    tag = f"{err_reason or 'INVALID_FIELD'}:{field_name}"
                    failure_tags.append(tag)
                    field_details[field_name] = {
                        "present": True,
                        "valid": False,
                        "error": err_reason,
                        "value": val,
                    }
            else:
                # Field is missing
                field_details[field_name] = {"present": False, "valid": False, "required": is_required}
                if is_required:
                    failed_fields.append(field_name)
                    failure_tags.append(f"MISSING_FIELD:{field_name}")

        # Exact Trust Score formula:
        # Trust Score = (fields_present / fields_expected) * (fields_valid / fields_present) * 100
        if fields_present == 0:
            trust_score = 0.0
        else:
            trust_score = round(
                (fields_present / fields_expected) * (fields_valid / fields_present) * 100.0,
                2,
            )

        # Baseline & Anomaly evaluation
        is_baseline = False
        anomalies: List[str] = []
        structural_passed = (
            len(failed_fields) == 0
            and trust_score >= self.min_pass_score
            and fields_valid == fields_expected
        )

        passed = structural_passed

        if key:
            baseline = self.baseline_store.get_baseline(key)
            if structural_passed and baseline is None:
                # First-ever verified extraction becomes baseline
                is_baseline = True
                curr_price = extract_price_from_data(extracted_data)
                curr_count = extract_item_count_from_data(extracted_data)
                self.baseline_store.save_baseline(
                    key=key,
                    data=extracted_data,
                    price=curr_price,
                    item_count=curr_count,
                )
            elif baseline is not None:
                # Check for historical anomalies
                anomalies = self.anomaly_detector.check_anomalies(
                    current_data=extracted_data,
                    baseline=baseline,
                )
                if anomalies:
                    if self.anomaly_detector.fail_on_anomaly:
                        passed = False
                        for anomaly in anomalies:
                            # Extract clean tag for failure_signature
                            clean_tag = anomaly.split(" ")[0] if " " in anomaly else anomaly
                            failure_tags.append(clean_tag)
                            failed_fields.append(clean_tag.lower())
                elif structural_passed:
                    # Update baseline with latest verified data
                    curr_price = extract_price_from_data(extracted_data)
                    curr_count = extract_item_count_from_data(extracted_data)
                    self.baseline_store.save_baseline(
                        key=key,
                        data=extracted_data,
                        price=curr_price,
                        item_count=curr_count,
                    )

        # Combined deterministic failure signature
        failure_signature: Optional[str] = None
        if failure_tags:
            # Sort tags deterministically
            failure_signature = ", ".join(sorted(set(failure_tags)))

        return {
            "passed": bool(passed),
            "trust_score": trust_score,
            "fields_expected": fields_expected,
            "fields_present": fields_present,
            "fields_valid": fields_valid,
            "failed_fields": sorted(list(set(failed_fields))),
            "failure_signature": failure_signature,
            "is_baseline": is_baseline,
            "anomalies": anomalies,
            "details": field_details,
        }

    def validate(self, context: OrchestrationContext) -> OrchestrationContext:
        """Protocol-compliant validate method for orchestrator pipeline."""
        result = self.validate_data(
            extracted_data=context.extracted_data,
            schema=context.schema,
            key=context.url or context.collector_id,
        )

        context.verification_result = result
        context.trust_score = result["trust_score"]
        context.failed_fields = result["failed_fields"]
        if result["failure_signature"]:
            if context.failure_signature:
                existing_sigs = [s.strip() for s in context.failure_signature.split(",") if s.strip()]
                new_sigs = [s.strip() for s in result["failure_signature"].split(",") if s.strip()]
                combined = sorted(set(existing_sigs + new_sigs))
                context.failure_signature = ", ".join(combined)
            else:
                context.failure_signature = result["failure_signature"]

        return context
