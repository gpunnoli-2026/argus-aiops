---
id: pod-crashloop
title: Pod crash loop / repeated restarts
alerts: [BoutiquePodRestarting]
services: ["*"]
severity: warning
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms

BoutiquePodRestarting fires: a workload's containers have restarted more than twice in the
recording window. Pods show `CrashLoopBackOff` or a climbing RESTARTS count. Memory use is
well below the limit, which separates this from an out-of-memory kill.

## Likely causes

- The process exits on startup: bad configuration, a missing environment variable or secret.
- A dependency the service needs at startup is unreachable, and the service exits instead of retrying.
- A failing liveness probe: the container is healthy but is killed for answering too slowly.
- A bad image from a recent deployment.

## Diagnosis

1. Find the restarting pods: `kubectl get pods -n boutique -l app=<service>`.
2. Read the exit reason: `kubectl describe pod <pod> -n boutique` and look at Last State.
   `Error` with a non-zero exit code points here; `OOMKilled` points to the memory runbook.
3. Read the logs of the crashed container: `kubectl logs <pod> -n boutique --previous`.
4. Check events for probe failures: `kubectl get events -n boutique --sort-by=.lastTimestamp`.
5. Check whether the image changed: `kubectl rollout history deployment <service> -n boutique`.

## Remediation

1. If the crash started with a rollout, roll back: `kubectl rollout undo deployment <service> -n boutique`.
2. If a configuration value or secret is missing, restore it and restart: `kubectl rollout restart deployment <service> -n boutique`.
3. If the liveness probe is too strict, raise its timeout or initial delay and redeploy.
4. If a dependency is down, fix the dependency first; the restarts stop on their own.

## Escalation

Escalate to the owning service team when the logs show an application exception that a
rollback does not clear.
