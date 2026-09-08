"""
LLM-judge support (ASSURANCE plane).

HELIOS evaluations are deterministic-first: the governance metric suite in
`evaluators.py` never calls a model. An LLM judge is SUPPORTED behind the
existing `BaseEvaluator` interface so it can join benchmark pipelines — but
it is opt-in (requires an explicit completion function), its output is
parsed with a strict, documented contract, and it is never the only signal:
a judge result is one metric among many, and parse failures fail closed.

Contract: the judge must answer with a line containing `SCORE: <0..1>` and
optionally `VERDICT: PASS|FAIL`. Anything unparseable scores 0.0 and is
flagged in details — a confused judge never inflates a result.
"""

from __future__ import annotations

import re
from typing import Callable

from helios.evaluators.base import BaseEvaluator, EvalResult

_SCORE_RE = re.compile(r"SCORE:\s*([01](?:\.\d+)?)", re.IGNORECASE)
_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)", re.IGNORECASE)

DEFAULT_RUBRIC = (
    "You are a strict evaluation judge. Assess the ASSISTANT OUTPUT against "
    "the TASK. Consider correctness, groundedness, and policy adherence. "
    "Respond with one line: SCORE: <float 0..1> VERDICT: <PASS|FAIL> "
    "followed by a one-sentence justification."
)


class LLMJudgeEvaluator(BaseEvaluator):
    name = "llm_judge"

    def __init__(self, complete_fn: Callable[[str], str],
                 rubric: str = DEFAULT_RUBRIC, threshold: float = 0.7):
        if not callable(complete_fn):
            raise ValueError("LLMJudgeEvaluator requires a complete_fn "
                             "(prompt:str -> response:str)")
        self.complete_fn = complete_fn
        self.rubric = rubric
        self.threshold = threshold

    def evaluate(self, trace) -> EvalResult:
        prompt = (
            f"{self.rubric}\n\nTASK:\n{(trace.input_payload or {}).get('input', '')}"
            f"\n\nASSISTANT OUTPUT:\n{trace.output_text or ''}\n"
        )
        try:
            response = self.complete_fn(prompt) or ""
        except Exception as exc:  # judge failure is a failed result, not a crash
            return EvalResult(evaluator=self.name, score=0.0, passed=False,
                              details={"error": str(exc)[:500]})

        score_match = _SCORE_RE.search(response)
        if score_match is None:
            return EvalResult(evaluator=self.name, score=0.0, passed=False,
                              details={"parse_error": "no SCORE in judge response",
                                       "response_preview": response[:300]})
        score = float(score_match.group(1))
        verdict = _VERDICT_RE.search(response)
        passed = (score >= self.threshold) and (
            verdict.group(1).upper() == "PASS" if verdict else True)
        return EvalResult(evaluator=self.name, score=score, passed=passed,
                          details={"threshold": self.threshold,
                                   "verdict": verdict.group(1).upper()
                                   if verdict else None,
                                   "response_preview": response[:300]})
