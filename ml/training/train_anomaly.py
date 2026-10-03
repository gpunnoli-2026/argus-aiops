"""Train per-service anomaly models on the aiops:svc:* Prometheus series.

One range envelope per service (plus a global fallback), bundled into a single
MLflow pyfunc model registered as `argus-anomaly`: the trained range of each
feature and a tolerance, scored by how far outside that range a sample sits.
It replaced an IsolationForest, which scored memory 0.3 MiB past its training
range the same as CPU at seven times it — ml/evaluation/compare_models.py
reruns that comparison on a recorded cluster run. Promotion to
the `production` alias is gated: the new bundle's background alarm rate on the
training window must stay under GATE_MAX_ALARM_RATE and must not regress
against the current production model. Scores are calibrated to [0, 1] where
higher = more anomalous.

`--rollback` flips the production alias back to the previous registered
version instead of training.

Runs in-cluster as a Job (see train-job.yaml) so it reaches Prometheus and
MLflow directly.
"""

import logging
import os
import sys
import time

import mlflow
import numpy as np
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("train")

PROM_URL = os.environ.get("PROM_URL", "http://monitoring-kube-prometheus-prometheus.monitoring:9090")
TRAIN_HOURS = float(os.environ.get("TRAIN_HOURS", "6"))
STEP_SECONDS = int(os.environ.get("STEP_SECONDS", "60"))
MIN_SAMPLES = int(os.environ.get("MIN_SAMPLES", "60"))
MODEL_NAME = os.environ.get("MODEL_NAME", "argus-anomaly")
ALERT_THRESHOLD = 0.8  # must match ServiceAnomalyDetected in observability/rules
GATE_MAX_ALARM_RATE = float(os.environ.get("GATE_MAX_ALARM_RATE", "0.05"))
GATE_REGRESSION_MARGIN = float(os.environ.get("GATE_REGRESSION_MARGIN", "0.02"))
REQUIRE_PRODUCTION_BASELINE = os.environ.get("REQUIRE_PRODUCTION_BASELINE", "").lower() == "true"

WARMUP_MINUTES = float(os.environ.get("WARMUP_MINUTES", "15"))

FEATURES = ["cpu_rate", "mem_ws_bytes", "restarts_delta", "pods_not_ready"]

# Range envelope (see fit_envelope). A feature's tolerance is the largest of
# RANGE_TOLERANCE x its trained range, RANGE_TOLERANCE x its median, and an
# absolute floor for features that barely move. RANGE_ALERT_DISTANCE
# tolerances outside the range is where the score reaches ALERT_THRESHOLD.
RANGE_TOLERANCE = float(os.environ.get("RANGE_TOLERANCE", "0.25"))
RANGE_ALERT_DISTANCE = float(os.environ.get("RANGE_ALERT_DISTANCE", "2.0"))
RANGE_FLOOR = {
    "cpu_rate": 0.010,  # 10 millicores
    "mem_ws_bytes": 8 * 2**20,  # 8 MiB
    # under 0.5, so one restart or one unready pod is already past the alert distance
    "restarts_delta": 0.4,
    "pods_not_ready": 0.4,
}

# Newest pod start per service, keyed like the aiops:svc:* rules. Not a feature,
# so it is queried raw rather than given a recording rule of its own.
POD_START_QUERY = (
    'max by (pod_owner) (label_replace(kube_pod_start_time{namespace="boutique"}, '
    '"pod_owner", "$1", "pod", "^(.*)-[a-z0-9]+-[a-z0-9]+$"))'
)


def prom_range(metric: str, start: float, end: float, query: str | None = None) -> pd.DataFrame:
    """Range query one recording rule (or a raw `query`) -> long df [ts, service, value]."""
    r = requests.get(
        f"{PROM_URL}/api/v1/query_range",
        params={"query": query or f"aiops:svc:{metric}", "start": start, "end": end, "step": STEP_SECONDS},
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
    rows = len(df)
    df = drop_warmup(df, prom_range("pod_start", start, end, query=POD_START_QUERY))
    log.info(
        "training matrix: %d rows, %d services (%d warm-up rows dropped)",
        len(df), df["service"].nunique(), rows - len(df),
    )
    return df


def drop_warmup(df: pd.DataFrame, starts: pd.DataFrame) -> pd.DataFrame:
    """Drop samples taken within WARMUP_MINUTES of a pod start in that service.

    A pod's first minutes (memory still climbing) are not its steady state, and
    would stretch the bottom of the trained memory range. Samples with no known
    pod start are kept.
    """
    if df.empty or starts.empty or WARMUP_MINUTES <= 0:
        return df
    age = df.merge(starts, on=["ts", "service"], how="left")
    age = age["ts"] - age["pod_start"]
    return df[~(age < WARMUP_MINUTES * 60).to_numpy()].reset_index(drop=True)


def fit_envelope(x: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Trained range per feature (0.5th-99.5th percentile) and its tolerance."""
    lo, hi = x.quantile(0.005).to_numpy(), x.quantile(0.995).to_numpy()
    floor = np.array([RANGE_FLOOR[f] for f in x.columns])
    tol = np.maximum.reduce([RANGE_TOLERANCE * (hi - lo), RANGE_TOLERANCE * np.abs(x.median().to_numpy()), floor])
    return lo, hi, tol


class AnomalyBundle(mlflow.pyfunc.PythonModel):
    """Per-service range envelopes + global fallback, scored to [0,1].

    0 inside the trained range; ALERT_THRESHOLD at RANGE_ALERT_DISTANCE
    tolerances outside it on the worst feature; approaching 1 beyond that.
    """

    def __init__(self, envelope: dict):
        self.envelope = envelope  # service -> (lo, hi, tol) ("__global__" = fallback)

    def _score(self, key: str, x: pd.DataFrame) -> np.ndarray:
        lo, hi, tol = self.envelope[key]
        v = x.to_numpy(dtype=float)
        dist = np.maximum((v - hi) / tol, (lo - v) / tol).max(axis=1).clip(min=0.0)
        return 1.0 - (1.0 - ALERT_THRESHOLD) ** (dist / RANGE_ALERT_DISTANCE)

    def predict(self, context, model_input: pd.DataFrame, params=None) -> np.ndarray:
        out = np.zeros(len(model_input))
        x = model_input[FEATURES]
        for i, svc in enumerate(model_input["service"].tolist()):
            key = svc if svc in self.envelope else "__global__"
            out[i] = self._score(key, x.iloc[[i]])[0]
        return out


def alarm_rate(bundle: "AnomalyBundle | object", df: pd.DataFrame) -> float:
    """Fraction of the (assumed mostly-normal) window scoring above the alert
    threshold — a proxy for the false-alert noise this model would produce."""
    scores = bundle.predict(None, df) if isinstance(bundle, AnomalyBundle) else bundle.predict(df)
    return float((np.asarray(scores) > ALERT_THRESHOLD).mean())


def promotion_gate(bundle: AnomalyBundle, df: pd.DataFrame) -> tuple[bool, dict]:
    """New model must be quiet on its own training window and no noisier than
    the current production model on that same window."""
    new_rate = alarm_rate(bundle, df)
    metrics = {"gate_new_alarm_rate": new_rate}
    if new_rate > GATE_MAX_ALARM_RATE:
        return False, metrics
    try:
        prod = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@production")
    except Exception:
        return True, metrics
    prod_rate = alarm_rate(prod, df)
    metrics["gate_prod_alarm_rate"] = prod_rate
    return new_rate <= prod_rate + GATE_REGRESSION_MARGIN, metrics


def rollback() -> None:
    client = mlflow.MlflowClient()
    current = int(client.get_model_version_by_alias(MODEL_NAME, "production").version)
    versions = sorted(
        (int(v.version) for v in client.search_model_versions(f"name='{MODEL_NAME}'")),
        reverse=True,
    )
    previous = next((v for v in versions if v < current), None)
    if previous is None:
        raise SystemExit(f"no version older than v{current} to roll back to")
    client.set_registered_model_alias(MODEL_NAME, "production", previous)
    log.info("rolled back %s @production: v%s -> v%s", MODEL_NAME, current, previous)


def has_production_model() -> bool:
    try:
        mlflow.MlflowClient().get_model_version_by_alias(MODEL_NAME, "production")
        return True
    except mlflow.exceptions.MlflowException as e:
        log.warning("no %s@production: %s", MODEL_NAME, e)
        return False


def main() -> None:
    # The nightly CronJob only refreshes an existing production model. With
    # nothing to compare against the gate passes anything: on a new cluster it
    # promoted a model trained on idle traffic 24 minutes before the first load
    # test, which then raised 39 false incidents. The first model is a
    # deliberate `make train`.
    if REQUIRE_PRODUCTION_BASELINE and not has_production_model():
        log.info("skipping: no production model to refresh yet — run `make train` once traffic has baked")
        return

    df = build_matrix()
    if df.empty:
        raise SystemExit("No training data — is the load generator running?")

    envelope = {}
    for svc, grp in df.groupby("service"):
        if len(grp) < MIN_SAMPLES:
            log.info("skip %s: only %d samples", svc, len(grp))
            continue
        envelope[svc] = fit_envelope(grp[FEATURES])

    envelope["__global__"] = fit_envelope(df[FEATURES])

    bundle = AnomalyBundle(envelope)
    promote, gate_metrics = promotion_gate(bundle, df)

    with mlflow.start_run(run_name="anomaly-train") as run:
        mlflow.log_params(
            {
                "train_hours": TRAIN_HOURS,
                "step_seconds": STEP_SECONDS,
                "features": ",".join(FEATURES),
                "warmup_minutes": WARMUP_MINUTES,
                "range_tolerance": RANGE_TOLERANCE,
                "range_alert_distance": RANGE_ALERT_DISTANCE,
                "gate_max_alarm_rate": GATE_MAX_ALARM_RATE,
            }
        )
        mlflow.log_metrics(
            {"rows": len(df), "services_modeled": len(envelope) - 1, **gate_metrics}
        )
        info = mlflow.pyfunc.log_model(
            artifact_path="model",
            python_model=bundle,
            registered_model_name=MODEL_NAME,
        )
        version = info.registered_model_version
        if promote:
            client = mlflow.MlflowClient()
            client.set_registered_model_alias(MODEL_NAME, "production", version)
            log.info(
                "registered %s v%s and set @production (run %s)", MODEL_NAME, version, run.info.run_id
            )
        else:
            log.error(
                "gate FAILED (%s): registered %s v%s but NOT promoted — production keeps its current model",
                gate_metrics, MODEL_NAME, version,
            )
            raise SystemExit(1)


if __name__ == "__main__":
    rollback() if "--rollback" in sys.argv else main()
