"""Runbook retrieval for a correlated incident.

Three signals rank the indexed chunks:

  alert    how well a runbook's listed alerts agree with the incident's
  vector   embedding similarity to the incident described as text
  keyword  full-text match on the same text

Alert agreement orders the result; the two text signals, fused with reciprocal
rank fusion, order chunks with equal agreement and are all there is for an
alert no runbook lists. The first version fused all three as equals and the
golden set showed why not to: a one-place difference in the alert ranking is
worth 1/61 - 1/62, so the text signals overrode exact alert matches.

The index is passed in, so this module has no database or model dependency
and the ranking logic is testable on its own (see store.PgIndex for the real
one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SIGNALS = ("alert", "vector", "keyword")
RRF_K = 60  # the constant from the original RRF paper; damps the top ranks
CANDIDATES = 20  # how deep each ranking goes before fusion

# Below this cosine similarity, with no alert agreement either, the incident
# has no runbook. Set from the golden set (eval/run_eval.py prints the ranges):
# unrelated alerts scored up to 0.716 and covered-but-unlisted ones from 0.753,
# bar one at 0.676 that this deliberately gives up. Grounding a diagnosis in
# the wrong runbook is worse than offering none.
MIN_SIMILARITY = 0.73


@dataclass(frozen=True)
class Query:
    root: str
    alert_names: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    runbook_id: str
    section: str
    content: str
    score: float  # fused text score
    alert_agreement: float  # 0 when the runbook lists none of the incident's alerts
    ranks: dict[str, int]  # text signal -> rank in that signal's list, where present


@dataclass(frozen=True)
class Result:
    matched: bool
    hits: list[Hit] = field(default_factory=list)
    runbooks: list[str] = field(default_factory=list)  # ranked, best first
    best_similarity: float | None = None


def humanize(alert_name: str) -> str:
    """BoutiquePodsNotReady -> 'boutique pods not ready'."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", alert_name).lower()


def build_query(incident: dict) -> Query:
    """Describe a correlator incident as retrieval input.

    The incident carries no prose, only alert names and services, so the text
    is built from those: the root service's alerts first, each alert name in
    both its exact and its spelled-out form.
    """
    root = incident.get("probable_root_service") or "unknown"
    alerts = sorted(incident.get("alerts", []), key=lambda a: a.get("service") != root)
    names: list[str] = []
    phrases: list[str] = []
    for alert in alerts:
        name, service = alert["alertname"], alert.get("service", "")
        if name not in names:
            names.append(name)
        # Alerts with no service label get the alert name as a pseudo-service.
        where = f" on {service}" if service and service != name else ""
        phrase = f"{name} ({humanize(name)}){where}"
        if phrase not in phrases:
            phrases.append(phrase)
    return Query(root=root, alert_names=tuple(names), text="; ".join(phrases))


def fuse(rankings: dict[str, dict[str, int]]) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: score = sum over signals of 1 / (RRF_K + rank)."""
    scores: dict[str, float] = {}
    for ranks in rankings.values():
        for chunk_id, rank in ranks.items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def retrieve(
    incident: dict,
    index,
    k: int = 4,
    signals: tuple[str, ...] = SIGNALS,
    min_similarity: float = MIN_SIMILARITY,
) -> Result:
    query = build_query(incident)
    agreement: dict[str, float] = {}
    rankings: dict[str, dict[str, int]] = {}
    best_similarity = None

    if "alert" in signals:
        agreement = index.alert_agreement(query.alert_names, query.root)
    if "vector" in signals:
        rankings["vector"], best_similarity = index.vector_ranks(query.text, query.root, CANDIDATES)
    if "keyword" in signals:
        rankings["keyword"] = index.keyword_ranks(query.text, query.root, CANDIDATES)

    # A keyword hit alone is not evidence of a match: any incident shares a
    # word with some runbook. It only decides when it is the only signal asked for.
    evidence = bool(agreement)
    if best_similarity is not None:
        evidence = evidence or best_similarity >= min_similarity
    if signals == ("keyword",):
        evidence = bool(rankings["keyword"])
    if not evidence:
        return Result(matched=False, best_similarity=best_similarity)

    fused = dict(fuse(rankings))
    order = sorted(
        set(agreement) | set(fused),
        key=lambda cid: (-agreement.get(cid, 0.0), -fused.get(cid, 0.0), cid),
    )
    chunks = index.chunks(order)
    runbooks: list[str] = []
    hits: list[Hit] = []
    for chunk_id in order:
        chunk = chunks[chunk_id]
        if chunk["runbook_id"] not in runbooks:
            runbooks.append(chunk["runbook_id"])
        if len(hits) < k:
            hits.append(
                Hit(
                    chunk_id=chunk_id,
                    runbook_id=chunk["runbook_id"],
                    section=chunk["section"],
                    content=chunk["content"],
                    score=fused.get(chunk_id, 0.0),
                    alert_agreement=agreement.get(chunk_id, 0.0),
                    ranks={s: r[chunk_id] for s, r in rankings.items() if chunk_id in r},
                )
            )
    return Result(matched=True, hits=hits, runbooks=runbooks, best_similarity=best_similarity)
