---
id: service-cpu-saturation
title: Service CPU saturation
alerts: [ServiceAnomalyDetected, ServiceAnomalyCritical]
services: ["*"]
severity: critical
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms

The anomaly model scores one service above 0.8 (ServiceAnomalyDetected) or above 0.95
(ServiceAnomalyCritical). The service's CPU usage is at or near its limit and stays there.
Pods are running and ready, and restart counts are flat. Callers of the service may show
their own anomalies a few minutes later.

## Likely causes

- A traffic surge: the load generator or real users are sending more requests than usual.
- A CPU-heavy code path introduced by a recent deployment.
- CPU limits set too low, so the container is throttled under normal load.
- A chaos experiment: `cpu-stress` targets cartservice with 90% load for five minutes.

## Diagnosis

1. Confirm CPU is the feature that moved: `kubectl top pods -n boutique -l app=<service>`.
2. Compare usage with the limit: `kubectl get deployment <service> -n boutique -o jsonpath='{.spec.template.spec.containers[*].resources}'`.
3. Check for an active chaos experiment: `kubectl get stresschaos -n chaos`.
4. Check for a recent rollout: `kubectl rollout history deployment <service> -n boutique`.
5. Look at request rate on the frontend dashboard in Grafana to tell a surge from a regression.

## Remediation

1. If a chaos experiment is running, remove it: `make chaos-clean`.
2. If traffic is up, scale out: `kubectl scale deployment <service> -n boutique --replicas=3`.
3. If a recent rollout correlates, roll it back: `kubectl rollout undo deployment <service> -n boutique`.
4. If the limit is simply too low, raise the CPU limit in the deployment and redeploy.
5. Confirm the anomaly score falls below 0.8 within two scoring intervals: `make scores`.

## Escalation

Escalate to the owning service team if CPU stays saturated after scaling out, or if the
rollback does not bring the score down within 15 minutes.
