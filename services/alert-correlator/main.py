"""Argus alert correlator.

Receives Alertmanager webhooks and groups alerts arriving within a sliding
time window into single incidents, using the service-dependency topology of
Online Boutique to guess the root-cause service: among alerted services, the
ones whose own dependencies are healthy are the likely origin of the failure.

Phase 4 forwards incidents to the orchestrator/Slack; for now they are
queryable at /incidents and exported as metrics.
"""

import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from prometheus_client import Counter, Gauge, make_asgi_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("correlator")

WINDOW_SECONDS = int(os.environ.get("CORRELATION_WINDOW_SECONDS", "300"))
MAX_INCIDENTS = 200

# Online Boutique service dependency map: service -> services it calls
TOPOLOGY = {
    "frontend": ["cartservice", "productcatalogservice", "currencyservice",
                 "recommendationservice", "shippingservice", "checkoutservice", "adservice"],
    "checkoutservice": ["paymentservice", "emailservice", "shippingservice",
                        "currencyservice", "cartservice", "productcatalogservice"],
    "recommendationservice": ["productcatalogservice"],
    "cartservice": ["redis-cart"],
    "loadgenerator": ["frontend"],
}

SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1, "none": 0}

ALERTS_RECEIVED = Counter("aiops_correlator_alerts_received_total", "Raw alerts received", ["status"])
INCIDENTS_CREATED = Counter("aiops_correlator_incidents_created_total", "Incidents created")
OPEN_INCIDENTS = Gauge("aiops_correlator_open_incidents", "Currently open incidents")

app = FastAPI(title="argus-alert-correlator")
app.mount("/metrics", make_asgi_app())

_incidents: list[dict] = []


def _service_of(alert: dict) -> str:
    labels = alert.get("labels", {})
    for key in ("service", "exported_service", "pod_owner"):
        v = labels.get(key)
        if v and v not in ("anomaly-detector",):
            return v
    return labels.get("alertname", "unknown")


# services that participate in topology inference (deps + dependents)
KNOWN_SERVICES = set(TOPOLOGY) | {d for deps in TOPOLOGY.values() for d in deps}


def _infer_root(services: set[str]) -> str:
    """Alerted services whose own dependencies are all healthy = failure origin.

    Only known app services participate — infra alerts (no service label) fall
    back to pseudo-service names and must not pollute root-cause inference."""
    services = {s for s in services if s in KNOWN_SERVICES} or services
    candidates = []
    for svc in services:
        deps = TOPOLOGY.get(svc, [])
        if not any(d in services for d in deps):
            candidates.append(svc)
    if not candidates:
        return sorted(services)[0] if services else "unknown"
    # prefer the deepest candidate (most transitive dependents alerted)
    def dependents(svc: str) -> int:
        return sum(1 for s, deps in TOPOLOGY.items() if svc in deps and s in services)
    return sorted(candidates, key=dependents, reverse=True)[0]


def _active_incident() -> dict | None:
    if _incidents:
        inc = _incidents[-1]
        if inc["status"] == "open" and time.time() - inc["last_activity"] < WINDOW_SECONDS:
            return inc
    return None


def _fold_alert(alert: dict) -> None:
    status = alert.get("status", "firing")
    ALERTS_RECEIVED.labels(status=status).inc()
    name = alert.get("labels", {}).get("alertname", "unknown")
    if name == "Watchdog":
        return
    svc = _service_of(alert)
    severity = alert.get("labels", {}).get("severity", "none")

    if status == "resolved":
        for inc in reversed(_incidents):
            if name in {a["alertname"] for a in inc["alerts"]} and inc["status"] == "open":
                inc["resolved_alerts"].add(name)
                if inc["resolved_alerts"] >= {a["alertname"] for a in inc["alerts"]}:
                    inc["status"] = "resolved"
                break
        return

    inc = _active_incident()
    if inc is None:
        inc = {
            "incident_id": f"inc-{uuid.uuid4().hex[:8]}",
            "created_at": time.time(),
            "status": "open",
            "alerts": [],
            "services": set(),
            "resolved_alerts": set(),
            "severity": "none",
            "probable_root_service": None,
            "last_activity": time.time(),
        }
        _incidents.append(inc)
        del _incidents[:-MAX_INCIDENTS]
        INCIDENTS_CREATED.inc()
        log.info("opened incident %s", inc["incident_id"])

    inc["alerts"].append(
        {"alertname": name, "service": svc, "severity": severity, "source": alert.get("labels", {}).get("source", "")}
    )
    inc["services"].add(svc)
    if SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(inc["severity"], 0):
        inc["severity"] = severity
    inc["probable_root_service"] = _infer_root(inc["services"])
    inc["last_activity"] = time.time()
    log.info(
        "incident %s: %d alerts, services=%s, root=%s",
        inc["incident_id"], len(inc["alerts"]), sorted(inc["services"]), inc["probable_root_service"],
    )


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    for alert in payload.get("alerts", []):
        _fold_alert(alert)
    OPEN_INCIDENTS.set(sum(1 for i in _incidents if i["status"] == "open"))
    return {"ok": True}


@app.get("/incidents")
def incidents():
    return [
        {**inc, "services": sorted(inc["services"]), "resolved_alerts": sorted(inc["resolved_alerts"])}
        for inc in reversed(_incidents)
    ]


@app.get("/healthz")
def healthz():
    return {"ok": True, "incidents": len(_incidents)}
