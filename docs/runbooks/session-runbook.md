# Session Runbook — Bring-up → First ML Detection → Teardown

Goal for this session: complete Phase 2 in practice — train the anomaly model
and watch a chaos fault get detected by ML, end to end. Budget ~3.5 hours of
cluster time (~$1), most of it unattended baseline baking.

## 1. Bring-up (~20 min)

```bash
cd <your-clone-of>/argus-aiops
git pull                      # in case anything changed
make up                       # Terraform + kubeconfig; ~15 min; 3 nodes now
kubectl get nodes             # expect 3 Ready
bash scripts/deploy.sh        # monitoring + boutique + chaos + MLflow + detector; ~10 min
```

Verify everything scheduled:

```bash
kubectl get pods -A --field-selector=status.phase=Pending   # must be EMPTY
kubectl -n boutique get pods                                # 11+ Running
kubectl -n mlflow get pods                                  # 1 Running (slow start: pip install)
kubectl -n aiops get pods                                   # anomaly-detector Running
```

## 2. Start the baseline bake (~2 h unattended)

```bash
make load                     # k6 steady traffic, 2h duration
kubectl -n loadgen get pods   # k6-steady must be Running, not Pending
make detector-logs            # expect "no model available" warnings — normal
```

Leave it running ≥2 hours. (Lunch, meetings, LinkedIn.)
Optional sanity check in Grafana meanwhile:

```bash
make grafana                  # http://localhost:3000  admin / password from: make grafana-password
# Explore → query: aiops:svc:cpu_rate   → should show all boutique services
```

## 3. Train (~10 min)

```bash
make train
# ends with: "registered argus-anomaly v1 and set @production"
make detector-logs            # within 5 min: "loaded model from models:/argus-anomaly@production"
make scores                   # all services should score LOW (< ~0.5)
```

MLflow UI if curious: `make mlflow` → http://localhost:5000 (run params, metrics, registry).

## 4. The demo — fault → ML detection (~15 min)

Terminal A: `make grafana`, open Explore, query:
`aiops_anomaly_score{service="cartservice"}` (switch to 15m range, auto-refresh 10s)

Terminal B:

```bash
make chaos-cpu                # 5-min CPU stress on cartservice
```

Expected timeline:
- T+1–2 min: cartservice score climbs toward/past 0.8
- T+2–4 min: ServiceAnomalyDetected firing (Alertmanager: port-forward
  `kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-alertmanager 9093:9093`)
- T+5 min: chaos expires → score recovers to baseline

**Capture evidence while it runs** (README + resume material):
- Screenshot/GIF of the Grafana score spike (fault window visible)
- Screenshot of the alert in Alertmanager
- Note the detection latency (fault start → score > 0.8)

Repeat with `make chaos-podkill` and `make chaos-latency` if time allows.
`make chaos-clean` removes experiments early if needed.

## 5. Wrap up

```bash
git add -A && git commit -m "docs: phase 2 demo evidence" && git push   # if you added screenshots
make down                     # ALWAYS; verify "Destroy complete"
```

Console double-check (30 s): EC2 instances = none, NAT gateways = none.

## Troubleshooting quick refs

| Symptom | Fix |
|---|---|
| Pods Pending | `aws eks update-nodegroup-config --cluster-name argus --nodegroup-name <name> --scaling-config minSize=1,maxSize=4,desiredSize=3 --region us-west-2 --profile argus` (terraform ignores desired_size on live clusters) |
| chaos-daemon CrashLoop with `C:/Program Files/Git/...` path | MSYS path mangling — deploy.sh guards it; don't pass Unix paths via ad-hoc `helm --set` from Git Bash |
| Train job: "No training data" | k6 wasn't running long enough; check `kubectl -n loadgen get pods` |
| Detector: "no model available" after train | check `make mlflow` → registry has argus-anomaly with @production alias |
| Scores all high/noisy | model trained on too little baseline; bake longer, `make train` again |
| helm upgrade stuck | `kubectl get pods -A --field-selector=status.phase=Pending` first — it's usually capacity |

## After this session (next build phases)

- Phase 3: capacity forecaster (Prophet) + alert correlator (DBSCAN)
- Phase 4: Slack incident workflow + gated remediation — the flagship demo
- Phase 5: CI/CD images (ECR), nightly retraining CronJob, Evidently drift gates
