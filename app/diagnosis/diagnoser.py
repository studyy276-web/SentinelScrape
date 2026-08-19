"""SentinelDiagnoser implementation integrating deterministic failure classification with the state machine."""

import logging
from typing import Optional

from app.diagnosis.classifier import FailureClassification, FailureClassifier
from app.orchestrator.context import OrchestrationContext

logger = logging.getLogger(__name__)


class SentinelDiagnoser:
    """Deterministic failure diagnoser for the SentinelScrape orchestration pipeline.
    
    Evaluates failure signatures from verification/validation and assigns:
    - context.is_healable (True for schema/selector/type errors, False for anomalies/infrastructure/client errors)
    - context.failure_signature (deterministic signature)
    - context.metadata["diagnosis"] (structured diagnostic telemetry)
    """

    def __init__(self, classifier: Optional[FailureClassifier] = None):
        self.classifier = classifier or FailureClassifier()

    def diagnose(self, context: OrchestrationContext) -> OrchestrationContext:
        """Analyzes context failure state and determines healability for the state machine."""
        classification: FailureClassification = self.classifier.classify(
            failure_signature=context.failure_signature,
            failed_fields=context.failed_fields,
            verification_result=context.verification_result,
        )

        context.is_healable = classification.is_healable
        if not context.failure_signature:
            context.failure_signature = classification.primary_signature

        context.metadata["diagnosis"] = {
            "category": classification.category.value,
            "is_healable": classification.is_healable,
            "signatures": classification.all_signatures,
            "details": classification.details,
        }

        logger.info(
            "Diagnosed failure category=%s healable=%s signature=%s",
            classification.category.value,
            classification.is_healable,
            context.failure_signature,
        )
        return context
