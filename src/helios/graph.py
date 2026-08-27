"""
Knowledge graph MVP (Phase 4 walking skeleton).

Heuristic entity extraction at ingestion time:
- Capitalized multi-word phrases become entities.
- Type inference by keyword (Policy, Service, Product, Person-ish, Term).
- Every entity gets a `mentioned_in` relationship to its source document
  (provenance, FR-KG-008) with a confidence score.

Deliberately NOT an NLP pipeline — it establishes the graph schema, dedup,
and provenance mechanics so a real extractor (NER model / LLM) can swap in
behind `extract_entities()` later.
"""

import re

from sqlalchemy.orm import Session

from helios.models import Entity, Relationship


# Two or more Capitalized Words in sequence ("Refund Policy", "Payment API").
_PHRASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)+)\b")

_TYPE_KEYWORDS = [
    ("Policy", ("policy", "regulation", "gdpr", "compliance", "addendum")),
    ("Service", ("api", "service", "cluster", "database", "gateway")),
    ("Product", ("product", "plan", "tier", "edition")),
    ("Incident", ("incident", "outage", "failure")),
]


def infer_type(name: str) -> str:
    lowered = name.lower()
    for entity_type, keywords in _TYPE_KEYWORDS:
        if any(k in lowered for k in keywords):
            return entity_type
    return "Term"


_LEADING_ARTICLES = ("The ", "A ", "An ", "Our ", "This ", "That ")


def extract_entities(text: str) -> list[tuple[str, str]]:
    """Return deduplicated (name, type) pairs found in text."""
    seen: dict[str, str] = {}
    for match in _PHRASE_RE.findall(text or ""):
        name = match.strip()
        # Sentence-initial articles get capitalized and captured; strip them
        # so "The Refund Policy" and "Refund Policy" resolve to one entity.
        for article in _LEADING_ARTICLES:
            if name.startswith(article):
                name = name[len(article):]
                break
        if " " not in name:
            continue  # single word left after stripping — too weak a signal
        if 3 <= len(name) <= 120 and name.lower() not in seen:
            seen[name.lower()] = name
    return [(name, infer_type(name)) for name in seen.values()]


def upsert_entities_for_document(
    db: Session,
    *,
    tenant_id: str,
    document_id: str,
    text: str,
    confidence: float = 0.5,
) -> int:
    """
    Extract entities from a document and link them with provenance.

    Dedup (FR-KG-007): entities are matched case-insensitively per tenant;
    re-mentions add a relationship, not a duplicate entity.
    Returns the number of entities linked.
    """
    pairs = extract_entities(text)
    if not pairs:
        return 0

    linked = 0
    for name, entity_type in pairs:
        entity = (
            db.query(Entity)
            .filter(
                Entity.tenant_id == tenant_id,
                Entity.name.ilike(name),
            )
            .first()
        )
        if entity is None:
            entity = Entity(
                tenant_id=tenant_id,
                type=entity_type,
                name=name,
                confidence=confidence,
            )
            db.add(entity)
            db.flush()

        exists = (
            db.query(Relationship)
            .filter(
                Relationship.tenant_id == tenant_id,
                Relationship.source_entity_id == entity.id,
                Relationship.document_id == document_id,
                Relationship.relationship_type == "mentioned_in",
            )
            .first()
        )
        if exists is None:
            db.add(
                Relationship(
                    tenant_id=tenant_id,
                    source_entity_id=entity.id,
                    relationship_type="mentioned_in",
                    document_id=document_id,
                    confidence=confidence,
                )
            )
            linked += 1
    return linked


def get_or_create_entity(
    db: Session,
    *,
    tenant_id: str,
    name: str,
    entity_type: str,
    confidence: float = 0.9,
) -> Entity:
    """Dedup-aware entity upsert (case-insensitive per tenant)."""
    entity = (
        db.query(Entity)
        .filter(Entity.tenant_id == tenant_id, Entity.name.ilike(name))
        .first()
    )
    if entity is None:
        entity = Entity(
            tenant_id=tenant_id, type=entity_type, name=name, confidence=confidence
        )
        db.add(entity)
        db.flush()
    return entity


def link_entities(
    db: Session,
    *,
    tenant_id: str,
    source: tuple[str, str],
    relationship_type: str,
    target: tuple[str, str],
    document_id: str | None = None,
    confidence: float = 0.9,
) -> Relationship:
    """
    Domain relationship between two entities (workflow layer), e.g.
    ("TestRun-104", "TestRun") -[involves]-> ("BM-2000", "BatteryModule").

    `document_id` preserves provenance: which document/source asserts this
    relationship.  Idempotent: an existing identical edge is returned as-is.
    """
    src = get_or_create_entity(
        db, tenant_id=tenant_id, name=source[0], entity_type=source[1],
        confidence=confidence,
    )
    dst = get_or_create_entity(
        db, tenant_id=tenant_id, name=target[0], entity_type=target[1],
        confidence=confidence,
    )
    existing = (
        db.query(Relationship)
        .filter(
            Relationship.tenant_id == tenant_id,
            Relationship.source_entity_id == src.id,
            Relationship.target_entity_id == dst.id,
            Relationship.relationship_type == relationship_type,
        )
        .first()
    )
    if existing:
        return existing
    edge = Relationship(
        tenant_id=tenant_id,
        source_entity_id=src.id,
        target_entity_id=dst.id,
        relationship_type=relationship_type,
        document_id=document_id,
        confidence=confidence,
    )
    db.add(edge)
    db.flush()
    return edge
