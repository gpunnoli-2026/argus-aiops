"""Argus anomaly detector.

Every SCORE_INTERVAL_SECONDS: pull the aiops:svc:* feature series from
Prometheus, score each boutique service with the model loaded from the MLflow
registry, and expose scores as the Prometheus gauge aiops_anomaly_score.
Prometheus alerting rules turn high scores into alerts (see observability/rules).
"""

import logging
import os
import threading
import time

import mlflow
import pandas as pd
import requests
from fastapi import FastAPI
from prometheus_client import Gauge, make_asgi_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("detector")

PROM_URL = os.environ.get("PROM_URL", "http://localhost:9090")
MODEL_URI = os.environ.get("MODEL_URI", "models:/argus-anomaly@production")
SCORE_INTERVAL = int(os.environ.get("SCORE_INTERVAL_SECONDS", "30"))
MODEL_REFRESH = int(os.environ.get("MODEL_REFRESH_SECONDS", "300"))

FEATURES = ["cpu_rate", "mem_ws_bytes", "restarts_delta", "pods_not_ready"]

SCORE = Gauge("aiops_anomaly_score", "Anomaly score 0-1 per service", ["service"])
SCORING_ERRORS = Gauge("aiops_detector_scoring_errors", "Consecutive scoring loop failures")
MODEL_LOADED = Gauge("aiops_detector_model_loaded", "1 if a model is loaded from the registry")
LAST_RUN = Gauge("aiops_detector_last_run_timestamp", "Unix time of last successful scoring loop")

app = FastAPI(title="argus-anomaly-detector")
app.mount("/metrics", make_asgi_app())

_state: dict = {"model": None, "model_ts": 0.0, "scores": {}}


def _prom_feature(metric: str) -> dict[str, float]:
    """Instant query for one aiops:svc:* recording rule -> {service: value}."""
    r = requests.get(
        f"{PROM_URL}/api/v1/query", params={"query": f"aiops:svc:{metric}"}, timeout=10
    )
    r.raise_for_status()
    out = {}
    for item in r.json()["data"]["result"]:
        svc = item["metric"].get("pod_owner", "unknown")
        out[svc] = float(item["value"][1])
    return out


def _load_model():
    try:
        model = mlflow.pyfunc.load_model(MODEL_URI)
        _state["model"] = model
        _state["model_ts"] = time.time()
        MODEL_LOADED.set(1)
        log.info("loaded model from %s", MODEL_URI)
    except Exception as exc:  # noqa: BLE001 — registry may simply be empty pre-first-train
        MODEL_LOADED.set(0)
        log.warning("no model available from %s: %s", MODEL_URI, exc)


def _score_once():
    feats = {m: _prom_feature(m) for m in FEATURES}
    services = sorted(set().union(*[set(v) for v in feats.values()]))
    if not services:
        log.info("no services found in Prometheus yet")
        return
    df = pd.DataFrame(
        [{"service": s, **{m: feats[m].get(s, 0.0) for m in FEATURES}} for s in services]
    )
    model = _state["model"]
    if model is None:
        return
    scores = model.predict(df)
    for svc, sc in zip(services, scores):
        SCORE.labels(service=svc).set(float(sc))
        _state["scores"][svc] = float(sc)
    LAST_RUN.set(time.time())


def _loop():
    errors = 0
    while True:
        try:
            if time.time() - _state["model_ts"] > MODEL_REFRESH:
                _load_model()
            _score_once()
            errors = 0
        except Exception:  # noqa: BLE001
            errors += 1
            log.exception("scoring loop failure #%d", errors)
        SCORING_ERRORS.set(errors)
        time.sleep(SCORE_INTERVAL)


@app.get("/healthz")
def healthz():
    return {"ok": True, "model_loaded": _state["model"] is not None}


@app.get("/scores")
def scores():
    return _state["scores"]


threading.Thread(target=_loop, daemon=True).start()
