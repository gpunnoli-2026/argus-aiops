"""Export a cluster run's feature history from Prometheus, for compare_models.py.

Runs inside the cluster (Prometheus is not exposed), fed over stdin:

    kubectl -n aiops exec -i deploy/anomaly-detector -- python - \\
        < ml/evaluation/export_run.py > ml/evaluation/data/<run>.json

Export before `make down` — the data goes with the cluster. Then add a
"windows" block by hand (see compare_models.py); the fault and load times are
only known to whoever ran the session.
"""

import json
import os
import sys
import time

import requests

PROM_URL = os.environ.get("PROM_URL", "http://monitoring-kube-prometheus-prometheus.monitoring:9090")
HOURS = float(os.environ.get("EXPORT_HOURS", "8"))
STEP = 30

BY_OWNER = 'max by (pod_owner) (label_replace(%s, "pod_owner", "$1", "pod", "^(.*)-[a-z0-9]+-[a-z0-9]+$"))'
QUERIES = {
    "cpu_rate": "aiops:svc:cpu_rate",
    "mem_ws_bytes": "aiops:svc:mem_ws_bytes",
    "restarts_delta": "aiops:svc:restarts_delta",
    "pods_not_ready": "aiops:svc:pods_not_ready",
    "anomaly_score": "aiops_anomaly_score",
    "pod_start": BY_OWNER % 'kube_pod_start_time{namespace="boutique"}',
    "mem_limit_bytes": BY_OWNER % 'kube_pod_container_resource_limits{namespace="boutique",resource="memory"}',
}

end = time.time()
out = {"exported_at": end, "step": STEP, "series": {}}
for name, query in QUERIES.items():
    r = requests.get(
        f"{PROM_URL}/api/v1/query_range",
        params={"query": query, "start": end - HOURS * 3600, "end": end, "step": STEP},
        timeout=120,
    )
    r.raise_for_status()
    out["series"][name] = {
        (s["metric"].get("pod_owner") or s["metric"].get("service")): [[float(t), float(v)] for t, v in s["values"]]
        for s in r.json()["data"]["result"]
    }
json.dump(out, sys.stdout)
