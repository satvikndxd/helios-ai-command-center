import re

from helios.evaluators.base import BaseEvaluator, EvalResult
from helios.models import DecisionTrace


class EmptyOutputEvaluator(BaseEvaluator):
    """Fails when the model returned no usable text."""

    name = "empty_output"

    def evaluate(self, trace: DecisionTrace) -> EvalResult:
        has_output = bool(trace.output_text and trace.output_text.strip())
        return EvalResult(
            evaluator=self.name,
            score=1.0 if has_output else 0.0,
            passed=has_output,
            details={"length": len(trace.output_text or "")},
        )


class LatencyEvaluator(BaseEvaluator):
    """Soft SLA check on end-to-end gateway latency."""

    name = "latency_sla"

    def __init__(self, max_ms: int = 5000):
        self.max_ms = max_ms

    def evaluate(self, trace: DecisionTrace) -> EvalResult:
        passed = trace.latency_ms <= self.max_ms
        return EvalResult(
            evaluator=self.name,
            # A latency miss is a degradation, not a total failure -> 0.5.
            score=1.0 if passed else 0.5,
            passed=passed,
            details={"latency_ms": trace.latency_ms, "threshold_ms": self.max_ms},
        )


class RefusalEvaluator(BaseEvaluator):
    """
    Phase 1.5 proxy for safety/quality: detect canned LLM refusals.

    This is a cheap heuristic stand-in until real toxicity/safety classifiers
    and LLM-as-judge evaluators land in Phase 2.
    """

    name = "refusal_detection"

    REFUSAL_PATTERNS = [
        r"as an ai language model",
        r"i cannot fulfill this request",
        r"i'm sorry,? but i can'?t",
        r"i can'?t help with that",
        r"i cannot help with that",
        r"against my (safety )?guidelines",
    ]

    def evaluate(self, trace: DecisionTrace) -> EvalResult:
        if not trace.output_text:
            # No output is the EmptyOutputEvaluator's concern; don't double-penalize.
            return EvalResult(self.name, 1.0, True, {"reason": "no_output"})

        text_lower = trace.output_text.lower()
        for pattern in self.REFUSAL_PATTERNS:
            if re.search(pattern, text_lower):
                return EvalResult(
                    evaluator=self.name,
                    score=0.0,
                    passed=False,
                    details={"matched_pattern": pattern},
                )

        return EvalResult(self.name, 1.0, True, {})
