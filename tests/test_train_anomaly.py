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
