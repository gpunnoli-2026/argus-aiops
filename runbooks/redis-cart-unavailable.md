---
id: redis-cart-unavailable
title: Cart store (redis-cart) unavailable
alerts: [BoutiquePodsNotReady, BoutiquePodRestarting, ServiceAnomalyDetected]
services: [redis-cart, cartservice]
severity: critical
owner: platform-sre
last_reviewed: 2026-10-08
---
## Symptoms

redis-cart has pods not ready or restarting, and cartservice shows an anomaly or its own
readiness failures shortly after. Adding to cart and viewing the cart fail on the frontend,
and checkout fails because it reads the cart. The correlator names redis-cart as the root.

## Likely causes

- The redis-cart pod was killed, evicted, or lost with a reclaimed spot node.
- redis-cart ran out of memory; it keeps every cart in memory with no persistence.
- cartservice cannot resolve or reach the redis-cart service address.

## Diagnosis

1. Check the store: `kubectl get pods -n boutique -l app=redis-cart -o wide`.
2. Read why it stopped: `kubectl describe pod <redis-cart-pod> -n boutique`.
3. Confirm cartservice is failing on Redis: `kubectl logs deployment/cartservice -n boutique --tail=50`.
4. Confirm the service has endpoints: `kubectl get endpoints redis-cart -n boutique`.

## Remediation

1. If the redis-cart pod is missing or stuck, restart it: `kubectl rollout restart deployment redis-cart -n boutique`.
2. Wait for it to be ready, then restart cartservice so it reconnects: `kubectl rollout restart deployment cartservice -n boutique`.
3. If redis-cart was killed for memory, raise its memory limit before restarting.
4. Tell support that carts created before the restart are lost; the store is not persistent.

## Escalation

Escalate to the cart team if cartservice still fails with redis-cart ready and endpoints
present.
