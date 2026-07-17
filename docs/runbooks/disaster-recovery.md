# Disaster Recovery Runbook — Argus AIOps Platform

Covers recovery for the four main failure classes: infrastructure loss, service outage,
ML pipeline failure, and data loss. Each section is independent — jump to the scenario
that matches the failure you are seeing.

**Prerequisites**: AWS CLI configured with the `argus` profile, `kubectl`, `terraform`,
`helm`, and the repo checked out locally.

---

## Failure Class 1 — Total Infrastructure Loss

**Symptom**: EKS cluster unreachable, `kubectl get nodes` times out, or AWS console
shows no EC2 instances / VPC deleted.

**Cause**: `make down` was run (expected teardown), accidental Terraform destroy, or
an AWS account suspension.

### Recovery

```bash
# 1. Recreate all AWS infrastructure (~15 min)
make up

# 2. Verify 3 nodes are Ready before proceeding
kubectl get nodes        # all three: Ready

# 3. Redeploy the full stack (monitoring, boutique, chaos, aiops services)
bash scripts/deploy.sh   # ~10 min

# 4. Verify all workloads scheduled
kubectl get pods -A --field-selector=status.phase=Pending   # must be EMPTY
kubectl -n boutique   get pods                              # 11+ Running
kubectl -n monitoring get pods                              # prometheus, grafana, alertmanager Running
kubectl -n mlflow     get pods                              # 1 Running
kubectl -n aiops      get pods                              # anomaly-detector, correlator, forecaster Running
```

After a full infrastructure rebuild the ML models are gone (MLflow PVC is new).
Continue to **Failure Class 3 — ML Pipeline Recovery** to retrain.

---

## Failure Class 2 — Service / Pod Failure

### 2a. Single Pod CrashLoop

```bash
# Identify the crashing pod
kubectl get pods -A | grep -v Running | grep -v Completed

# Inspect logs (last 100 lines)
kubectl -n <namespace> logs <pod-name> --tail=100

# Common fixes by namespace:
#   aiops — missing Prometheus / MLflow endpoint or OOM
#   boutique — image pull error or node pressure
#   monitoring — Prometheus PVC full (see Section 2c)

# Restart the deployment after the root cause is fixed
kubectl -n <namespace> rollout restart deployment/<name>

# Watch recovery
kubectl -n <namespace> rollout status deployment/<name>
```

### 2b. Namespace Stuck — All Pods Pending

Spot node eviction or under-provisioned node group.

```bash
# Check node pressure
kubectl describe nodes | grep -A5 "Conditions:"

# Force node group to desired size via AWS CLI (Terraform ignores desired_size on
# live clusters — this is a known limitation)
aws eks update-nodegroup-config \
  --cluster-name argus \
  --nodegroup-name <nodegroup-name> \
  --scaling-config minSize=1,maxSize=4,desiredSize=3 \
  --region us-west-2 \
  --profile argus

# Find the nodegroup name if needed
aws eks list-nodegroups --cluster-name argus --region us-west-2 --profile argus

# Wait ~3 min for new node to join, then verify
kubectl get nodes
```

### 2c. Prometheus PVC Full (10 Gi limit)

```bash
# Confirm disk pressure
kubectl -n monitoring exec -it $(kubectl -n monitoring get pod -l app.kubernetes.io/name=prometheus -o name | head -1) \
  -- df -h /prometheus

# Option A — reduce retention (fastest, loses old data)
# Edit the Prometheus CR and lower --storage.tsdb.retention.time from the default
kubectl -n monitoring edit prometheus monitoring-kube-prometheus-prometheus
# Set: retention: "1d"   (was 2d)

# Option B — expand the PVC (no data loss, requires EBS CSI)
kubectl -n monitoring patch pvc <prometheus-pvc-name> \
  --type=merge -p '{"spec":{"resources":{"requests":{"storage":"20Gi"}}}}'
# Then restart Prometheus pod so it picks up the new size
kubectl -n monitoring rollout restart statefulset/prometheus-monitoring-kube-prometheus-prometheus
```

### 2d. Alertmanager Not Routing to Correlator

```bash
# Check Alertmanager config is loaded correctly
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-alertmanager 9093:9093 &
# Open http://localhost:9093/#/status — verify "argus-correlator" receiver is listed

# Check correlator is receiving webhooks
make correlator-logs   # look for "received alert" lines

# If config missing receivers (truncated): redeploy
bash scripts/deploy.sh
```

---

## Failure Class 3 — ML Pipeline Recovery

### 3a. Models Gone (Fresh Cluster or MLflow PVC Replaced)

Anomaly-detector logs: `"no model available"` and scores are not being published.

```bash
# Step 1 — generate at least 2h of representative traffic for training data
make load             # k6 steady traffic; 2h duration; run unattended

# Verify load is running
kubectl -n loadgen get pods    # k6-steady: Running

# Step 2 — after ≥2h, train
make train
# Expected final line: "registered argus-anomaly v1 and set @production"

# Step 3 — verify detector loads the new model (within 5 min of training)
make detector-logs    # look for: "loaded model from models:/argus-anomaly@production"

# Step 4 — confirm scores are healthy (all services < 0.5 under normal load)
make scores
```

### 3b. Training Job Fails — "No training data"

```bash
# Check that recording rules have data
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090 &
# Open http://localhost:9090 and query: aiops:svc:cpu_rate
# If empty → k6 traffic was not running long enough

# Check k6 pod
kubectl -n loadgen get pods
kubectl -n loadgen logs <k6-pod>

# Fix: restart load, wait ≥30 min (2h preferred), then retrain
make load
# ... wait ...
make train
```

### 3c. Detector Scores Noisy / All High After Training

Model trained on insufficient or non-representative baseline.

```bash
# Restart with a fuller baseline using the varied traffic profile
make load-varied     # 2.5h multi-regime (idle → ramp → steady → spike)

# Retrain after completion
make train

# Verify
make scores          # expect majority of services below 0.5
```

### 3d. MLflow Unreachable From Training Job

```bash
# Check MLflow pod
kubectl -n mlflow get pods
kubectl -n mlflow logs <mlflow-pod>

# Port-forward to inspect UI and registry
make mlflow           # http://localhost:5000

# If pod is in CrashLoop, check PVC binding
kubectl -n mlflow describe pvc

# If PVC unbound (fresh cluster, no StorageClass): ensure EBS CSI driver is deployed
kubectl get storageclass
# Should list: gp2 or gp3 (EKS default)
# If missing, deploy.sh registers it — rerun: bash scripts/deploy.sh
```

---

## Failure Class 4 — Data Recovery

### 4a. Restore MLflow Model Artifacts From S3

S3 versioning is enabled on the `argus-artifacts-<account-id>` bucket. Models are
never truly deleted — only overwritten.

```bash
# List versions of a model artifact
aws s3api list-object-versions \
  --bucket argus-artifacts-<account-id> \
  --prefix mlflow/argus-anomaly/ \
  --profile argus \
  --query 'Versions[*].{Key:Key,VersionId:VersionId,LastModified:LastModified}'

# Restore a specific version to a local path
aws s3 cp \
  "s3://argus-artifacts-<account-id>/mlflow/argus-anomaly/<model-path>" \
  ./recovered-model/ \
  --version-id <version-id> \
  --profile argus

# Re-register the recovered artifact via MLflow CLI or Python SDK, then promote:
# mlflow.register_model("runs:/<run-id>/model", "argus-anomaly")
# client.set_registered_model_alias("argus-anomaly", "production", <version>)
```

### 4b. Restore Prometheus Metrics From EBS Snapshot

If the Prometheus PVC needs to be restored from an EBS snapshot (e.g., after
accidental PVC deletion):

```bash
# 1. Find the snapshot in AWS console: EC2 → Snapshots → filter by "argus"
#    Note the snapshot ID (snap-xxxxxxxx)

# 2. Create a new EBS volume from the snapshot in the same AZ as your nodes
aws ec2 create-volume \
  --snapshot-id snap-xxxxxxxx \
  --availability-zone us-west-2a \
  --volume-type gp2 \
  --profile argus

# 3. Create a PersistentVolume pointing at the new EBS volume, then re-bind the
#    existing PVC to it. The kube-prometheus-stack Helm values bind by StorageClass;
#    for manual PV/PVC pairing use the volumeName field:
#    kubectl patch pvc prometheus-db -n monitoring -p '{"spec":{"volumeName":"<pv-name>"}}'

# 4. Restart Prometheus
kubectl -n monitoring rollout restart \
  statefulset/prometheus-monitoring-kube-prometheus-prometheus
```

Note: Prometheus data loss is recoverable for the ML pipeline only if there is ≥2h
of recent data. If the snapshot predates the last model training, retraining from
fresh load is often faster than snapshot restore.

---

## Post-Recovery Validation

Run these checks after any recovery to confirm the platform is fully operational.

```bash
# 1. Infrastructure
kubectl get nodes                         # 3 nodes, all Ready
kubectl get pods -A | grep -v Running | grep -v Completed   # no non-Running pods

# 2. Observability
make grafana                              # http://localhost:3000  admin / argus-admin
# Query in Explore: aiops:svc:cpu_rate    # should show all boutique services

# 3. ML detection
make scores                               # all services scoring LOW under normal load

# 4. End-to-end detection (5-min smoke test)
make chaos-cpu                            # inject CPU stress on cartservice
# In Grafana: aiops_anomaly_score{service="cartservice"} → should spike above 0.8
# In Alertmanager (port-forward 9093): ServiceAnomalyDetected should fire

# 5. Alert correlation
make incidents                            # should show at least one correlated incident

# 6. Forecaster
make forecasts                            # hours_to_threshold values for all resources
```

---

## Cost-Control Safeguards

These steps prevent runaway AWS charges after recovery work.

```bash
# Always destroy the cluster at the end of a session
make down              # wait for "Destroy complete. Resources: N destroyed."

# Console verification (30 sec): open AWS console and confirm:
#   EC2 → Instances: none in us-west-2
#   VPC → NAT Gateways: none
#   EC2 → Volumes: none tagged argus (or confirm deleted)
```

Recommended: set a billing alert at $30 in AWS Budgets before starting any recovery
session involving `make up`.

---

## Quick Troubleshooting Reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `kubectl get nodes` timeout | Cluster destroyed or kubeconfig stale | `make up` then `aws eks update-kubeconfig` |
| Pods Pending on fresh deploy | Node group not at desired capacity | `aws eks update-nodegroup-config … desiredSize=3` (see §2b) |
| `chaos-daemon` CrashLoop with `C:/Program Files/Git/…` path | MSYS path mangling on Windows | Run `deploy.sh` (guards this); avoid ad-hoc `helm --set` with Unix paths from Git Bash |
| Detector logs: `"no model available"` | No `@production` alias in MLflow | Run `make load` (≥2h), then `make train` |
| Scores all 1.0 immediately after train | Training data too sparse or too noisy | Run `make load-varied` (2.5h), retrain |
| `make train` → `"No training data"` | k6 was not running / recording rules empty | Start `make load`, wait ≥30 min, retrain |
| Alertmanager shows no receivers | Config was truncated on deploy | Rerun `bash scripts/deploy.sh` |
| MLflow UI blank / 500 errors | PVC not bound or pod still starting | `kubectl -n mlflow describe pvc`; wait for pod Running before querying |
| Helm upgrade hangs | Pending pods blocking webhook | `kubectl get pods -A --field-selector=status.phase=Pending` first |
| S3 artifact upload fails | IRSA annotation missing on mlflow SA | Check `kubectl -n mlflow describe sa mlflow` for IRSA annotation |
