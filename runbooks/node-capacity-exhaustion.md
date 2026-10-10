---
id: node-capacity-exhaustion
title: Node capacity forecast to run out
alerts: [CapacityExhaustionForecast, CapacityExhaustionImminent]
services: ["*"]
severity: critical
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms

CapacityExhaustionForecast fires when the forecaster predicts a node resource will cross 80%
within 12 hours; CapacityExhaustionImminent fires when that is within 2 hours. The alert's
`resource` label says which resource (disk, memory or CPU) and `instance` says which node.
Nothing is failing yet: this is a prediction, not an outage.

## Likely causes

- Disk: container logs, unused images, or a persistent volume filling up.
- Memory: more pods scheduled on the node than it can hold, or one workload growing.
- Steady growth in traffic that the current node count cannot absorb.
- A forecast extrapolated from a short burst, such as a load test, that will not continue.

## Diagnosis

1. Read the forecast and its inputs: `make forecasts`.
2. Check current usage on the node: `kubectl top node <node>` and `kubectl describe node <node>`.
3. For disk, look for disk-pressure conditions and evictions in the node description.
4. Look at the resource's 24-hour graph in Grafana. A steady ramp is real; a single step that has flattened is not.
5. Check whether a load test or chaos experiment ran in the last six hours.

## Remediation

1. If the trend is real and the node pool can grow, add a node and let pods rebalance.
2. For disk, remove unused images and rotated logs on the node, or expand the volume.
3. For memory, move the heaviest workload: `kubectl cordon <node>`, then restart that deployment.
4. If the forecast came from a short burst, take no action and confirm the alert clears within two forecast runs.
5. For CapacityExhaustionImminent, act on step 1 first and investigate afterwards.

## Escalation

Escalate to the platform team when the node pool is already at its maximum size and the
forecast is still under 12 hours.
