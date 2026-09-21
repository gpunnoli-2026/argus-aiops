#!/usr/bin/env bash
# Deploys: observability stack, demo app, chaos tooling, Argus platform services.
# Idempotent — safe to re-run. Deploys into the current kube context; CLOUD
# selects which values overlay describes that cluster (EKS, GKE or kind).
set -euo pipefail
cd "$(dirname "$0")/.."

# Git Bash / MSYS on Windows rewrites Unix-style path arguments (e.g. helm --set
# socketPath=/run/...) into C:/Program Files/Git/... — disable that conversion.
# Keep every path handed to helm/kubectl repo-relative because of it.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

CLOUD="${CLOUD:-}"
case "$CLOUD" in
  aws | gcp | kind) ;;
  "")
    echo "ERROR: CLOUD is required. Use: make deploy CLOUD=aws|gcp|kind" >&2
    exit 1
    ;;
  *)
    echo "ERROR: unknown CLOUD='$CLOUD' (expected aws, gcp or kind)" >&2
    exit 1
    ;;
esac
echo ">>> Target: $CLOUD  (context: $(kubectl config current-context))"

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
  --values "helm/values/$CLOUD/monitoring.yaml" \
  --wait --timeout 10m

echo ">>> Online Boutique (demo microservices app)..."
kubectl apply -n boutique \
  -f https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/v0.10.2/release/kubernetes-manifests.yaml

# The upstream manifest exposes the frontend through a cloud load balancer. k6
# drives the in-cluster Service, so that LB is pure cost and a public,
# unauthenticated surface for every session. Opt back in: make frontend-public.
kubectl -n boutique delete svc frontend-external --ignore-not-found

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

# Per-deployment facts (bucket URI, pod identity) come from Terraform as a
# ready-made values document — deploy.sh never learns which cloud it is on.
# Repo-relative path: MSYS_NO_PATHCONV above would mangle an absolute /tmp one.
TF_VALUES=".terraform-helm-values.json"
EXTRA_ARGS=()
trap 'rm -f "$TF_VALUES"' EXIT

if [ "$CLOUD" != "kind" ] && command -v terraform >/dev/null 2>&1 &&
  [ -f "terraform/$CLOUD/terraform.tfstate" ]; then
  if terraform -chdir="terraform/$CLOUD" output -json helm_values >"$TF_VALUES" 2>/dev/null &&
    [ -s "$TF_VALUES" ]; then
    EXTRA_ARGS=(--values "$TF_VALUES")
    echo "    artifacts: $(terraform -chdir="terraform/$CLOUD" output -raw artifact_uri)"
  fi
fi

if [ ${#EXTRA_ARGS[@]} -eq 0 ]; then
  echo "    no terraform outputs — MLflow will use PVC-local artifacts"
fi

helm upgrade --install argus helm/platform \
  --namespace aiops \
  --values "helm/values/$CLOUD/platform.yaml" \
  "${EXTRA_ARGS[@]}" \
  --wait --timeout 10m

echo ""
echo ">>> Done. Useful commands:"
echo "  kubectl -n boutique get pods                     # demo app status"
echo "  kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80"
echo "      → http://localhost:3000  (admin / make grafana-password)"
echo "  make load / make load-varied                     # background traffic"
echo "  make chaos-cpu                                   # inject a CPU stress fault"
echo "  make forecasts / make incidents                  # Phase 3 outputs"
echo "  make frontend-public                             # expose the demo app (creates a cloud LB)"
