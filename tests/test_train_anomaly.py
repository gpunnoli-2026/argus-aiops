import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def fitted(train_module):
    rng = np.random.default_rng(42)
    n = 300
    normal = pd.DataFrame(
        {
            "service": "cartservice",
            "cpu_rate": rng.normal(0.2, 0.02, n),
            "mem_ws_bytes": rng.normal(2e8, 1e7, n),
            "restarts_delta": 0.0,
            "pods_not_ready": 0.0,
        }
    )
    models, calib = {}, {}
    models["cartservice"], lo, hi = train_module.fit_pipeline(normal[train_module.FEATURES])
    calib["cartservice"] = (lo, hi)
    models["__global__"], lo, hi = train_module.fit_pipeline(normal[train_module.FEATURES])
    calib["__global__"] = (lo, hi)
    bundle = train_module.AnomalyBundle(models, calib)
    return train_module, bundle, normal


def _extreme_row(service="cartservice"):
    return pd.DataFrame(
        [{"service": service, "cpu_rate": 5.0, "mem_ws_bytes": 2e9,
          "restarts_delta": 4.0, "pods_not_ready": 2.0}]
    )


def test_scores_bounded_zero_one(fitted):
    _, bundle, normal = fitted
    scores = bundle.predict(None, normal)
    assert scores.min() >= 0.0 and scores.max() <= 1.0


def test_extreme_fault_scores_above_alert_threshold(fitted):
    mod, bundle, _ = fitted
    assert bundle.predict(None, _extreme_row())[0] > mod.ALERT_THRESHOLD


def test_normal_traffic_scores_low(fitted):
    _, bundle, normal = fitted
    assert np.median(bundle.predict(None, normal)) < 0.5


def test_unknown_service_falls_back_to_global(fitted):
    mod, bundle, _ = fitted
    assert bundle.predict(None, _extreme_row(service="neverseen"))[0] > mod.ALERT_THRESHOLD


def test_alarm_rate_low_on_training_window(fitted):
    mod, bundle, normal = fitted
    assert mod.alarm_rate(bundle, normal) <= mod.GATE_MAX_ALARM_RATE


def _window_with_warmup(minutes=128):
    """One service shaped like cartservice on the GKE parity run: memory climbs
    for the first minutes after the pod starts, then traffic is steady."""
    rng = np.random.default_rng(7)
    mem = np.linspace(52, 61, minutes) + rng.normal(0, 0.3, minutes)
    mem[:8] = [37.6, 40.5, 42.8, 46.3, 48.1, 50.6, 51.4, 51.4]
    ts = 1_000_000.0 + 60 * np.arange(minutes)
    df = pd.DataFrame(
        {
            "ts": ts,
            "service": "cartservice",
            "cpu_rate": rng.normal(0.025, 0.002, minutes),
            "mem_ws_bytes": mem * 2**20,
            "restarts_delta": 0.0,
            "pods_not_ready": 0.0,
        }
    )
    starts = pd.DataFrame({"ts": ts, "service": "cartservice", "pod_start": ts[0] - 60})
    return df, starts


def test_drop_warmup_removes_minutes_after_each_pod_start(train_module):
    df, starts = _window_with_warmup()
    restart = df["ts"].iloc[60]
    starts.loc[starts["ts"] >= restart, "pod_start"] = restart  # pod replaced mid-window
    other = df.assign(service="emailservice")  # no pod start known: kept whole
    kept = train_module.drop_warmup(pd.concat([df, other], ignore_index=True), starts)

    cart = kept[kept["service"] == "cartservice"]["ts"]
    warmup = train_module.WARMUP_MINUTES * 60
    assert cart.min() >= starts["pod_start"].iloc[0] + warmup
    assert not cart.between(restart, restart + warmup, inclusive="left").any()
    assert (kept["service"] == "emailservice").sum() == len(df)


def test_cpu_only_fault_alerts_once_warmup_is_dropped(train_module):
    df, starts = _window_with_warmup()
    train = train_module.drop_warmup(df, starts)
    pipe, lo, hi = train_module.fit_pipeline(train[train_module.FEATURES])
    bundle = train_module.AnomalyBundle({"__global__": pipe}, {"__global__": (lo, hi)})
    # CPU pinned at its limit, nothing else wrong — what make chaos-cpu produces
    fault = pd.DataFrame(
        [{"service": "cartservice", "cpu_rate": 0.300, "mem_ws_bytes": 65 * 2**20,
          "restarts_delta": 0.0, "pods_not_ready": 0.0}]
    )
    assert bundle.predict(None, fault)[0] > train_module.ALERT_THRESHOLD
    assert train_module.alarm_rate(bundle, train) <= train_module.GATE_MAX_ALARM_RATE


def test_gate_passes_quiet_model_without_production_baseline(fitted, monkeypatch):
    mod, bundle, normal = fitted
    monkeypatch.setattr(
        mod.mlflow.pyfunc, "load_model", lambda uri: (_ for _ in ()).throw(RuntimeError("empty registry"))
    )
    ok, metrics = mod.promotion_gate(bundle, normal)
    assert ok and "gate_new_alarm_rate" in metrics


def test_gate_blocks_noisy_model(fitted, monkeypatch):
    mod, bundle, normal = fitted
    monkeypatch.setattr(mod, "GATE_MAX_ALARM_RATE", 0.0)
    monkeypatch.setattr(mod, "alarm_rate", lambda b, df: 0.5)
    ok, _ = mod.promotion_gate(bundle, normal)
    assert not ok


def test_gate_blocks_regression_against_production(fitted, monkeypatch):
    mod, bundle, normal = fitted

    class QuietProd:
        def predict(self, df):
            return np.zeros(len(df))

    monkeypatch.setattr(mod.mlflow.pyfunc, "load_model", lambda uri: QuietProd())
    monkeypatch.setattr(mod, "GATE_MAX_ALARM_RATE", 1.0)
    rates = iter([0.9, 0.0])  # new model noisy, production quiet
    monkeypatch.setattr(mod, "alarm_rate", lambda b, df: next(rates))
    ok, metrics = mod.promotion_gate(bundle, normal)
    assert not ok
    assert metrics["gate_prod_alarm_rate"] == 0.0
