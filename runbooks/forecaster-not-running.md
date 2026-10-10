---
id: forecaster-not-running
title: Capacity forecaster not running
alerts: [ForecasterNotRunning]
services: ["*"]
severity: warning
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms

ForecasterNotRunning fires when the capacity-forecaster has not completed a forecast loop in
an hour. Forecast values stop updating, so the capacity alerts either never fire or stay
stuck on their last value. Nothing user-facing is affected.

## Likely causes

- The forecaster cannot reach Prometheus for its range queries.
- Model fitting is failing or taking longer than the loop interval.
- The forecaster pod was killed for memory while fitting, or was evicted.

## Diagnosis

1. Read the forecaster's logs for fit failures or query errors: `make forecaster-logs`.
2. Check the pod and its restart count: `kubectl get pods -n aiops -l app=capacity-forecaster`.
3. If it restarted, read the reason: `kubectl describe pod <pod> -n aiops`.
4. Check the fit-error gauge `aiops_forecaster_fit_errors` in Grafana Explore.

## Remediation

1. Prometheus unreachable: restore Prometheus; the forecaster recovers on its next loop.
2. Pod killed for memory: raise the forecaster's memory limit and redeploy.
3. Pod unhealthy with no clear cause: `kubectl rollout restart deployment capacity-forecaster -n aiops`.
4. Confirm forecasts are updating again: `make forecasts`.

## Escalation

Escalate to the ML platform owner if fitting fails for every series after a restart.
