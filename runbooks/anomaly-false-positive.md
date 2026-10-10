---
id: anomaly-false-positive
title: Anomaly alert with no real fault (model drift)
alerts: [ServiceAnomalyDetected]
services: ["*"]
severity: info
owner: ml-platform
last_reviewed: 2026-10-08
---
## Symptoms

ServiceAnomalyDetected fires on one service, or on many unrelated services at once, with no
static alert alongside it. Pods are ready, nothing is restarting, memory and CPU are inside
their limits, and users see no errors. The score hovers just above 0.8 and does not climb.

## Likely causes

- The traffic pattern changed legitimately, for example a new load profile, and the model has not seen it.
- A model was promoted last night from an unrepresentative training window.
- A deployment changed a service's normal resource profile.
- A feature value falls outside the range recorded at training time.

## Diagnosis

1. Read the scores for every service: `make scores`. Many services just over the threshold suggests the model, not a fault.
2. Check that no static alert is firing for the same service in Alertmanager.
3. Check when the production model was last promoted: `make mlflow`.
4. Check whether the load profile changed: `kubectl get jobs -n loadgen`.
5. Confirm the service is healthy: `kubectl get pods -n boutique -l app=<service>`.

## Remediation

1. If the alerts started after last night's promotion, restore the previous model: `make rollback`.
2. If the workload changed for good, retrain on recent data: `make train`.
3. Take no action on the service itself; restarting or scaling it does not change the score.
4. Record the case so it can be added to the model's evaluation data.

## Escalation

Escalate to the ML platform owner if scores stay above the threshold after a rollback and a
retrain.
