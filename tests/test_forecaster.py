"""Disk-forecast query construction.

The mountpoint is the one node-level assumption that does not survive a change
of cloud: EKS and kind nodes fill up "/", GKE's COS nodes mount "/" read-only
and near-full from a verity partition, so forecasting it would predict
permanent exhaustion.
"""


def test_default_mountpoint_is_root(forecaster_module):
    assert forecaster_module.DISK_MOUNTPOINT == "/"


def test_root_query_matches_the_eks_behaviour(forecaster_module):
    """Regression guard: the default render must stay what EKS runs today."""
    disk = forecaster_module.build_queries("/")["node_disk"]
    assert disk == (
        '1 - avg by (instance) (node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"} '
        '/ node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"})'
    )


def test_mountpoint_applies_to_both_sides_of_the_ratio(forecaster_module):
    """A mountpoint on only one side would divide two different filesystems."""
    disk = forecaster_module.build_queries("/mnt/stateful_partition")["node_disk"]
    assert disk.count('mountpoint="/mnt/stateful_partition"') == 2
    assert '"/"' not in disk


def test_mountpoint_does_not_leak_into_cpu_or_memory(forecaster_module):
    queries = forecaster_module.build_queries("/mnt/stateful_partition")
    assert "mountpoint" not in queries["node_cpu"]
    assert "mountpoint" not in queries["node_mem"]


def _series(instance, last_ts, n=3):
    return {
        "metric": {"instance": instance},
        "values": [[last_ts - 300 * i, "0.25"] for i in reversed(range(n))],
    }


def test_parse_series_skips_nodes_that_are_gone(forecaster_module):
    """A scaled-down or preempted node stays in the 12h range result; forecasting
    its last hours would keep publishing a node that no longer exists."""
    now = 1_000_000.0
    result = [_series("live:9100", now - 60), _series("gone:9100", now - 3600)]
    assert list(forecaster_module.parse_series(result, now)) == ["live:9100"]


def test_retire_removes_published_series(forecaster_module):
    hours = forecaster_module.HOURS
    hours.labels(resource="node_cpu", instance="gone:9100").set(0.75)
    forecaster_module.retire({("node_cpu", "gone:9100"), ("node_cpu", "never-published:9100")})
    published = {s.labels["instance"] for m in hours.collect() for s in m.samples}
    assert "gone:9100" not in published


def _frame(values):
    import pandas as pd

    ds = pd.date_range("2026-10-01", periods=len(values), freq="300s")
    return pd.DataFrame({"ds": ds, "y": values})


def test_flat_short_history_forecasts_no_crossing(forecaster_module):
    """Regression: with daily seasonality forced on, 3h of flat 25% CPU was
    forecast to cross 80% within hours."""
    import numpy as np
    import pytest

    pytest.importorskip("prophet")
    y = 0.25 + np.random.default_rng(0).normal(0, 0.01, 36)
    assert forecaster_module.hours_to_threshold(_frame(y)) is None


def test_steady_ramp_forecasts_the_linear_crossing(forecaster_module):
    """30% -> 60% over 6h is +5%/h, so 80% is about 4h away."""
    import numpy as np
    import pytest

    pytest.importorskip("prophet")
    y = np.linspace(0.30, 0.60, 72) + np.random.default_rng(0).normal(0, 0.01, 72)
    assert 3.0 <= forecaster_module.hours_to_threshold(_frame(y)) <= 5.0
