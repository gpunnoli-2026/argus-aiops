# Argus

**AIOps Incident Prediction & Automated Response Platform**

Argus is an end-to-end AIOps platform that ingests infrastructure telemetry from a
Kubernetes microservices application, learns each service's normal behaviour to detect
anomalies, forecasts capacity exhaustion, and correlates alert storms into classified
incidents. It runs on AWS (EKS) and Google Cloud (GKE) from one codebase. The closing step —
human-in-the-loop, approval-gated auto-remediation delivered through Slack — is Phase 4,
designed and next to build (see status below).

> Named for Argus Panoptes, the hundred-eyed watchman of Greek myth.

## Status

🚧 **In active development.**

| Phase | Scope | Status |
|---|---|---|
| 0 | Architecture, repo scaffold, Terraform/EKS foundation | ✅ Done |
| 1 | Telemetry & failure lab (Prometheus, Chaos Mesh, k6) | ✅ Done |
| 2 | Anomaly detection (learned per-service baselines + MLflow) | ✅ Done |
| 3 | Capacity forecasting (Prophet) & alert correlation | ✅ Done |
| 4 | Slack incident workflow + gated remediation | 📋 Next |
| 5 | MLOps hardening | 🔶 Partial — gated promotion, rollback, nightly retraining, CI, offline model evaluation on recorded chaos runs done; drift gates (Evidently) planned |
| 6 | Multi-cloud portability & polish | 🔶 Partial — **ported to GCP**: full platform running on GKE inside a Terraform-built landing zone, verified by a measured chaos run (results below); EKS re-measurement with the current model and polish remaining |

### Measured results (live chaos runs)

**GKE, current model (range envelope), 4-hour session**

- **Detection latency:** injected CPU fault → anomaly score > 0.8 in **73 seconds**. The
  score rises with the size of the deviation (0.56 → 0.83 → 0.96 → 1.00 as CPU climbed).
- **False alerts:** **zero** ML alerts in the clean window before the fault, and zero for
  the whole session apart from the fault itself.
- **Root cause:** one incident, correctly attributed to the faulted service, resolved
  automatically once the fault ended.
- **Leak warning:** a demo service leaking memory was flagged at 92% of its limit, before
  it was OOM-killed; the anomaly model flagged a second leaking service at 80%.

**How the model got here**

- *EKS, IsolationForest:* v1 (single-regime baseline) false-alerted under normal traffic;
  v2 (multi-regime baseline: idle/ramp/steady/spike) cut background score noise ~60% and
  detected an injected CPU fault in under 2 minutes. 7 raw alerts folded into 1 incident
  (~86% noise reduction), capturing a noisy-neighbor effect with topology-based
  root-cause inference.
- *GKE, IsolationForest:* the same model did not hold up. On one run a real fault scored
  0.66 and never alerted; on the next, five healthy services alerted within minutes of
  training. Cause: the forest's score stops rising outside its training range, so memory
  0.3 MiB past the trained maximum scored like CPU at seven times it.
- *Replacement:* four models were compared on the recorded run
  ([ml/evaluation/compare_models.py](ml/evaluation/compare_models.py)) — the forest, a
  range envelope, robust Mahalanobis distance and a rolling-median residual. The range
  envelope was chosen: no false alerts on normal traffic, the fault detected in 75 s, and
  it keeps alerting for as long as a fault lasts. The live run above confirmed it.

**Known limits**

- The EKS figures were measured with the IsolationForest and have not been re-measured
  with the current model.
- On the GKE run only the faulted service alerted, so the 7-into-1 noise reduction seen
  on EKS was not reproduced there.
- The correlator groups alerts by time first: two unrelated problems that alert within
  five minutes of each other land in one incident.

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
bucket; fault injection, detection, alerting and correlation end to end (results above). Design and decisions: [docs/gcp-port-design.md](docs/gcp-port-design.md).

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
make load-varied        # 2.5h of idle/ramp/steady/spike traffic — what the model learns "normal" from
make load               # steady traffic for the rest of the session
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
ml/              Training pipeline, offline model evaluation on recorded runs
chaos/           Chaos Mesh experiment library (labeled ground truth)
loadgen/         k6 load profiles
observability/   Dashboards, recording & alerting rules
docs/            Architecture, build plan, runbooks, design decisions
```

## License

Copyright 2026 Gopakumar Punnoli. Licensed under [Apache-2.0](LICENSE); redistributions
must keep the attribution in [NOTICE](NOTICE).
