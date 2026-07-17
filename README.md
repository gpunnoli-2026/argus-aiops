# Argus

**AIOps Incident Prediction & Automated Response Platform**

Argus is an end-to-end AIOps platform that ingests infrastructure telemetry from a
Kubernetes microservices application, applies ML to detect anomalies, forecast capacity
exhaustion, and correlate alert storms into classified incidents — then closes the loop
with human-in-the-loop, approval-gated auto-remediation delivered through Slack.

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
| 5 | MLOps hardening (retraining, drift gates, CI/CD) | 📋 Planned |
| 6 | Multi-cloud portability & polish | 📋 Planned |

### Measured results (live chaos runs on EKS)

- **Detection latency:** injected CPU fault → ML anomaly score > 0.8 in **under 2 minutes**
- **Model iteration:** v1 (single-regime baseline) false-alerted under normal traffic;
  v2 (multi-regime baseline: idle/ramp/steady/spike) cut background score noise **~60%**,
  zero false alerts in a clean window, while still detecting real faults decisively —
  promoted live via MLflow registry alias flip, no redeploy
- **Alert correlation:** 7 raw alerts folded into **1 incident** (~86% noise reduction),
  correctly capturing a noisy-neighbor effect (CPU stress on one service pushed
  co-located services into anomaly), with topology-based root-cause inference

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full high-level and detailed
architecture, and [docs/plan.md](docs/plan.md) for the phased build plan.

```
Chaos fault injected
  → Online Boutique degrades
  → Prometheus metrics / Alertmanager alerts
  → ML services: anomaly score, capacity forecast, alert correlation
  → One classified incident posted to Slack with recommended runbook
  → [Approve] → RBAC-scoped remediation (scale/restart/rollback), audited
  → Grafana shows recovery
```

## Stack

Kubernetes (EKS) · Terraform · Helm · Prometheus/Alertmanager/Grafana · Chaos Mesh · k6 ·
Python · scikit-learn · Prophet · MLflow · Evidently · FastAPI · Slack (Socket Mode) ·
GitHub Actions

## Quickstart

```bash
make up            # provision VPC + EKS + S3 (~15 min)
make deploy        # observability stack, demo app, chaos tooling, Argus services
make load          # baseline traffic (bake ≥2h before first training)
make train         # train anomaly models, register in MLflow (@production)
make chaos-cpu     # inject a fault — watch detection, alerting, correlation
make incidents     # correlated incidents with root-cause inference
make forecasts     # capacity projections per node resource
make down          # tear everything down (always run this)
```

`make help` lists all targets. Local dev loop without AWS: `make kind-up`.

## Repository layout

```
terraform/       Infrastructure as code (aws/ now; gcp/, azure/ planned)
helm/            Platform umbrella chart + per-target values
services/        FastAPI microservices (detection, correlation, orchestration, remediation)
ml/              Training pipelines, evaluation, drift checks
chaos/           Chaos Mesh experiment library (labeled ground truth)
loadgen/         k6 load profiles
observability/   Dashboards, recording & alerting rules
docs/            Architecture, build plan, runbooks, design decisions
```

## License

[Apache-2.0](LICENSE)
