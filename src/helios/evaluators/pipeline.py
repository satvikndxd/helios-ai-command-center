from helios.evaluators.base import BaseEvaluator
from helios.models import DecisionTrace


class EvaluationPipeline:
    """
    Runs a list of evaluators against a trace and aggregates their results.

    A single evaluator raising must never sink the whole pipeline — its failure
    is captured as a failed result so the rest still run (fault isolation).
    """

    def __init__(self, evaluators: list[BaseEvaluator]):
        self.evaluators = evaluators

    def run(self, trace: DecisionTrace) -> dict[str, dict]:
        results: dict[str, dict] = {}
        for evaluator in self.evaluators:
            try:
                res = evaluator.evaluate(trace)
                results[evaluator.name] = {
                    "score": res.score,
                    "passed": res.passed,
                    "details": res.details,
                }
            except Exception as exc:  # noqa: BLE001 - fault isolation is intentional
                results[evaluator.name] = {
                    "score": 0.0,
                    "passed": False,
                    "details": {"error": str(exc)},
                }
        return results
