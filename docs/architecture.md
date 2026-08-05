# Argus — Platform Architecture

Two levels: the **high-level architecture** (system context and layers) and the **detailed architecture** (component contracts, data models, and flows).

This doc started as the Day-1 design and is kept in sync with the build as phases land.
Component specs in Part 2 are tagged **✅ implemented** (describes the running code) or
**📋 designed** (target spec for a later phase, not yet built). Where the implementation
deliberately diverged from the original design, the section says so and why.

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
| **4. Intelligence** | Detect, predict, correlate | anomaly-detector, capacity-forecaster, alert-correlator (FastAPI + models from registry); LLM diagnostic layer (RAG-grounded narrative, standalone for now) |
| **5. Action** | Turn predictions into SRE workflow | incident-orchestrator, Slack integration, remediation-executor (gated) — **Phase 4, designed not built** |
| **6. MLOps** | Model lifecycle | MLflow tracking/registry, gated promotion + rollback, nightly retraining CronJob, GitHub Actions CI/CD; Evidently drift checks planned |

## 1.3 Primary data flow (the demo story)

```
Chaos Mesh injects fault
   → Online Boutique degrades
   → Prometheus scrapes metrics / Alertmanager fires raw alerts
   → anomaly-detector scores metric streams (30s loop)
   → alert-correlator folds alerts + anomalies into ONE incident,
     infers likely root-cause service from the dependency topology
   ---- everything below is Phase 4 (designed, not yet built) ----
   → incident-orchestrator posts incident to Slack with
     recommended runbook + [Approve] [Dismiss] buttons
   → on Approve: remediation-executor performs safe action
     (scale / restart / rollback) with audit log
   → Grafana shows recovery; incident auto-resolves
```

Parallel flow: **capacity-forecaster** continuously projects resource trends and raises *predictive* alerts ("node pool exhausts memory in ~6h") into the same incident pipeline.

MLOps flow (implemented): **nightly CronJob** pulls fresh Prometheus data → retrains →
alarm-rate promotion gate → promote `@production` alias in the registry → services
hot-reload within 5 min. Evidently drift reporting and a recall gate against labeled
chaos windows are planned (Phase 5).

## 1.4 Key architectural principles

1. **Kubernetes-native core, cloud-specific edge.** Every component runs in-cluster via Helm. Cloud specifics (cluster provisioning, object storage, IAM) are isolated behind Terraform modules and env vars → AWS today, GKE/AKS as additive modules later.
2. **Human-in-the-loop by default, automation by allowlist.** All remediations are recommendations unless the action is on a narrow, pre-approved safe list; everything is audited and dry-run-able.
3. **Closed feedback loop.** Injected faults are labeled ground truth → training data → measurable precision/recall and MTTR numbers.
4. **Models are cattle.** Services load models by registry alias (`@production`), never by file path. Promotion, rollback, and lineage live in MLflow; promote/rollback are alias flips, no redeploy.
5. **Deterministic causality, LLM narration.** Root cause is decided by the deterministic correlation layer; the LLM only explains and drafts, grounded in retrieved runbooks. The LLM never decides causality.

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

### anomaly-detector (Python/FastAPI) — ✅ implemented
- **Loop:** every 30s, query Prometheus HTTP API for feature vector per service.
- **Features (per service):** `cpu_rate`, `mem_ws_bytes`, `restarts_delta`, `pods_not_ready`
  — read from the `aiops:svc:*` recording rules (5m windows), the single feature
  definition shared by training and serving (no train/serve skew).
  *Planned:* p50/p95/p99 latency, request_rate, error_rate — the current feature set is
  resource-level only and cannot directly see latency/error faults.
- **Model:** IsolationForest per service (`n_estimators=100`, `contamination=0.02`)
  behind a StandardScaler, plus a `__global__` fallback for services with too few
  samples or unseen at serve time. Scores calibrated to [0,1] by inverting and
  min-max scaling the decision function over the training window.
  *Considered, not built:* a rolling z-score ensemble alongside the forest.
- **Seasonality handling (v2):** data-centric — the model is trained on multi-regime
  traffic (idle/ramp/steady/spike k6 profile) so load variation is inside the learned
  normal envelope. No time-of-day features; regime-agnostic, not regime-aware.
- **Outputs:**
  - `GET /metrics` → exposes `aiops_anomaly_score{service=...}` (scraped by Prometheus)
  - `GET /scores` → latest scores as JSON; `GET /healthz`
- **Alerting:** PrometheusRule: `aiops_anomaly_score > 0.8 for 2m` → warning;
  `> 0.95 for 1m` → critical. Meta-alerts cover a stalled scoring loop and a missing model.
- **Model loading:** MLflow client, alias `models:/argus-anomaly@production`, re-resolved every 5m.

### capacity-forecaster (Python/FastAPI) — ✅ implemented
- **Loop:** every 15m, pull 12h of node-level cpu, memory, and disk utilization
  (5m resolution). *Planned:* 7d history with weekly seasonality, per-service request-rate series.
- **Model:** Prophet per (resource, instance) — daily seasonality only,
  `changepoint_prior_scale=0.1`; projects 12h ahead against an 80% threshold.
- **Output:** `aiops_forecast_hours_to_threshold{resource=..., instance=...}` gauge
  (sentinel 999 = no crossing in horizon); PrometheusRules fire at < 12h (warning)
  and < 2h (critical), plus a meta-alert if the loop stalls.
- **API:** `GET /forecasts` → current projections as JSON; `GET /healthz`.

### alert-correlator (Python/FastAPI) — ✅ implemented
- **Input:** Alertmanager webhook (all firing + resolved alerts, including ML anomaly alerts).
- **Correlation (deterministic, no clustering):** sliding 5m activity window. An
  incoming alert joins an open incident that already involves its service or a
  topology neighbour; failing that, any open incident inside the window (time
  proximity alone still groups noisy-neighbour effects topology can't see);
  failing that, a new incident is opened.
  *Design note:* the original design called for DBSCAN over (time, label similarity).
  The shipped window+topology fold is simpler, explainable, and unit-testable, and has
  been sufficient at demo scale; clustering remains an option if alert volume outgrows it.
- **Topology:** a static service-dependency map of Online Boutique (service → services
  it calls), maintained in code. Static is a deliberate v1 choice — the demo app's call
  graph is known and stable. *Planned:* derive topology from service-mesh/eBPF telemetry.
- **Root-cause inference (rule-based):** among alerted services (infra pseudo-services
  excluded), candidates are those whose own dependencies are all healthy; tie-break by
  most alerted direct dependents. Severity = max severity label seen. An incident
  resolves when all its alertnames have resolved.
  *Considered, not built:* learned severity/root-cause classifier trained on labeled chaos runs.
- **State:** in-memory, capped at 200 incidents (lost on restart — acceptable for the
  demo loop; Phase 4 moves incident state to Postgres).
- **Output:** `GET /incidents` + Prometheus counters/gauges. Phase 4 forwards incidents
  to the incident-orchestrator (schema §2.3).

### llm-diagnostic layer (Python, `src/llm_diagnostic.py`) — ✅ implemented (standalone)
- **Boundary:** takes the STRUCTURED incident from the deterministic correlation stage
  (root cause already decided) and (1) retrieves the most relevant runbook via RAG,
  (2) has an LLM write a root-cause narrative + draft a Jira-style ticket grounded in
  that runbook. **The LLM never decides causality.**
- **RAG:** sentence-transformers `all-MiniLM-L6-v2` + Chroma (in-memory) on the
  production path; whole-runbook embeddings, top-1 retrieval. Offline fallback
  (default configuration): keyword-overlap scoring — no model downloads or API key
  needed, so the demo runs anywhere.
- **LLM:** Anthropic `claude-sonnet-5` behind a strict JSON-only contract; offline
  fallback is a deterministic stub emitting the same JSON shape. After parsing, the
  code stamps `runbook_used` from the actual retrieval result — ground truth, not the
  model's claim.
- **Integration status:** standalone demo (`python src/llm_diagnostic.py`); wiring the
  correlator's incidents into it is Phase 4 work, alongside chunked runbooks and top-k
  retrieval as the runbook library grows.

### incident-orchestrator (Python/FastAPI) — 📋 designed, Phase 4 (not yet built)
- **Responsibilities:** incident lifecycle (open → acknowledged → remediating → resolved), dedup, Slack posting, approval handling, audit trail.
- **State store:** Postgres (shared instance with MLflow, separate DB).
- **Slack:** Block Kit message — summary, severity, root-cause guess, metric snapshot link, recommended runbook, `[Approve remediation] [Dismiss]`. Socket Mode interaction handler.
- **Auto-execute path:** if recommended action ∈ allowlist AND severity ≤ SEV3 → execute with 60s cancel window posted to Slack.

### remediation-executor (Python/FastAPI) — 📋 designed, Phase 4 (not yet built)
- **Actions (v1 allowlist):**
  | Action | Params | Safety check before | Verify after |
  |---|---|---|---|
  | scale_deployment | ns, name, +replicas (max 2× current, cap 10) | HPA absent/not fighting | pods Ready in 3m |
  | restart_deployment | ns, name | not restarted in last 10m | rollout complete |
  | rollback_deployment | ns, name | previous ReplicaSet exists | error rate drops 5m |
- **Execution:** Kubernetes Python client; ServiceAccount RBAC scoped to `boutique` namespace, verbs: get/list/patch on deployments only.
- **Modes:** `dry_run` (default in config), `gated` (Slack approval), `auto` (allowlist only).
- **Audit:** every request/decision/result → structured JSON log + `remediation_audit` table.

### retraining pipeline (K8s CronJob, nightly) — ✅ implemented (anomaly model)
```
pull 6h feature window (Prometheus recording rules)
  → train per-service IsolationForest bundle + global fallback
  → promotion gate:
      background alarm rate on the training window ≤ 5%
      AND not > 2% noisier than the current @production model
      on that same window
  → pass: register version + flip @production alias (services
          hot-reload within 5m)
     fail: register version, keep old alias, exit non-zero
           (CronJob shows failed — a bad night cannot demote quality)
Rollback: --rollback / make rollback flips @production to the
previous registered version. No redeploy in either direction.
```
*Known limitation (deliberate scope):* the gate is a false-positive (noise) gate only —
it does not measure detection ability. *Planned (Phase 5):* recall/precision evaluation
against labeled chaos windows, Evidently drift reports, forecast MAPE.

## 2.3 Data contracts

**Incident document (correlator → orchestrator) — 📋 Phase-4 contract** (today the
correlator serves its incidents at `GET /incidents`; `recommended_action` and
`forecast_context` enrichment arrive with the orchestrator):
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

**Feature vector (Prometheus recording rules, per service, 5m window) — ✅ implemented:**
`cpu_rate, mem_ws_bytes, restarts_delta, pods_not_ready` — recorded as `aiops:svc:<metric>`
series so training and inference read identical definitions (no train/serve skew).
*Planned:* `latency_p50/p95/p99, req_rate, err_rate`.

**Training label source — 📋 designed:** Chaos Mesh experiment CRs are logged (type, target, start/end) by a small `chaos-labeler` job → `labels.parquet` in S3. Anomaly windows = experiment windows. Today the chaos experiments themselves are the (manually applied) ground truth; the labeler job lands with the Phase-5 eval gate.

## 2.4 Repository layout

```
argus-aiops/
├── terraform/aws/            # VPC, EKS, S3, IAM (gcp/, azure/ later)
├── helm/
│   ├── platform/             # umbrella chart: aiops services
│   └── values/               # per-target values
├── services/
│   └── anomaly-detector/  capacity-forecaster/  alert-correlator/
│       # Phase 4 adds: incident-orchestrator/, remediation-executor/
├── src/                      # LLM diagnostic layer (standalone)
├── ml/
│   └── training/             # anomaly training + gated promotion, retrain CronJob
│       # Phase 5 adds: evaluation/ (chaos-window eval + Evidently drift)
├── chaos/                    # Chaos Mesh experiment library
├── loadgen/                  # k6 scenarios (steady + multi-regime varied)
├── observability/            # recording/alerting rules, dashboards
├── tests/                    # unit tests over the pure logic
├── .github/workflows/        # ci.yaml (lint, terraform fmt, unit tests)
├── docs/                     # this file, runbooks/, plan
└── Makefile                  # up/down/deploy/train/chaos/demo/teardown
```

## 2.5 Security model

- **IRSA** for anything touching AWS (MLflow→S3); no static keys in-cluster. ✅
- Grafana admin password generated at deploy time (never committed); retrieved via `make grafana-password`. ✅
- **remediation-executor** (Phase 4) will be the only component with K8s write access; RBAC limited to `deployments` in `boutique` ns; no cluster-admin anywhere. 📋
- Slack signing-secret verification on all interaction payloads; approval identity recorded in audit log. 📋 Phase 4
- NetworkPolicies: aiops ns → monitoring (Prometheus API) and mlflow only; boutique cannot reach aiops. 📋
- Secrets: External Secrets Operator → AWS Secrets Manager (kind: plain K8s secrets). 📋

## 2.6 Portability abstraction

| Concern | Abstraction | AWS impl | Later GCP/Azure |
|---|---|---|---|
| Cluster | Terraform module interface (`cluster` outputs kubeconfig) | EKS | GKE / AKS module |
| Object storage | S3-compatible endpoint env vars | S3 | GCS (S3-interop) / MinIO gateway |
| Secrets | External Secrets Operator | Secrets Manager | GCP SM / Key Vault |
| Everything else | Helm charts, unchanged | — | — |

## 2.7 Observability of the platform itself (meta-monitoring)

Each AIOps service exposes `/metrics` today: scoring-loop errors and last-run timestamps, model-loaded state, alert/incident counters, forecast fit errors — with PrometheusRules alerting on a stalled detector/forecaster or a missing model. Phase 4 adds Slack delivery failures and remediation success/failure counters. An SRE platform must itself be observable.

## 2.8 Platform KPIs

- Detection latency: fault injection → anomaly alert (target < 60s)
- Alert noise reduction: raw alerts vs correlated incidents per chaos run
- Simulated MTTR: fault → recovery, gated-auto vs manual baseline
- Model quality: precision/recall on held-out chaos windows; forecast MAPE
- Forecast lead time: hours of warning before threshold breach
