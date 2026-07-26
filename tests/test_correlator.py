import time


def _alert(alertname, service=None, severity="warning", status="firing"):
    labels = {"alertname": alertname, "severity": severity}
    if service:
        labels["service"] = service
    return {"status": status, "labels": labels}


def test_service_of_prefers_service_label(correlator):
    assert correlator._service_of(_alert("X", service="cartservice")) == "cartservice"
    assert correlator._service_of(_alert("KubeNodeNotReady")) == "KubeNodeNotReady"


def test_infer_root_noisy_neighbor(correlator):
    # cartservice's own dependency (redis-cart) is healthy while its dependents
    # alert too -> cartservice is the origin
    assert correlator._infer_root({"cartservice", "frontend", "checkoutservice"}) == "cartservice"


def test_infer_root_ignores_infra_pseudo_services(correlator):
    assert correlator._infer_root({"cartservice", "KubeNodeNotReady"}) == "cartservice"


def test_alerts_within_window_fold_into_one_incident(correlator):
    correlator._fold_alert(_alert("HighCPU", service="cartservice"))
    correlator._fold_alert(_alert("MLAnomaly", service="frontend"))
    assert len(correlator._incidents) == 1
    inc = correlator._incidents[0]
    assert inc["services"] == {"cartservice", "frontend"}
    assert inc["probable_root_service"] == "cartservice"


def test_watchdog_is_dropped(correlator):
    correlator._fold_alert(_alert("Watchdog"))
    assert correlator._incidents == []


def test_severity_escalates_to_max_seen(correlator):
    correlator._fold_alert(_alert("A", service="cartservice", severity="warning"))
    correlator._fold_alert(_alert("B", service="cartservice", severity="critical"))
    assert correlator._incidents[0]["severity"] == "critical"


def test_late_alert_rejoins_its_own_incident_not_the_newest(correlator):
    now = time.time()
    older = {
        "incident_id": "inc-a", "created_at": now - 60, "status": "open",
        "alerts": [], "services": {"cartservice"}, "resolved_alerts": set(),
        "severity": "warning", "probable_root_service": "cartservice",
        "last_activity": now - 30,
    }
    newer = {
        "incident_id": "inc-b", "created_at": now - 10, "status": "open",
        "alerts": [], "services": {"paymentservice"}, "resolved_alerts": set(),
        "severity": "warning", "probable_root_service": "paymentservice",
        "last_activity": now - 5,
    }
    correlator._incidents.extend([older, newer])
    assert correlator._active_incident("cartservice") is older
    # topology neighbour of cartservice also lands in the older incident
    assert correlator._active_incident("redis-cart") is older
    # unrelated service falls back to the most recent open incident
    assert correlator._active_incident("adservice") is newer


def test_resolved_incident_is_not_reused(correlator):
    correlator._fold_alert(_alert("HighCPU", service="cartservice"))
    correlator._incidents[0]["status"] = "resolved"
    correlator._fold_alert(_alert("HighCPU", service="cartservice"))
    assert len(correlator._incidents) == 2


def test_all_alerts_resolved_closes_incident(correlator):
    correlator._fold_alert(_alert("HighCPU", service="cartservice"))
    correlator._fold_alert(_alert("HighCPU", service="cartservice", status="resolved"))
    assert correlator._incidents[0]["status"] == "resolved"
