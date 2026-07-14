#!/usr/bin/env bash
# Phase 1 deploy: observability stack, demo app, chaos tooling.
# Idempotent — safe to re-run. Works on EKS and kind (current kube context).
set -euo pipefail
cd "$(dirname "$0")/.."

# Git Bash / MSYS on Windows rewrites Unix-style path arguments (e.g. helm --set
# socketPath=/run/...) into C:/Program Files/Git/... — disable that conversion.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

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
  --set controllerManager.replicaCount=1 \
  --set dashboard.create=true \
  --wait --timeout 5m

echo ">>> Argus recording & alerting rules..."
kubectl apply -f observability/rules/

echo ">>> Argus platform (MLflow + anomaly detector)..."
# service code rides in ConfigMaps until Phase 5 CI/CD builds real images
kubectl -n aiops create configmap argus-detector-code \
  --from-file=services/anomaly-detector/ \
  --dry-run=client -o yaml |