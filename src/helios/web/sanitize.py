"""
Content sanitization — every worker result passes through here before it
returns to the orchestrator.

Three jobs:

1. **Trust labeling** — force `trust=untrusted_external_content` on every
   document, no matter what an adapter (or a poisoned upstream response)
   claims.  The model sees evidence, not instructions.
2. **Injection detection** — scan pages, transcripts, posts, and MCP
   resources for prompt-injection patterns using the same Sentinel patterns
   that guard RAG.  Poisoned content is flagged (and its content withheld
   from prompt-eligible fields) rather than silently passed on.
3. **Secret scrubbing** — credential-shaped strings (API keys, bearer
   tokens, cookies) never travel in content, warnings, logs, or traces.
"""

from __future__ import annotations

import re

from helios.sentinel import detect_injection
from helios.web.types import UNTRUSTED, SourceDocument

# Credential-shaped material that must never survive sanitization.
_SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(p)
    for p in [
        r"sk-[A-Za-z0-9_\-]{16,}",             # OpenAI-style keys
        r"gsk_[A-Za-z0-9_\-]{16,}",            # Groq
        r"hf_[A-Za-z0-9]{16,}",                # Hugging Face
        r"ghp_[A-Za-z0-9]{16,}",               # GitHub PAT
        r"github_pat_[A-Za-z0-9_]{22,}",       # GitHub fine-grained PAT
        r"xox[baprs]-[A-Za-z0-9\-]{10,}",      # Slack
        r"AKIA[0-9A-Z]{16}",                   # AWS access key id
        r"(?i)bearer\s+[A-Za-z0-9\-_\.=]{20,}",
        r"(?i)(set-)?cookie:\s*\S+",
        r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}",  # JWT
    ]
]

REDACTION = "[REDACTED-SECRET]"


def scrub_secrets(text: str) -> tuple[str, int]:
    """Replace credential-shaped substrings; return (clean_text, count)."""
    count = 0
    for pattern in _SECRET_PATTERNS:
        text, n = pattern.subn(REDACTION, text or "")
        count += n
    return text, count


def sanitize_document(doc: SourceDocument) -> SourceDocument:
    """
    Sanitize one normalized document in place-of (returns a new model).

    - trust label is forced to UNTRUSTED (adapters cannot upgrade trust)
    - secrets are scrubbed from content and title
    - injection patterns flag the document; its content is quarantined so a
      poisoned page cannot ride into prompt assembly
    """
    warnings = list(doc.warnings)

    content, secret_hits = scrub_secrets(doc.content)
    title, title_hits = scrub_secrets(doc.title or "")
    if secret_hits + title_hits:
        warnings.append(f"secrets_redacted={secret_hits + title_hits}")

    injected = detect_injection(content) + detect_injection(title)
    if injected:
        warnings.append("injection_detected")
        content = (
            "[content withheld: prompt-injection patterns detected in "
            "retrieved external content]"
        )

    return doc.model_copy(
        update={
            "content": content,
            "title": title or None,
            "trust": UNTRUSTED,  # forced, always
            "warnings": warnings,
        }
    )


def sanitize_documents(docs: list[SourceDocument]) -> list[SourceDocument]:
    return [sanitize_document(d) for d in docs]
