#!/usr/bin/env bash
# Deploys: observability stack, demo app, chaos tooling, Argus platform services.
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

echo ">>> Grafana admin credentials (generated once, stored as a Secret)..."
if ! kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n monitoring create secret generic grafana-admin \
    --from-literal=admin-user=admin \
    --from-literal=admin-password="$(openssl rand -hex 16)"
fi

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

echo ">>> Argus platform (MLflow, detector, forecaster, correlator)..."
# service code rides in ConfigMaps until Phase 5 CI/CD builds real images
kubectl -n aiops create configmap argus-detector-code \
  --from-file=services/anomaly-detector/ \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n aiops create configmap argus-forecaster-code \
  --from-file=services/capacity-forecaster/ \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n aiops create configmap argus-correlator-code \
  --from-file=services/alert-correlator/ \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n aiops create configmap argus-training-code \
  --from-file=ml/training/train_anomaly.py \
  --dry-run=client -o yaml | kubectl apply -f -

echo ">>> Scheduled retraining (nightly, gated promotion)..."
kubectl apply -f ml/training/retrain-cronjob.yaml

EXTRA_ARGS=""
if command -v terraform >/dev/null 2>&1 && [ -f terraform/aws/terraform.tfstate ]; then
  BUCKET=$(terraform -chdir=terraform/aws output -raw artifact_bucket 2>/dev/null || true)
  ROLE=$(terraform -chdir=terraform/aws output -raw mlflow_irsa_role_arn 2>/dev/null || true)
  if [ -n "$BUCKET" ]; then
    EXTRA_ARGS="--set artifactBucket=$BUCKET --set mlflowRoleArn=$ROLE"
    echo "    using S3 artifacts: $BUCKET"
  fi
else
  echo "    no terraform state found — MLflow will use PVC-local artifacts (kind mode)"
fi

# shellcheck disable=SC2086
helm upgrade --install argus helm/platform \
  --namespace aiops \
  $EXTRA_ARGS \
  --wait --timeout 10m

echo ""
echo ">>> Done. Useful commands:"
echo "  kubectl -n boutique get pods                     # demo app status"
echo "  kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80"
echo "      → http://localhost:3000  (admin / make grafana-password)"
echo "  make load / make load-varied                     # background traffic"
echo "  make chaos-cpu                                   # inject a CPU stress fault"
echo "  make forecasts / make incidents                  # Phase 3 outputs"
