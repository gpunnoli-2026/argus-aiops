"""Argus capacity forecaster.

Every FORECAST_INTERVAL_SECONDS: pull recent node-level utilization series from
Prometheus, fit a Prophet model per (resource, instance), project HORIZON_HOURS
ahead, and publish `aiops_forecast_hours_to_threshold` — how many hours until
the resource first crosses THRESHOLD (sentinel 999 = no crossing in horizon).
A PrometheusRule turns low values into predictive alerts.
"""

import logging
import os
import threading
import time

import pandas as pd
import requests
from fastapi import FastAPI
from prometheus_client import Gauge, make_asgi_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)
log = logging.getLogger("forecaster")

PROM_URL = os.environ.get("PROM_URL", "http://localhost:9090")
INTERVAL = int(os.environ.get("FORECAST_INTERVAL_SECONDS", "900"))
HISTORY_HOURS = float(os.environ.get("HISTORY_HOURS", "12"))
HORIZON_HOURS = float(os.environ.get("HORIZON_HOURS", "12"))
THRESHOLD = float(os.environ.get("THRESHOLD", "0.8"))
STEP = 300  # 5m resolution
NO_CROSSING = 999.0

QUERIES = {
    "node_cpu": '1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m]))',
    "node_mem": "1 - avg by (instance) (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
    "node_disk": '1 - avg by (instance) (node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"} '
    '/ node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"})',
}

HOURS = Gauge(
    "aiops_forecast_hours_to_threshold",
    f"Forecast hours until utilization crosses {THRESHOLD:.0%} (999 = no crossing in horizon)",
    ["resource", "instance"],
)
LAST_RUN = Gauge("aiops_forecaster_last_run_timestamp", "Unix time of last successful forecast loop")
FIT_ERRORS = Gauge("aiops_forecaster_fit_errors", "Series that failed to fit in the last loop")

app = FastAPI(title="argus-capacity-forecaster")
app.mount("/metrics", make_asgi_app())

_state: dict = {"forecasts": {}}


def prom_range(query: str) -> dict[str, pd.DataFrame]:
    end = time.time()
    r = requests.get(
        f"{PROM_URL}/api/v1/query_range",
        params={"query": query, "start": end - HISTORY_HOURS * 3600, "end": end, "step": STEP},
        timeout=60,
    )
    r.raise_for_status()
    out = {}
    for series in r.json()["data"]["result"]:
        inst = series["metric"].get("instance", "unknown")
        df = pd.DataFrame(series["values"], columns=["ds", "y"])
        df["ds"] = pd.to_datetime(df["ds"].astype(float), unit="s")
        df["y"] = df["y"].astype(float)
        out[inst] = df
    return out


def hours_to_threshold(df: pd.DataFrame) -> float | None:
    """Fit Prophet, project the horizon, return hours until first crossing."""
    from prophet import Prophet  # heavy import, keep local

    if len(df) < 24 or df["y"].isna().all():
        return None
    if df["y"].iloc[-1] >= THRESHOLD:
        return 0.0
    m = Prophet(
        daily_seasonality=True,
        weekly_seasonality=False,
        yearly_seasonality=False,
        changepoint_prior_scale=0.1,
    )
    m.fit(df)
    future = m.make_future_dataframe(periods=int(HORIZON_HOURS * 3600 / STEP), freq=f"{STEP}s")
    fc = m.predict(future)
    now = df["ds"].max()
    ahead = fc[fc["ds"] > now]
    crossing = ahead[ahead["yhat"] >= THRESHOLD]
    if crossing.empty:
        return None
    return (crossing["ds"].iloc[0] - now).total_seconds() / 3600


def _loop():
    while True:
        errors = 0
        try:
            snapshot = {}
            for resource, query in QUERIES.items():
                for inst, df in prom_range(query).items():
                    try:
                        h = hours_to_threshold(df)
                    except Exception:  # noqa: BLE001
                        errors += 1
                        log.exception("fit failed for %s/%s", resource, inst)
                        continue
                    value = NO_CROSSING if h is None else round(h, 2)
                    HOURS.labels(resource=resource, instance=inst).set(value)
                    snapshot[f"{resource}/{inst}"] = {
                        "hours_to_threshold": value,
                        "current": round(float(df["y"].iloc[-1]), 4),
                    }
            _state["forecasts"] = snapshot
            LAST_RUN.set(time.time())
            log.info("forecast loop done: %d series, %d errors", len(snapshot), errors)
        except Exception:  # noqa: BLE001
            log.exception("forecast loop failure")
        FIT_ERRORS.set(errors)
        time.sleep(INTERVAL)


@app.get("/healthz")
def healthz():
    return {"ok": True, "series": len(_state["forecasts"])}


@app.get("/forecasts")
def forecasts():
    return _state["forecasts"]


threading.Thread(target=_loop, daemon=True).start()
