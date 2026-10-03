"""Compare anomaly models on a recorded cluster run.

    python ml/evaluation/compare_models.py ml/evaluation/data/<run>.json

A run file is what export_run.py writes, plus a "windows" block naming the
training cut-off, a stretch of normal traffic, an injected fault and backtest
splits (all epoch seconds). Each model is trained the way production trains
(60s samples, pod warm-up dropped) and judged by the production alert rule:
score > 0.8 for 2 minutes.

  envelope     the production model (train_anomaly.AnomalyBundle)
  forest       the IsolationForest it replaced
  mahalanobis  robust covariance (MCD) distance
  residual     distance from each feature's own rolling median

Columns: false alerts on normal traffic (live window, then each backtest),
seconds to detect the fault (+ other services alerting during it), services
on which a container pinned at 200m CPU alerts, and whether each real restart
in the run alerts.
"""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.covariance import MinCovDet
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

_spec = importlib.util.spec_from_file_location(
    "train_anomaly", Path(__file__).resolve().parents[1] / "training" / "train_anomaly.py"
)
train = importlib.util.module_from_spec(_spec)
sys.modules["train_anomaly"] = train
_spec.loader.exec_module(train)

F = train.FEATURES
FLOOR = np.array([train.RANGE_FLOOR[f] for f in F])
RES = [f"res_{f}" for f in F]
STEP = 30  # seconds between exported samples
HOLD = 120 // STEP  # consecutive samples above threshold before the alert fires


def load(path: str, residual_minutes: int = 60) -> tuple[pd.DataFrame, dict]:
    run = json.load(open(path))
    parts = []
    for f in [*F, "pod_start"]:
        rows = [(t, svc, v) for svc, vals in run["series"][f].items() for t, v in vals]
        parts.append(pd.DataFrame(rows, columns=["ts", "service", f]).set_index(["ts", "service"]))
    df = pd.concat(parts, axis=1).reset_index().sort_values(["service", "ts"], ignore_index=True)
    df[["restarts_delta", "pods_not_ready"]] = df[["restarts_delta", "pods_not_ready"]].fillna(0.0)
    df = df.dropna(subset=["cpu_rate", "mem_ws_bytes"]).reset_index(drop=True)
    n = residual_minutes * 60 // STEP
    for f in F:  # gap to the median of the trailing window, current sample excluded
        base = df.groupby("service")[f].transform(lambda s: s.shift(1).rolling(n, min_periods=20).median())
        df[f"res_{f}"] = (df[f] - base).fillna(0.0)
    return df, run["windows"]


def window(df: pd.DataFrame, start: float, end: float) -> pd.DataFrame:
    return df[(df.ts >= start) & (df.ts <= end)]


def training_rows(df: pd.DataFrame, end: float) -> pd.DataFrame:
    d = df[df.ts <= end]
    d = d[~((d.ts - d.pod_start) < train.WARMUP_MINUTES * 60)]
    return d[(d.ts % 60) < STEP]


def per_service(fn):
    """Lift a per-service scorer to a whole frame."""

    def score(self, d):
        out = np.zeros(len(d))
        for svc, idx in d.groupby("service").indices.items():
            out[idx] = fn(self, svc, d.iloc[idx])
        return out

    return score


def distance_score(dist, alert_distance):
    return 1.0 - (1.0 - train.ALERT_THRESHOLD) ** (np.asarray(dist) / alert_distance)


class Envelope:
    def fit(self, d):
        self.bundle = train.AnomalyBundle({svc: train.fit_envelope(g[F]) for svc, g in d.groupby("service")})
        return self

    def score(self, d):
        return self.bundle.predict(None, d)


class Forest:
    def fit(self, d):
        self.m = {}
        for svc, g in d.groupby("service"):
            pipe = Pipeline(
                [("scaler", StandardScaler()),
                 ("iforest", IsolationForest(n_estimators=100, contamination=0.02, random_state=42))]
            ).fit(g[F])
            dec = pipe.decision_function(g[F])
            self.m[svc] = (pipe, dec.min(), dec.max())
        return self

    @per_service
    def score(self, svc, g):
        pipe, lo, hi = self.m[svc]
        return np.clip((hi - pipe.decision_function(g[F])) / ((hi - lo) or 1.0), 0.0, 1.0)


class Mahalanobis:
    """Features in floor units; covariance regularised so constant features
    (restarts) stay finite. Alerts at 2x the 99.5th percentile training distance."""

    def fit(self, d):
        self.m = {}
        for svc, g in d.groupby("service"):
            x = g[F].to_numpy(float) / FLOOR
            vary = x.std(axis=0) > 1e-9
            mu, cov = np.median(x, axis=0), np.zeros((len(F), len(F)))
            if vary.any():
                mcd = MinCovDet(random_state=0).fit(x[:, vary])
                mu[vary], cov[np.ix_(vary, vary)] = mcd.location_, mcd.covariance_
            inv = np.linalg.inv(cov + np.eye(len(F)))
            self.m[svc] = (mu, inv, 2 * np.quantile(self._dist(x, mu, inv), 0.995))
        return self

    @staticmethod
    def _dist(x, mu, inv):
        z = x - mu
        return np.sqrt(np.einsum("ij,jk,ik->i", z, inv, z))

    @per_service
    def score(self, svc, g):
        mu, inv, alert = self.m[svc]
        return distance_score(self._dist(g[F].to_numpy(float) / FLOOR, mu, inv), alert)


class Residual:
    def fit(self, d):
        self.tol = {svc: np.maximum(g[RES].abs().quantile(0.995).to_numpy(), FLOOR) for svc, g in d.groupby("service")}
        return self

    @per_service
    def score(self, svc, g):
        return distance_score((g[RES].abs().to_numpy() / self.tol[svc]).max(axis=1), train.RANGE_ALERT_DISTANCE)


def alerting(d: pd.DataFrame, scores: np.ndarray) -> set[str]:
    """Services whose score stays above the threshold long enough to fire."""
    out = set()
    for svc, g in d.assign(score=scores).groupby("service"):
        above = (g["score"] > train.ALERT_THRESHOLD).astype(int)
        if (above.rolling(HOLD).sum() >= HOLD).any():
            out.add(svc)
    return out


def evaluate(name: str, make, df: pd.DataFrame, w: dict) -> str:
    model = make().fit(training_rows(df, w["train_end"]))
    fault, restarts = w["fault"], w.get("restarts", [])
    excused = {r["service"] for r in restarts}  # really did restart: not a false alert

    normal = window(df, *w["normal"])
    false_live = alerting(normal, model.score(normal)) - excused

    during = window(df, fault["start"], fault["end"])
    scored = during.assign(score=model.score(during))
    hit = scored[(scored.service == fault["service"]) & (scored.score > train.ALERT_THRESHOLD)]
    detect = "none" if hit.empty else f"{hit.ts.iloc[0] - fault['start']:.0f}s"
    others = alerting(during, scored["score"].to_numpy()) - excused - {fault["service"]}

    pinned = normal.sort_values("ts").groupby("service").tail(1).copy()
    pinned["res_cpu_rate"] = 0.200 - (pinned["cpu_rate"] - pinned["res_cpu_rate"])
    pinned["cpu_rate"] = 0.200
    caught = int((model.score(pinned) > train.ALERT_THRESHOLD).sum())

    seen = []
    for r in restarts:
        after = window(df, r["at"] - 60, r["at"] + 480)
        seen.append("yes" if r["service"] in alerting(after, model.score(after)) else "NO")

    backtests = []
    for cut, end in w["backtests"]:
        m = make().fit(training_rows(df, cut))
        held_out = window(df, cut, end)
        backtests.append(len(alerting(held_out, m.score(held_out))))

    return (
        f"{name:<12} | {len(false_live):4d} | {'/'.join(map(str, backtests)):<9} | {detect:>5} +{len(others)} "
        f"| {caught:2d}/{pinned['service'].nunique():<2d} | {','.join(seen) or '-'}"
    )


def main(path: str) -> None:
    df, windows = load(path)
    print(f"{'model':<12} | live | backtests | {'fault':<8} | pinned | restarts")
    for name, make in (("envelope", Envelope), ("forest", Forest), ("mahalanobis", Mahalanobis), ("residual", Residual)):
        print(evaluate(name, make, df, windows))


if __name__ == "__main__":
    main(sys.argv[1])
