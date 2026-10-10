---
id: pods-not-ready
title: Pods not ready
alerts: [BoutiquePodsNotReady]
services: ["*"]
severity: warning
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms

BoutiquePodsNotReady fires: a workload has had at least one pod failing its readiness check
for five minutes. The pod is `Running` but shows `0/1` ready, or it is stuck in `Pending` or
`ContainerCreating`. The restart count is not climbing. Requests to the service fail or are
served by fewer replicas.

## Likely causes

- The readiness probe fails because the application has not finished starting or cannot reach a dependency.
- The pod cannot be scheduled: no node has enough CPU or memory, or a spot node was reclaimed.
- The image cannot be pulled.
- A pod was deleted and its replacement is slow to become ready (the `pod-kill` chaos experiment does this).

## Diagnosis

1. See the pod phase and readiness: `kubectl get pods -n boutique -l app=<service> -o wide`.
2. Read the conditions and events: `kubectl describe pod <pod> -n boutique`.
   `FailedScheduling` means capacity; `ImagePullBackOff` means the image; `Readiness probe failed` means the application.
3. Check node health and headroom: `kubectl get nodes` and `kubectl top nodes`.
4. Check for an active chaos experiment: `kubectl get podchaos -n chaos`.

## Remediation

1. Scheduling failure: wait for the node pool to scale, or add a node, then confirm the pod is placed.
2. Image pull failure: correct the image tag or registry credentials and redeploy.
3. Readiness failure caused by a dependency: restore the dependency; the pod becomes ready without a restart.
4. Readiness failure with no clear cause: `kubectl rollout restart deployment <service> -n boutique`.
5. Chaos experiment: `make chaos-clean`, then confirm the replacement pod is ready.

## Escalation

Escalate to the platform team if pods stay unschedulable for more than 15 minutes, since
that means the cluster is out of capacity.
