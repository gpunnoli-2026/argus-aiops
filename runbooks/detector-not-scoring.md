---
id: detector-not-scoring
title: Anomaly detector not scoring or has no model
alerts: [DetectorNotScoring, DetectorModelMissing]
services: ["*"]
severity: warning
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms

DetectorNotScoring fires when the anomaly-detector has not finished a scoring loop in five
minutes. DetectorModelMissing fires when it has had no model loaded for 15 minutes. In both
cases anomaly scores are stale or absent, so ServiceAnomalyDetected cannot fire and the
platform is blind to behavioural faults. Static alerts still work.

## Likely causes

- The detector cannot reach Prometheus to read its feature queries.
- The detector cannot reach MLflow, or no model version carries the `production` alias.
- The detector pod is crashing or was evicted.
- A new cluster where no model has been trained yet.

## Diagnosis

1. Read the detector's logs: `make detector-logs`.
2. Check the pod: `kubectl get pods -n aiops -l app=anomaly-detector`.
3. Check that MLflow is up: `kubectl get pods -n mlflow`.
4. Open MLflow and confirm a registered model version has the `production` alias: `make mlflow`.
5. Check that Prometheus is answering queries from the Grafana Explore page.

## Remediation

1. No model on a new cluster: train one with `make train`, then wait one model refresh interval.
2. The `production` alias points at a broken version: `make rollback`.
3. MLflow or Prometheus is down: restore that service first; the detector resumes on its next loop.
4. The detector pod is unhealthy: `kubectl rollout restart deployment anomaly-detector -n aiops`.
5. Confirm scores are fresh: `make scores`.

## Escalation

Escalate to the ML platform owner if a model is present and both dependencies are healthy
but the scoring loop still does not complete.
