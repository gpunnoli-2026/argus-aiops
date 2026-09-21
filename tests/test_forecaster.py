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
