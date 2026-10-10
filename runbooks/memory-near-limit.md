---
id: memory-near-limit
title: Container memory near limit / OOMKilled
alerts: [BoutiqueMemoryNearLimit, BoutiquePodRestarting]
services: ["*"]
severity: warning
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms

BoutiqueMemoryNearLimit fires: a workload's working-set memory has been above 90% of its
memory limit for five minutes. If the container crosses the limit the kernel kills it, the
pod restarts, and BoutiquePodRestarting follows. The anomaly model often stays quiet, because
a slow leak during the training window is learned as normal.

## Likely causes

- A memory leak: usage climbs steadily and never returns to its baseline.
- The memory limit is too low for the service's normal working set.
- A traffic increase that raises per-request memory, such as larger carts or caches.
- A recent deployment that changed memory behaviour.

## Diagnosis

1. Compare usage with the limit: `kubectl top pods -n boutique -l app=<service>`.
2. Confirm an out-of-memory kill: `kubectl describe pod <pod> -n boutique` shows Last State `OOMKilled`, exit code 137.
3. Look at the memory graph for the service over six hours. A steady ramp is a leak; a flat line near the limit is an undersized limit.
4. Check whether the ramp started at a rollout: `kubectl rollout history deployment <service> -n boutique`.

## Remediation

1. To buy time, restart the workload so memory returns to baseline: `kubectl rollout restart deployment <service> -n boutique`.
2. If the limit is undersized, raise the memory limit and request in the deployment and redeploy.
3. If a leak started with a rollout, roll back: `kubectl rollout undo deployment <service> -n boutique`.
4. Confirm usage settles below 80% of the limit and the restart count stops rising.

## Escalation

Escalate to the owning service team with the memory graph when usage ramps again after a
restart; a leak needs a code fix, not a larger limit.
