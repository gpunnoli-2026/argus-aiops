# Argus — Platform Architecture (Day 1 Deliverable)

Two levels: the **high-level architecture** (system context and layers) and the **detailed architecture** (component contracts, data models, and flows — what you build against).

---

# Part 1: High-Level Architecture

## 1.1 System context

```
                        ┌──────────────────────────────┐
   SRE / Operator ◄────►│  Slack (incidents, approvals) │
        │               └──────────────▲───────────────┘
        │ Grafana / MLflow UI          │
        ▼                              │
┌──────────────────────────────────────┴──────────────────────────┐
│                     Argus Platform (Kubernetes)                  │
│                                                                  │
│   Workload Layer      Observability      Intelligence   Action  │
│   (demo app +   ───►  Layer         ───► Layer     ───►  Layer  │
│    fault inject)      (metrics/alerts)   (ML)           (remedi-│
│                                                          ation) │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                    MLOps Layer (MLflow, CI/CD, retraining)
                               │
                    Infrastructure Layer (Terraform: EKS, S3, IAM)
```

## 1.2 The six layers

| Layer | Purpose | Components |
|---|---|---|
| **1. Infrastructure** | Reproducible, disposable environment | Terraform (VPC, EKS, S3, IAM/IRSA), Helm, Makefile lifecycle |
| **2. Workload** | Realistic system to monitor + controllable failures | Online Boutique (11 microservices), k6 load profiles, Chaos Mesh experiments |
| **3. Observability** | Ground-truth telemetry | Prometheus, Alertmanager, Grafana, recording rules (the ML feature source) |
| **4. Intelligence** | Detect, predict, correlate | anomaly-detector, capacity-forecaster, alert-correlator (FastAPI + models from registry) |
| **5. Action** | Turn predictions into SRE workflow | incident-orchestrator, Slack integration, remediation-executor (gated) |
| **6. MLOps** | Model lifecycle | MLflow tracking/registry, retraining CronJob, Evidently drift checks, GitHub Actions CI/CD |

## 1.3 Primary data flow (the demo story)

```
Chaos Mesh injects fault
   → Online Boutique degrades
   → Prometheus scrapes metrics / Alertmanager fires raw alerts
   → anomaly-detector scores metric streams (30s loop)
   → alert-correlator groups alerts + anomalies into ONE incident,
     predicts severity + likely root-cause service
   → incident-orchestrator posts incident to Slack with
     recommended runbook + [Approve] [Dismiss] buttons
   → on Approve: remediation-executor performs safe action
     (scale / restart / rollback) with audit log
   → Grafana shows recovery; incident auto-resolves
```

Parallel flow: **capacity-forecaster** continuously projects resource trends and raises *predictive* alerts ("node pool exhausts memory in ~6h") into the same incident pipeline.

MLOps flow: **nightly CronJob** pulls fresh Prometheus data → retrains → Evidently drift report → eval gate → promote to registry → services hot-reload.

## 1.4 Key architectural principles

1. **Kubernetes-native core, cloud-specific edge.** Every component runs in-cluster via Helm. Cloud specifics (cluster provisioning, object storage, IAM) are isolated behind Terraform modules and env vars → AWS today, GKE/AKS as additive modules later.
2. **Human-in-the-loop by default, automation by allowlist.** All remediations are recommendations unless the action is on a narrow, pre-approved safe list; everything is audited and dry-run-able.
3. **Closed feedback loop.** Injected faults are labeled ground truth → training data → measurable precision/recall and MTTR numbers.
4. **Models are cattle.** Services load models by registry stage (`Production`), never by file path. Promotion, rollback, and lineage live in MLflow.

---

# Part 2: Detailed Architecture

## 2.1 Deployment topology (AWS)

```
AWS Account
└── VPC 10.0.0.0/16 (2 AZs)
    ├── Public subnets: NAT GW, (optional) ALB for Slack webhook ingress
    ├── Private subnets: EKS managed node group (2× t3.medium spot,
    │                    scale 1–4)
    └── EKS control plane (v1.31+)
        ├── ns: boutique        — demo app
        ├── ns: monitoring      — kube-prometheus-stack, Grafana
        ├── ns: chaos           — Chaos Mesh
        ├── ns: aiops           — ML services, orchestrator, executor
        ├── ns: mlflow          — MLflow server (backend: in-cluster
        │                         Postgres; artifacts: S3)
        └── ns: loadgen         — k6 jobs
S3: aiops-artifacts-<acct>   — MLflow artifacts, training datasets
IAM: IRSA roles — mlflow→S3, executor→none (K8s RBAC only)
```

- Slack ingress: ALB + Ingress **or** (cheaper) `slack-events` via Socket Mode — no public endpoint needed. **Decision: Socket Mode** (zero ingress cost, no exposed public surface).
- Local dev target: kind cluster, MinIO replaces S3 (`ARTIFACT_ENDPOINT` env var), everything else identical.

## 2.2 Component specifications

### anomaly-detector (Python/FastAPI)
- **Loop:** every 30s, query Prometheus HTTP API for feature vector per service.
- **Features (per service):** cpu_usage_rate, memory_working_set, p50/p95/p99 latency, request_rate, error_rate, pod_restart_delta — from recording rules (5m windows).
- **Model:** IsolationForest per service + rolling z-score ensemble; score ∈ [0,1].
- **Outputs:**
  - `GET /metrics` → exposes `aiops_anomaly_score{service=...}` (scraped by Prometheus)
  - `POST /score` → on-demand scoring (used by retraining eval)
- **Alerting:** PrometheusRule: `aiops_anomaly_score > 0.8 for 2m` → Alertmanager.
- **Model loading:** MLflow client, stage=`Production`, poll for new version every 5m.

### capacity-forecaster (Python/FastAPI)
- **Loop:** every 15m, pull 7d of node/namespace-level cpu, memory, disk, and per-service request-rate trends.
- **Model:** Prophet per resource series (daily/weekly seasonality from k6 load schedule).
- **Output:** `aiops_forecast_hours_to_threshold{resource=..., threshold="80"}` gauge; PrometheusRule fires predictive alert when < 12h.
- **API:** `GET /forecast/{resource}` → JSON series for the Grafana forecast panel.

### alert-correlator (Python/FastAPI)
- **Input:** Alertmanager webhook (all firing alerts) + anomaly alerts.
- **Correlation:** sliding 5m window; DBSCAN over (time proximity, label similarity: service, node, namespace) + service-dependency hints (static topology map of Online Boutique) → incident groups.
- **Classification:** gradient-boosted classifier → `severity` (SEV1–3) + `probable_root_service`. Trained on labeled chaos runs (fault type/target = ground truth). V1 fallback: rule-based mapping, ML swapped in Phase 3.
- **Output:** `POST /incident` to incident-orchestrator with incident document (schema §2.3).

### incident-orchestrator (Python/FastAPI)
- **Responsibilities:** incident lifecycle (open → acknowledged → remediating → resolved), dedup, Slack posting, approval handling, audit trail.
- **State store:** Postgres (shared instance with MLflow, separate DB).
- **Slack:** Block Kit message — summary, severity, root-cause guess, metric snapshot link, recommended runbook, `[Approve remediation] [Dismiss]`. Socket Mode interaction handler.
- **Auto-execute path:** if recommended action ∈ allowlist AND severity ≤ SEV3 → execute with 60s cancel window posted to Slack.

### remediation-executor (Python/FastAPI)
- **Actions (v1 allowlist):**
  | Action | Params | Safety check before | Verify after |
  |---|---|---|---|
  | scale_deployment | ns, name, +replicas (max 2× current, cap 10) | HPA absent/not fighting | pods Ready in 3m |
  | restart_deployment | ns, name | not restarted in last 10m | rollout complete |
  | rollback_deployment | ns, name | previous ReplicaSet exists | error rate drops 5m |
- **Execution:** Kubernetes Python client; ServiceAccount RBAC scoped to `boutique` namespace, verbs: get/list/patch on deployments only.
- **Modes:** `dry_run` (default in config), `gated` (Slack approval), `auto` (allowlist only).
- **Audit:** every request/decision/result → structured JSON log + `remediation_audit` table.

### retraining pipeline (K8s CronJob, nightly)
```
extract (Prometheus → parquet, 24h window, S3)
  → validate (schema, null checks)
  → drift check (Evidently: new window vs training baseline)
  → train (all 3 model families; log params/metrics/artifacts to MLflow)
  → evaluate (precision/recall on held-out labeled chaos windows;
              forecast MAPE)
  → gate: metrics ≥ current Production model? → promote to Production
          else → log, alert #aiops-ml channel, keep old model
```

## 2.3 Data contracts

**Incident document (correlator → orchestrator):**
```json
{
  "incident_id": "inc-2026-...",
  "created_at": "ISO8601",
  "severity": "SEV2",
  "probable_root_service": "cartservice",
  "affected_services": ["cartservice", "frontend"],
  "alert_ids": ["..."],
  "anomaly_scores": {"cartservice": 0.93},
  "forecast_context": {"node_mem_hours_to_80pct": 5.5},
  "recommended_action": {
    "type": "restart_deployment",
    "params": {"namespace": "boutique", "name": "cartservice"},
    "confidence": 0.81,
    "runbook_url": "docs/runbooks/cart-oom.md"
  },
  "status": "open"
}
```

**Feature vector (Prometheus recording rules, per service, 5m window):**
`cpu_rate, mem_ws_bytes, latency_p50/p95/p99, req_rate, err_rate, restarts_delta` — recorded as `aiops:svc:<metric>` series so training and inference read identical definitions (no train/serve skew).

**Training label source:** Chaos Mesh experiment CRs are logged (type, target, start/end) by a small `chaos-labeler` job → `labels.parquet` in S3. Anomaly windows = experiment windows.

## 2.4 Repository layout

```
argus-aiops/
├── terraform/aws/            # VPC, EKS, S3, IAM (gcp/, azure/ later)
├── helm/
│   ├── platform/             # umbrella chart: aiops services
│   └── values/{kind,eks}.yaml
├── services/
│   ├── anomaly-detector/  capacity-forecaster/  alert-correlator/
│   ├── incident-orchestrator/  remediation-executor/
│   └── common/               # shared prometheus/mlflow/slack clients
├── ml/
│   ├── training/             # pipelines per model family
│   ├── evaluation/           # eval + drift (Evidently)
│   └── notebooks/            # exploration only, nothing production
├── chaos/                    # Chaos Mesh experiment library
├── loadgen/                  # k6 scenarios + schedule
├── observability/            # dashboards json, recording/alerting rules
├── .github/workflows/        # ci.yaml, deploy.yaml, retrain-image.yaml
├── docs/                     # this file, runbooks/, decisions/
└── Makefile                  # up/down/deploy/demo/teardown
```

## 2.5 Security model

- **IRSA** for anything touching AWS (MLflow→S3); no static keys in-cluster.
- **remediation-executor** is the only component with K8s write access; RBAC limited to `deployments` in `boutique` ns; no cluster-admin anywhere.
- Slack signing-secret verification on all interaction payloads; approval identity recorded in audit log.
- NetworkPolicies: aiops ns → monitoring (Prometheus API) and mlflow only; boutique cannot reach aiops.
- Secrets: External Secrets Operator → AWS Secrets Manager (kind: plain K8s secrets).

## 2.6 Portability abstraction

| Concern | Abstraction | AWS impl | Later GCP/Azure |
|---|---|---|---|
| Cluster | Terraform module interface (`cluster` outputs kubeconfig) | EKS | GKE / AKS module |
| Object storage | S3-compatible endpoint env vars | S3 | GCS (S3-interop) / MinIO gateway |
| Secrets | External Secrets Operator | Secrets Manager | GCP SM / Key Vault |
| Everything else | Helm charts, unchanged | — | — |

## 2.7 Observability of the platform itself (meta-monitoring)

Each AIOps service exposes `/metrics`: scoring latency, model version in use, scoring loop lag, Slack delivery failures, remediation success/failure counters. Dashboard "AIOps Health" — an SRE platform must itself be observable.

## 2.8 Platform KPIs

- Detection latency: fault injection → anomaly alert (target < 60s)
- Alert noise reduction: raw alerts vs correlated incidents per chaos run
- Simulated MTTR: fault → recovery, gated-auto vs manual baseline
- Model quality: precision/recall on held-out chaos windows; forecast MAPE
- Forecast lead time: hours of warning before threshold breach
