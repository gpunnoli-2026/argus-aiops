import numpy as np
import pandas as pd
import pytest


def _bundle(mod, train):
    env = mod.fit_envelope(train[mod.FEATURES])
    return mod.AnomalyBundle({"cartservice": env, "__global__": env})


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
    return train_module, _bundle(train_module, normal), normal


def _extreme_row(service="cartservice"):
    return pd.DataFrame(
        [{"service": service, "cpu_rate": 5.0, "mem_ws_bytes": 2e9,
          "restarts_delta": 4.0, "pods_not_ready": 2.0}]
    )


def test_scores_bounded_zero_one(fitted):
    _, bundle, normal = fitted
    scores = bundle.predict(None, pd.concat([normal, _extreme_row()], ignore_index=True))
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


@pytest.fixture(scope="module")
def steady(train_module):
    df, starts = _window_with_warmup()
    train = train_module.drop_warmup(df, starts)
    return train_module, _bundle(train_module, train), train


def _row(**features):
    return pd.DataFrame([{"service": "cartservice", "restarts_delta": 0.0, "pods_not_ready": 0.0, **features}])


def test_memory_creep_just_past_the_trained_range_does_not_alert(steady):
    """Regression (GKE parity run): memory 0.3 MiB over the trained maximum
    scored ~1.0 on the IsolationForest and raised alerts on healthy services."""
    _, bundle, train = steady
    creep = _row(cpu_rate=train["cpu_rate"].median(), mem_ws_bytes=train["mem_ws_bytes"].max() + 0.3 * 2**20)
    assert bundle.predict(None, creep)[0] < 0.2


def test_score_grows_with_distance_outside_the_range(steady):
    """What the forest could not do: tell slightly outside from far outside."""
    _, bundle, train = steady
    mem = train["mem_ws_bytes"].median()
    scores = [bundle.predict(None, _row(cpu_rate=c, mem_ws_bytes=mem))[0] for c in (0.030, 0.045, 0.060, 0.300)]
    assert scores == sorted(scores) and scores[0] < 0.2 < scores[-1]


@pytest.mark.parametrize(
    "fault",
    [
        {"cpu_rate": 0.300},  # CPU pinned at its limit, nothing else wrong: make chaos-cpu
        {"restarts_delta": 1.0},  # one restart, e.g. an OOM kill
        {"pods_not_ready": 1.0},
    ],
)
def test_single_feature_faults_alert(steady, fault):
    mod, bundle, train = steady
    row = {"cpu_rate": train["cpu_rate"].median(), "mem_ws_bytes": train["mem_ws_bytes"].median(), **fault}
    assert bundle.predict(None, _row(**row))[0] > mod.ALERT_THRESHOLD
    assert mod.alarm_rate(bundle, train) <= mod.GATE_MAX_ALARM_RATE


def test_quieter_traffic_inside_the_trained_range_scores_zero(fitted):
    _, bundle, normal = fitted
    quiet = _row(cpu_rate=normal["cpu_rate"].quantile(0.05), mem_ws_bytes=normal["mem_ws_bytes"].median())
    assert bundle.predict(None, quiet)[0] == 0.0


def test_nightly_refresh_skips_when_there_is_no_production_model(train_module, monkeypatch):
    """The CronJob must not promote the first model on a new cluster."""
    monkeypatch.setattr(train_module, "REQUIRE_PRODUCTION_BASELINE", True)
    monkeypatch.setattr(train_module, "has_production_model", lambda: False)
    monkeypatch.setattr(train_module, "build_matrix", lambda: pytest.fail("trained without a baseline"))
    train_module.main()


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
