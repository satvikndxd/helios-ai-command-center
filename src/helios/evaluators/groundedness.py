"""
Claim-level groundedness / hallucination-risk evaluator (FR-HD-001/006).

Deterministic Phase-1-scale implementation:
1. Split the output into sentences (claims).
2. For each claim, compute content-word overlap against every retrieved chunk
   stored on the trace (request_payload.retrieved_context[].content).
3. groundedness = supported_claims / total_claims
4. hallucination_risk = 1 - groundedness (+0.2 if citations are absent).

LLM-as-judge entailment slots in behind this same evaluator name later —
the pipeline and worker never change.
"""

import re

from helios.evaluators.base import BaseEvaluator, EvalResult
from helios.models import DecisionTrace

_SENT_RE = re.compile(r"[.!?]\s+|\n+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have i if in into is it its
    may must not of on or our so that the their this to was we what when which
    who will with you your only say so use following context answer question
    reference sources their number insufficient""".split()
)


def _content_words(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS}


def _claims(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.split(text) if len(s.strip()) >= 15]


class GroundednessEvaluator(BaseEvaluator):
    name = "groundedness"

    def __init__(self, support_threshold: float = 0.3, risk_threshold: float = 0.5):
        self.support_threshold = support_threshold
        self.risk_threshold = risk_threshold

    def evaluate(self, trace: DecisionTrace) -> EvalResult:
        context = (trace.request_payload or {}).get("retrieved_context") or []
        chunk_words = [_content_words(c.get("content", "")) for c in context]
        chunk_words = [w for w in chunk_words if w]

        if not chunk_words:
            # Ungrounded request (no KB used): not applicable, don't penalize.
            return EvalResult(
                evaluator=self.name,
                score=1.0,
                passed=True,
                details={"skipped": "no retrieved context on trace"},
            )

        claims = _claims(trace.output_text or "")
        if not claims:
            return EvalResult(
                evaluator=self.name,
                score=0.0,
                passed=False,
                details={"reason": "no claims extractable from output"},
            )

        supported = 0
        unsupported: list[str] = []
        for claim in claims:
            words = _content_words(claim)
            if not words:
                supported += 1
                continue
            best = max(len(words & cw) / len(words) for cw in chunk_words)
            if best >= self.support_threshold:
                supported += 1
            else:
                unsupported.append(claim[:120])

        groundedness = supported / len(claims)
        hallucination_risk = round(
            min(1.0, (1.0 - groundedness) + (0.2 if not trace.citations else 0.0)), 4
        )

        return EvalResult(
            evaluator=self.name,
            score=round(groundedness, 4),
            passed=hallucination_risk < self.risk_threshold,
            details={
                "claims": len(claims),
                "supported": supported,
                "unsupported_examples": unsupported[:3],
                "hallucination_risk": hallucination_risk,
            },
        )
