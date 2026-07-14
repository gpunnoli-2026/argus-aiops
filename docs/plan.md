# Argus — AIOps Incident Prediction & Automated Response Platform: Build Plan

**Goal:** Demonstrate the complete operational lifecycle of ML in production: telemetry → detection/prediction → MLOps pipeline → SRE workflow integration → gated auto-remediation.

**Constraints:** AWS first, portable to GCP/Azure later. Fastest possible path. Remediation = recommend by default + safe gated auto-actions.

---

## Architecture at a glance

```
┌─────────────────────────── EKS Cluster ───────────────────────────┐
│                                                                    │
│  Online Boutique (demo microservices app)                          │
│       │ metrics/logs                                               │
│  kube-prometheus-stack (Prometheus + Alertmanager + Grafana)       │
│       │                                                            │
│  ┌─── ML Services (FastAPI) ─────────────────────────┐             │
│  │ anomaly-detector   capacity-forecaster            │             │
│  │ alert-correlator                                  │◄── MLflow   │
│  └───────────────┬───────────────────────────────────┘   (registry)│
│                  ▼                                                 │
│  incident-orchestrator ──► Slack (recommend / approve buttons)     │
│                  │ approved                                        │
│                  ▼                                                 │
│  remediation-executor (scale, restart, rollback — dry-run first)   │
│                                                                    │
│  k6 load generator + Chaos Mesh (fault injection for demos)        │
└────────────────────────────────────────────────────────────────────┘
   Terraform (EKS, VPC, S3, IAM) │ GitHub Actions (CI/CD + retraining)
```

**Portability principle:** everything that matters runs *inside* Kubernetes (Helm charts). AWS-specific parts are isolated to one Terraform module (cluster, S3, IAM). Porting to GKE/AKS later = new Terraform module + swap S3 for GCS/Blob behind an env var. No CloudWatch, no SageMaker, no AWS-only dependencies in the core.

---

## Stack decisions (optimized for speed and clarity)

| Concern | Choice | Why |
|---|---|---|
| Infra | Terraform + EKS (spot nodes) | IaC signal; spot keeps cost ~$3–5/day; teardown script mandatory |
| Demo app | Online Boutique (Google microservices demo) | 11 services, realistic, zero effort |
| Telemetry | kube-prometheus-stack via Helm | Industry standard; portable |
| Load/chaos | k6 + Chaos Mesh | Generates the anomalies your ML detects — makes demos reproducible |
| ML | Python: scikit-learn (IsolationForest), Prophet, DBSCAN/gradient boosting | Well-understood, fast to build, easy to reason about and explain |
| MLOps | MLflow (tracking + registry) on-cluster, S3 artifact store | The pragmatic industry default; Kubeflow is slower to operate for little extra signal |
| Serving | FastAPI containers, models pulled from MLflow registry | Simple, debuggable, portable |
| CI/CD | GitHub Actions (lint/test/build/deploy) + K8s CronJob for retraining | Free, visible on the repo itself |
| Drift | Evidently AI report in the retraining job | Cheap to add, big MLOps talking point |
| Workflow | Alertmanager webhook → orchestrator → Slack Block Kit buttons | "Approve remediation from Slack" is the demo highlight |

---

## Phases (aggressive timeline: ~4 weeks part-time)

### Phase 0 — Foundation (days 1–3)
- **Day 1: architecture first.** Finalize the high-level + detailed architecture (see `aiops_architecture.md` — companion document): layers, component contracts, data schemas, security model, portability abstractions. Commit it as `docs/architecture.md` in the repo before writing code.
- Monorepo: `terraform/`, `helm/`, `services/`, `ml/`, `.github/workflows/`, `docs/`
- Terraform: VPC + EKS (2 spot nodes) + S3 bucket + IAM (IRSA)
- `make up` / `make down` — teardown discipline from day one
- **Exit:** cluster up/down in one command

### Phase 1 — Telemetry & failure lab (days 4–7)
- Deploy Online Boutique, kube-prometheus-stack, Grafana dashboards
- k6 load profiles (steady, spike, ramp) as K8s Jobs
- Chaos Mesh experiments: CPU stress, memory leak sim, pod kill, network latency
- Prometheus recording rules exporting clean time-series for ML
- **Exit:** you can inject a fault and watch it in Grafana. This is your data factory.

### Phase 2 — Use case 1: Anomaly detection (days 8–13)
- Export metrics (CPU, memory, p95 latency, error rate per service) via Prometheus API
- Train IsolationForest / rolling z-score ensemble; log runs + models to MLflow
- `anomaly-detector` FastAPI service: polls Prometheus every 30s, scores, exposes `aiops_anomaly_score` back to Prometheus, fires alert on threshold
- Grafana panel: anomaly score overlaid on raw metrics
- **Exit:** inject chaos → anomaly alert fires within a minute. First end-to-end win.

### Phase 3 — Use cases 2 & 3 (days 14–19)
- **Capacity forecasting:** Prophet on CPU/memory/disk trends → "resource X exceeds 80% in ~N hours" predictive alerts + forecast dashboard
- **Alert correlation:** time-window + label clustering (DBSCAN) groups alert storms into one incident; small classifier assigns severity + likely root-cause service (train on labeled chaos runs — you know ground truth because you injected the faults)
- **Exit:** one chaos experiment produces one *correlated incident* with predicted severity, not 15 raw alerts.

### Phase 4 — SRE workflow + gated remediation (days 20–24)
- `incident-orchestrator`: receives Alertmanager webhooks + correlator output, creates incident record, posts rich Slack message (summary, affected service, severity, recommended runbook)
- Slack interactive buttons: **Approve** / **Dismiss**
- `remediation-executor`: safe actions only — scale deployment, restart pods, rollback image. Dry-run mode, RBAC-scoped ServiceAccount, full audit log. Auto-execute only for a small allowlist (e.g., HPA-style scale-up) with automatic rollback check
- **Exit:** the money demo — chaos injected → incident in Slack with prediction → click Approve → platform remediates → recovery visible in Grafana.

### Phase 5 — MLOps hardening (days 25–28)
- GitHub Actions: test → build → push → helm upgrade on merge
- Retraining CronJob: pull fresh metrics → retrain → Evidently drift report → auto-promote to registry `Production` stage only if eval metrics pass; services hot-reload the new model
- Model versioning visible in MLflow UI; rollback = demote in registry
- **Exit:** you can say "models retrain nightly, gated on drift and eval metrics, with one-click rollback" — and show it.

### Phase 6 — Portability + polish (days 29–31, or later)
- README with architecture diagram, demo GIF/video (record the Phase 4 scenario)
- `terraform/aws/` refactored so `terraform/gcp/` is an additive module later
- Docs: design decisions, SLO thinking, what you'd do differently at scale
- Optional later: GKE port to prove the multi-cloud claim

---

## Fastest-path notes

- **Develop on kind/k3d locally**, deploy to EKS only for integration/demo — saves money and iteration time. Same Helm charts run on both (that's your portability proof, day one).
- Cut first if pressed: Chaos Mesh (use manual `kubectl` faults), Evidently (add later), severity classifier (start rule-based, ML later).
- Don't cut: MLflow registry, Slack approval flow, Terraform, teardown script. These carry the core story of the project.

## What this project demonstrates

- Designed and built an AIOps platform on EKS that detects anomalies, forecasts capacity exhaustion, and correlates alert storms into single incidents across an 11-service microservices app
- Implemented full MLOps lifecycle: MLflow experiment tracking and model registry, automated nightly retraining with drift detection (Evidently), metric-gated promotion, and one-click rollback
- Built human-in-the-loop auto-remediation: Slack-integrated incident workflow with approval-gated Kubernetes remediation (scale/restart/rollback), RBAC-scoped and fully audited
- Reduced simulated MTTR by X% and cut alert noise ~Y% via ML alert correlation (measured during chaos runs — real numbers beat adjectives)
- Provisioned reproducible infrastructure with Terraform and Helm, portable across AWS/GCP/Azure

## Cost guardrails

EKS control plane ~$73/mo + 2 spot t3.medium ~$20/mo if left running. With `make down` after each session, expect **$10–25 total**. Set an AWS budget alert at $30 before starting.
