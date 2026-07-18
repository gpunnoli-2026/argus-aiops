"""
llm_diagnostic.py
-----------------
Argus LLM diagnostic layer. Takes the STRUCTURED incident emitted by the
deterministic correlation stage (root cause already decided by rules) and:
  1. retrieves the most relevant runbook via RAG,
  2. asks an LLM to write a root-cause narrative + draft a Jira ticket,
     grounded in that runbook.

Design guardrail:
  The LLM NEVER decides causality. Causality comes from the deterministic
  correlation layer. The LLM only EXPLAINS and DRAFTS, grounded in retrieved
  runbooks. That boundary is what makes it safe to put in an on-call path.

Runs offline out of the box:
  - retriever falls back to keyword scoring if sentence-transformers/chroma
    aren't installed,
  - LLM falls back to a deterministic stub if no ANTHROPIC_API_KEY is set.
Flip USE_REAL_EMBEDDINGS / USE_REAL_LLM to True (and install deps) for production.

Optional deps for production paths:
    pip install sentence-transformers chromadb anthropic
"""

from __future__ import annotations
import os
import json
import textwrap
from dataclasses import dataclass, field, asdict
from typing import List, Optional

USE_REAL_EMBEDDINGS = False   # True -> sentence-transformers + Chroma
USE_REAL_LLM = False          # True -> Anthropic API (needs ANTHROPIC_API_KEY)
LLM_MODEL = "claude-sonnet-5" # confirmed current model string (see shared/models.md)


# ---------------------------------------------------------------------------
# 1. The incident object the DETERMINISTIC layer hands us. Root cause is
#    already decided upstream; we only explain/draft from here.
# ---------------------------------------------------------------------------
@dataclass
class Incident:
    incident_id: str
    root_service: str                 # decided by correlation, NOT by the LLM
    affected_services: List[str]
    severity: str                     # e.g. "SEV-2"
    signals: List[str]                # the raw alerts folded into this incident
    dependency_edges: List[str]       # e.g. "checkout-service -> payment-service"
    started_at: str

    def signature(self) -> str:
        """Short text used to retrieve the matching runbook."""
        return f"{self.root_service}: " + "; ".join(self.signals[:4])


# ---------------------------------------------------------------------------
# 2. Runbook store with RAG retrieval. Two backends behind one interface.
# ---------------------------------------------------------------------------
@dataclass
class Runbook:
    name: str
    text: str


class RunbookStore:
    def __init__(self, runbooks: List[Runbook]):
        self.runbooks = runbooks
        self._backend = None
        if USE_REAL_EMBEDDINGS:
            self._init_vector_backend()

    def _init_vector_backend(self):
        # Production path: embed each runbook once, query by cosine similarity.
        from sentence_transformers import SentenceTransformer
        import chromadb

        self._model = SentenceTransformer("all-MiniLM-L6-v2")
        client = chromadb.Client()
        self._col = client.create_collection("argus_runbooks")
        self._col.add(
            ids=[rb.name for rb in self.runbooks],
            documents=[rb.text for rb in self.runbooks],
            embeddings=self._model.encode([rb.text for rb in self.runbooks]).tolist(),
        )
        self._backend = "vector"

    def retrieve(self, query: str, k: int = 1) -> List[Runbook]:
        if self._backend == "vector":
            q_emb = self._model.encode([query]).tolist()
            res = self._col.query(query_embeddings=q_emb, n_results=k)
            names = res["ids"][0]
            return [rb for name in names for rb in self.runbooks if rb.name == name]
        # Offline fallback: simple keyword overlap scoring.
        q_terms = {w.lower() for w in query.replace(":", " ").split()}
        scored = []
        for rb in self.runbooks:
            rb_terms = set(rb.text.lower().split())
            scored.append((len(q_terms & rb_terms), rb))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [rb for _, rb in scored[:k]]


# ---------------------------------------------------------------------------
# 3. The prompt. Incident + dependency graph + retrieved runbook go in;
#    a strict JSON object (narrative + ticket fields) comes out.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = textwrap.dedent("""
    You are an SRE incident diagnostic assistant. You are given a STRUCTURED
    incident whose root cause has already been determined by a deterministic
    correlation engine, plus the single most relevant runbook.

    Rules:
    - Do NOT re-derive or second-guess the root cause. Treat root_service as
      the established root. Affected services other than the root are downstream
      symptoms; say so explicitly.
    - Ground every remediation step in the provided runbook. Do NOT invent
      remediation steps that are not supported by the runbook; if the runbook
      is insufficient, say what additional info is needed.
    - Respond with ONLY a JSON object, no prose, no markdown fences, with keys:
      "narrative" (string), "ticket" (object with keys: "title", "severity",
      "root_service", "impacted_services" (array), "timeline" (string),
      "suggested_remediation" (array of strings), "runbook_used" (string)).
""").strip()


def build_user_message(incident: Incident, runbook: Runbook) -> str:
    return textwrap.dedent(f"""
        INCIDENT (structured, from correlation engine):
        {json.dumps(asdict(incident), indent=2)}

        DEPENDENCY GRAPH:
        {chr(10).join('  ' + e for e in incident.dependency_edges)}

        RETRIEVED RUNBOOK ({runbook.name}):
        {runbook.text}
    """).strip()


# ---------------------------------------------------------------------------
# 4. LLM call. Real Anthropic client OR an offline stub with the same signature.
# ---------------------------------------------------------------------------
def call_llm(system: str, user: str) -> str:
    if USE_REAL_LLM and os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=LLM_MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")
    return _stub_llm(user)


def _stub_llm(user: str) -> str:
    """Deterministic offline stand-in so the pipeline runs with no API key.
    Produces the same JSON shape the real model is instructed to return."""
    # crude parse of the structured incident back out of the prompt
    start = user.index("{")
    end = user.index("\n\n", start)
    inc = json.loads(user[start:end])
    root = inc["root_service"]
    downstream = [s for s in inc["affected_services"] if s != root]
    out = {
        "narrative": (
            f"At {inc['started_at']}, {root} is the root cause of incident "
            f"{inc['incident_id']} ({inc['severity']}). Downstream services "
            f"{', '.join(downstream) or '(none)'} are impacted via their "
            f"dependency on {root} and are symptoms, not independent faults. "
            f"Signals: {'; '.join(inc['signals'])}."
        ),
        "ticket": {
            "title": f"[{inc['severity']}] {root} — {inc['signals'][0]}",
            "severity": inc["severity"],
            "root_service": root,
            "impacted_services": downstream,
            "timeline": f"Onset {inc['started_at']}; {len(inc['signals'])} correlated signals.",
            "suggested_remediation": [
                "Follow the retrieved runbook (see runbook_used).",
                "Apply the runbook's interim mitigation, then confirm recovery.",
            ],
            "runbook_used": "(stub) top retrieved runbook",
        },
    }
    return json.dumps(out)


# ---------------------------------------------------------------------------
# 5. Orchestration: retrieve -> prompt -> parse.
# ---------------------------------------------------------------------------
def diagnose(incident: Incident, store: RunbookStore) -> dict:
    runbook = store.retrieve(incident.signature(), k=1)[0]
    user_msg = build_user_message(incident, runbook)
    raw = call_llm(SYSTEM_PROMPT, user_msg)
    try:
        result = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except json.JSONDecodeError:
        result = {"narrative": raw, "ticket": None, "parse_error": True}
    result.setdefault("ticket", {})
    if isinstance(result.get("ticket"), dict):
        result["ticket"]["runbook_used"] = runbook.name  # ground truth, not model's word
    return result


# ---------------------------------------------------------------------------
# Demo: the payment-service DB-pool-exhaustion incident.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    runbooks = [
        Runbook("runbook-db-pool-exhaustion.md", (
            "Symptom: database connection pool utilization at 100%, rising p99 "
            "latency and 5xx errors on the owning service. Common cause: a "
            "connection leak introduced by a recent deployment. Remediation: "
            "1) check deployments in the last 2 hours for the affected service; "
            "2) scale the connection pool as interim mitigation; "
            "3) if a recent deploy correlates, roll it back.")),
        Runbook("runbook-node-cpu-throttling.md",
            "Symptom: sustained CPU throttling on pods. Remediation: raise CPU "
            "limits or scale the deployment horizontally."),
        Runbook("runbook-dns-failure.md",
            "Symptom: resolution failures across services. Remediation: check "
            "CoreDNS pods and upstream resolvers."),
    ]
    store = RunbookStore(runbooks)

    incident = Incident(
        incident_id="INC-4821",
        root_service="payment-service",
        affected_services=["payment-service", "checkout-service"],
        severity="SEV-2",
        signals=[
            "payment-service DB connection pool at 100%",
            "payment-service p99 latency > 2s",
            "payment-service 5xx rate rising",
            "checkout-service latency rising",
            "checkout-service 5xx appearing",
        ],
        dependency_edges=["checkout-service -> payment-service",
                          "payment-service -> payments-db"],
        started_at="02:14 UTC",
    )

    result = diagnose(incident, store)
    print(json.dumps(result, indent=2))
