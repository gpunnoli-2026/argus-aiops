"""Train per-service anomaly models on the aiops:svc:* Prometheus series.

One IsolationForest pipeline per service (plus a global fallback), bundled
into a single MLflow pyfunc model registered as `argus-anomaly` and promoted
via the `production` alias. Scores are calibrated to [0, 1] where higher =
more anomalous.

Runs in-cluster as a Job (see train-job.yaml) so it reaches Prometheus and
MLflow directly.
"""

import logging
import os
import time

import mlflow
import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("train")

PROM_URL = os.environ.get("PROM_URL", "http://monitoring-kube-prometheus-prometheus.monitoring:9090")
TRAIN_HOURS = float(os.environ.get("TRAIN_HOURS", "6"))
STEP_SECONDS = int(os.environ.get("STEP_SECONDS", "60"))
MIN_SAMPLES = int(os.environ.get("MIN_SAMPLES", "60"))
MODEL_NAME = os.environ.get("MODEL_NAME", "argus-anomaly")

FEATURES = ["cpu_rate", "mem_ws_bytes", "restarts_delta", "pods_not_ready"]


def prom_range(metric: str, start: float, end: float) -> pd.DataFrame:
    """Range query one recording rule -> long df [ts, service, value]."""
    r = requests.get(
        f"{PROM_URL}/api/v1/query_range",
        params={"query": f"aiops:svc:{metric}", "start": start, "end": end, "step": STEP_SECONDS},
        timeout=60,
    )
    r.raise_for_status()
    rows = []
    for series in r.json()["data"]["result"]:
        svc = series["metric"].get("pod_owner", "unknown")
        for ts, val in series["values"]:
            rows.append({"ts": float(ts), "service": svc, metric: float(val)})
    return pd.DataFrame(rows)


def build_matrix() -> pd.DataFrame:
    end = time.time()
    start = end - TRAIN_HOURS * 3600
    df = None
    for m in FEATURES:
        part = prom_range(m, start, end)
        if part.empty:
            part = pd.DataFrame(columns=["ts", "service", m])
        df = part if df is None else df.merge(part, on=["ts", "service"], how="outer")
    df = df.fillna(0.0)
    log.info("training matrix: %d rows, %d services", len(df), df["service"].nunique())
    return df


def fit_pipeline(x: pd.DataFrame) -> tuple[Pipeline, float, float]:
    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("iforest", IsolationForest(n_estimators=100, contamination=0.02, random_state=42)),
        ]
    )
    pipe.fit(x)
    # calibration bounds: decision_function is high=normal; invert to 0-1 anomaly score
    dec = pipe.decision_function(x)
    return pipe, float(dec.min()), float(dec.max())


class AnomalyBundle(mlflow.pyfunc.PythonModel):
    """Per-service pipelines + global fallback, calibrated to [0,1]."""

    def __init__(self, models: dict, calib: dict):
        self.models = models  # service -> Pipeline ("__global__" = fallback)
        self.calib = calib    # service -> (dec_min, dec_max)

    def _score(self, key: str, x: pd.DataFrame) -> np.ndarray:
        pipe = self.models[key]
        lo, hi = self.calib[key]
        dec = pipe.decision_function(x)
        rng = (hi - lo) or 1.0
        return np.clip((hi - dec) / rng, 0.0, 1.0)

    def predict(self, context, model_input: pd.DataFrame, params=None) -> np.ndarray:
        out = np.zeros(len(model_input))
        x = model_input[FEATURES]
        for i, svc in enumerate(model_input["service"].tolist()):
            key = svc if svc in self.models else "__global__"
            out[i] = self._score(key, x.iloc[[i]])[0]
        return out


def main() -> None:
    df = build_matrix()
    if df.empty:
        raise SystemExit("No training data — is the load generator running?")

    models, calib, per_service_samples = {}, {}, {}
    for svc, grp in df.groupby("service"):
        if len(grp) < MIN_SAMPLES:
            log.info("skip %s: only %d samples", svc, len(grp))
            continue
        models[svc], lo, hi = fit_pipeline(grp[FEATURES])
        calib[svc] = (lo, hi)
        per_service_samples[svc] = len(grp)

    models["__global__"], lo, hi = fit_pipeline(df[FEATURES])
    calib["__global__"] = (lo, hi)

    with mlflow.start_run(run_name="anomaly-train") as run:
        mlflow.log_params(
            {
                "train_hours": TRAIN_HOURS,
                "step_seconds": STEP_SECONDS,
                "features": ",".join(FEATURES),
                "contamination": 0.02,
            }
        )
        mlflow.log_metrics(
            {"rows": len(df), "services_modeled": len(models) - 1}
        )
        info = mlflow.pyfunc.log_model(
            artifact_path="model",
            python_model=AnomalyBundle(models, calib),
            registered_model_name=MODEL_NAME,
        )
        version = info.registered_model_version
        client = mlflow.MlflowClient()
        client.set_registered_model_alias(MODEL_NAME, "production", version)
        log.info("registered %s v%s and set @production (run %s)", MODEL_NAME, version, run.info.run_id)


if __name__ == "__main__":
    main()
