#!/usr/bin/env bash
# Phase 1 deploy: observability stack, demo app, chaos tooling.
# Idempotent — safe to re-run. Works on EKS and kind (current kube context).
set -euo pipefail
cd "$(dirname "$0")/.."

echo ">>> Adding helm repos..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo add chaos-mesh https://charts.chaos-mesh.org >/dev/null
helm repo update >/dev/null

echo ">>> Namespaces..."
for ns in monitoring boutique chaos loadgen aiops mlflow; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done

echo ">>> kube-prometheus-stack (Prometheus, Alertmanager, Grafana)..."
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --values helm/values/monitoring.yaml \
  --wait --timeout 10m

echo ">>> Online Boutique (demo microservices app)..."
kubectl apply -n boutique \
  -f https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/v0.10.2/release/kubernetes-manifests.yaml

echo ">>> Chaos Mesh..."
helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
  --namespace chaos \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
  --set dashboard.create=true \
  --wait --timeout 5m

echo ">>> Argus recording & alerting rules..."
kubectl apply -f observability/rules/

echo ""
echo ">>> Done. Useful commands:"
echo "  kubectl -n boutique get pods                     # demo app status"
echo "  kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80"
echo "      → http://localhost:3000  (admin / argus-admin)"
echo "  kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090"
echo "  make load                                        # start background traffic"
echo "  make chaos-cpu                                   # inject a CPU stress fault"
