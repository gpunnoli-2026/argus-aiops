---
id: dependency-latency
title: Slow dependency degrading its callers
alerts: [ServiceAnomalyDetected]
services: ["*"]
severity: warning
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms

ServiceAnomalyDetected fires on two or more services that call each other, for example
productcatalogservice together with recommendationservice, checkoutservice and frontend.
The correlator names the deepest one as the root. No pods are restarting or unready, and
CPU on the root service is not saturated. Pages load slowly but do not fail outright.

## Likely causes

- Network delay or packet loss between the callers and the root service.
- The root service is slow to answer: lock contention, a slow downstream call, or a cold cache.
- A chaos experiment: `network-delay` adds 500 ms to productcatalogservice for five minutes.
- A node with a degraded network interface hosting the root service's pods.

## Diagnosis

1. Read the incident and confirm the root is a dependency of the other alerted services: `make incidents`.
2. Check for an active chaos experiment: `kubectl get networkchaos -n chaos`.
3. Find which node hosts the root service: `kubectl get pods -n boutique -l app=<root-service> -o wide`.
4. Compare request latency for the root service and its callers in Grafana. The root slows first.
5. Rule out saturation: `kubectl top pods -n boutique -l app=<root-service>`.

## Remediation

1. If a chaos experiment is running, remove it: `make chaos-clean`.
2. If one node is the common factor, move the pods off it: `kubectl cordon <node>`, then `kubectl rollout restart deployment <root-service> -n boutique`.
3. If the root service is slow without a network cause, scale it out: `kubectl scale deployment <root-service> -n boutique --replicas=3`.
4. Do not restart the callers; they recover when the dependency does.

## Escalation

Escalate to the platform team if latency persists across nodes with no chaos experiment
active, since that points to the cluster network.
