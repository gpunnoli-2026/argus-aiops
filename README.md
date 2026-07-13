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
| 0 | Architecture, repo scaffold, Terraform/EKS foundation | 🚧 In progress |
| 1 | Telemetry & failure lab (Prometheus, Chaos Mesh, k6) | 📋 Planned |
| 2 | Anomaly detection (IsolationForest + MLflow) | 📋 Planned |
| 3 | Capacity forecasting (Prophet) & alert correlation | 📋 Planned |
| 4 | Slack incident workflow + gated remediation | 📋 Planned |
| 5 | MLOps hardening (retraining, drift gates, CI/CD) | 📋 Planned |
| 6 | Multi-cloud portability & polish | 📋 Planned |

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

> Coming with Phase 0 completion.

```bash
make up      # provision EKS + deploy platform
make demo    # inject fault, watch the incident flow
make down    # tear everything down (always run this)
```

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
