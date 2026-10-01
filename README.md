# Argus

**AIOps Incident Prediction & Automated Response Platform**

Argus is an end-to-end AIOps platform that ingests infrastructure telemetry from a
Kubernetes microservices application, applies ML to detect anomalies, forecast capacity
exhaustion, and correlate alert storms into classified incidents. The closing step —
human-in-the-loop, approval-gated auto-remediation delivered through Slack — is Phase 4,
designed and next to build (see status below).

> Named for Argus Panoptes, the hundred-eyed watchman of Greek myth.

## Status

🚧 **In active development.**

| Phase | Scope | Status |
|---|---|---|
| 0 | Architecture, repo scaffold, Terraform/EKS foundation | ✅ Done |
| 1 | Telemetry & failure lab (Prometheus, Chaos Mesh, k6) | ✅ Done |
| 2 | Anomaly detection (IsolationForest + MLflow) | ✅ Done |
| 3 | Capacity forecasting (Prophet) & alert correlation | ✅ Done |
| 4 | Slack incident workflow + gated remediation | 📋 Next |
| 5 | MLOps hardening | 🔶 Partial — gated promotion, rollback, nightly retraining, CI done; drift gates (Evidently) + chaos-window eval planned |
| 6 | Multi-cloud portability & polish | 🔶 Partial — **ported to GCP**: full platform deployed and running on GKE inside a Terraform-built landing zone; measured parity run (the EKS results below, repeated on GKE) next |

### Measured results (live chaos runs on EKS)

- **Detection latency:** injected CPU fault → ML anomaly score > 0.8 in **under 2 minutes**
- **Model iteration:** v1 (single-regime baseline) false-alerted under normal traffic;
  v2 (multi-regime baseline: idle/ramp/steady/spike) cut background score noise **~60%**,
  zero false alerts in a clean window, while still detecting real faults decisively —
  promoted live via MLflow registry alias flip, no redeploy
- **Alert correlation:** 7 raw alerts folded into **1 incident** (~86% noise reduction),
  correctly capturing a noisy-neighbor effect (CPU stress on one service pushed
  co-located services into anomaly), with topology-based root-cause inference

### Running on GCP

The same Helm charts, services and `make up | deploy | down` workflow run on GKE; only the
cloud edge differs. GCP gets a small landing zone modelled on Google's
`terraform-example-foundation`, built in four Terraform stages, each running as its own
service account:

| Stage | Builds |
|---|---|
| `0-bootstrap` | Remote state bucket, per-stage service accounts, keyless GitHub Actions (Workload Identity Federation) |
| `1-org` | Environment folders, projects, org policies (no SA keys, no external VM IPs, US-only), central audit-log sink, budget |
| `2-networks` | Shared VPC host with the GKE subnet; the workload project attaches as a service project |
| `3-apps` | GKE Standard cluster (private Spot nodes, Dataplane V2, Workload Identity), MLflow bucket, Cloud NAT. The only stage `make up/down` touches |

Verified on GKE: all platform workloads running, including Chaos Mesh's privileged
`chaos-daemon` under the org guardrails; MLflow serving artifacts from GCS through
Workload Identity with no keys; audit logs from every project landing in one central
bucket. Design and decisions: [docs/gcp-port-design.md](docs/gcp-port-design.md).

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full high-level and detailed
architecture, and [docs/plan.md](docs/plan.md) for the phased build plan.

```
Chaos fault injected
  → Online Boutique degrades
  → Prometheus metrics / Alertmanager alerts
  → ML services: anomaly score, capacity forecast, alert correlation
    (deterministic root-cause inference; incidents at /incidents)
  → LLM diagnostic layer drafts narrative + Jira ticket, RAG-grounded in
    the matching runbook (LLM never decides causality) — standalone demo
    today, wired into the incident pipeline in Phase 4
  ------------------- Phase 4 (designed, not yet built) -------------------
  → One classified incident posted to Slack with recommended runbook
  → [Approve] → RBAC-scoped remediation (scale/restart/rollback), audited
  → Grafana shows recovery
```

## Stack

Kubernetes (EKS · GKE) · Terraform · Helm · Prometheus/Alertmanager/Grafana · Chaos Mesh · k6 ·
Python · scikit-learn · Prophet · MLflow · FastAPI ·
Anthropic Claude (RAG-grounded diagnostic narrative) · GitHub Actions ·
*Phase 4/5:* Slack (Socket Mode) · Evidently

## Quickstart

```bash
make up CLOUD=aws       # provision VPC + EKS + S3 (~15 min) — CLOUD=gcp gives GKE + GCS on the landing zone's shared VPC (docs/gcp-port-design.md §12–16)
make deploy CLOUD=aws   # observability stack, demo app, chaos tooling, Argus services
make load               # baseline traffic (bake ≥2h before first training)
make train              # train anomaly models, register in MLflow (@production)
make chaos-cpu          # inject a fault — watch detection, alerting, correlation
make incidents          # correlated incidents with root-cause inference
make forecasts          # capacity projections per node resource
make down CLOUD=aws     # tear everything down (always run this)

python src/llm_diagnostic.py   # standalone demo: RAG-grounded narrative + ticket draft
                                # (runs fully offline — no API key needed)
```

`CLOUD` has no default — `make down` against the wrong cloud is the one cheap mistake
this repo can make expensive, so every infrastructure target requires it, and teardown
also checks the live kubectl context agrees.

`make help` lists all targets. Local dev loop without a cloud account:
`make kind-up && make deploy CLOUD=kind`.

The demo app is deployed without its public load balancer (k6 drives it in-cluster);
`make frontend-public` creates one on demand.

## Repository layout

```
terraform/       Infrastructure as code (aws/ = EKS+S3; gcp/ = landing-zone stages 0-bootstrap … 3-apps, GKE+GCS; azure/ later)
helm/            Platform umbrella chart + per-cloud values overlays (aws/ gcp/ kind/)
services/        FastAPI microservices (detection, forecasting, correlation; Phase 4 adds orchestration + remediation)
src/             LLM diagnostic layer — RAG-grounded incident narrative + ticket drafting
ml/              Training pipelines, evaluation, drift checks
chaos/           Chaos Mesh experiment library (labeled ground truth)
loadgen/         k6 load profiles
observability/   Dashboards, recording & alerting rules
docs/            Architecture, build plan, runbooks, design decisions
```

## License

Copyright 2026 Gopakumar Punnoli. Licensed under [Apache-2.0](LICENSE); redistributions
must keep the attribution in [NOTICE](NOTICE).
