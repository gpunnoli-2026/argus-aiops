import json

import pytest


@pytest.fixture(scope="module")
def store(llm_module):
    m = llm_module
    return m.RunbookStore(
        [
            m.Runbook(
                "runbook-db-pool-exhaustion.md",
                "Symptom: database connection pool utilization at 100%, rising p99 "
                "latency and 5xx errors. Remediation: check recent deployments; "
                "scale the connection pool; roll back if a deploy correlates.",
            ),
            m.Runbook(
                "runbook-node-cpu-throttling.md",
                "Symptom: sustained CPU throttling on pods. Remediation: raise CPU "
                "limits or scale the deployment horizontally.",
            ),
            m.Runbook(
                "runbook-dns-failure.md",
                "Symptom: resolution failures across services. Remediation: check "
                "CoreDNS pods and upstream resolvers.",
            ),
        ]
    )


@pytest.fixture
def incident(llm_module):
    return llm_module.Incident(
        incident_id="INC-1",
        root_service="payment-service",
        affected_services=["payment-service", "checkout-service"],
        severity="SEV-2",
        signals=[
            "payment-service DB connection pool at 100%",
            "payment-service p99 latency > 2s",
            "checkout-service 5xx appearing",
        ],
        dependency_edges=["checkout-service -> payment-service"],
        started_at="02:14 UTC",
    )


def test_retrieval_matches_symptom_keywords(store, incident):
    top = store.retrieve(incident.signature(), k=1)[0]
    assert top.name == "runbook-db-pool-exhaustion.md"


def test_stub_llm_returns_valid_json_shape(llm_module, incident, store):
    runbook = store.retrieve(incident.signature(), k=1)[0]
    raw = llm_module.call_llm(llm_module.SYSTEM_PROMPT, llm_module.build_user_message(incident, runbook))
    out = json.loads(raw)
    assert set(out) == {"narrative", "ticket"}
    assert out["ticket"]["root_service"] == "payment-service"


def test_diagnose_marks_downstream_as_symptoms_not_roots(llm_module, incident, store):
    result = llm_module.diagnose(incident, store)
    assert result["ticket"]["root_service"] == "payment-service"
    assert result["ticket"]["impacted_services"] == ["checkout-service"]


def test_runbook_used_is_ground_truth_not_model_output(llm_module, incident, store):
    result = llm_module.diagnose(incident, store)
    assert result["ticket"]["runbook_used"] == "runbook-db-pool-exhaustion.md"


def test_unparseable_llm_output_degrades_gracefully(llm_module, incident, store, monkeypatch):
    monkeypatch.setattr(llm_module, "call_llm", lambda s, u: "sorry, I ramble instead of JSON")
    result = llm_module.diagnose(incident, store)
    assert result["parse_error"] is True
    assert result["narrative"]  # raw text preserved for a human to read
