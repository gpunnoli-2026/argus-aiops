#!/usr/bin/env bash
# GitOps deploy: installs Argo CD and one root Application; Argo CD then
# deploys everything scripts/deploy.sh does, from git. Idempotent.
#
# What stays here, outside Argo CD: Argo CD itself, the namespaces, the
# generated Grafana secret, and handing Terraform's outputs to the root app.
set -euo pipefail
cd "$(dirname "$0")/.."

# See scripts/deploy.sh: keep MSYS from rewriting path-like arguments.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

ARGOCD_CHART_VERSION="10.9.6"

CLOUD="${CLOUD:-}"
case "$CLOUD" in
  aws | gcp | kind) ;;
  "")
    echo "ERROR: CLOUD is required. Use: make deploy-gitops CLOUD=aws|gcp|kind" >&2
    exit 1
    ;;
  *)
    echo "ERROR: unknown CLOUD='$CLOUD' (expected aws, gcp or kind)" >&2
    exit 1
    ;;
esac

REPO_URL="${REPO_URL:-https://github.com/gpunnoli-2026/argus-aiops.git}"
REVISION="${REVISION:-$(git rev-parse --abbrev-ref HEAD)}"
echo ">>> Target: $CLOUD  (context: $(kubectl config current-context))"
echo ">>> Source: $REPO_URL @ $REVISION"

# Argo CD deploys what is pushed, not the working tree. Say so before a
# session is spent wondering why a local edit is not live.
git fetch --quiet origin "$REVISION" 2>/dev/null || true
if ! git rev-parse --quiet --verify "origin/$REVISION" >/dev/null; then
  echo "ERROR: origin/$REVISION does not exist — push the branch first." >&2
  exit 1
fi
if [ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$REVISION")" ] || [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "WARNING: local changes are not on origin/$REVISION. Argo CD will deploy origin/$REVISION."
fi

echo ">>> Namespaces..."
for ns in argocd monitoring boutique chaos loadgen aiops mlflow; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done

echo ">>> Grafana admin credentials (generated once, stored as a Secret)..."
if ! kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n monitoring create secret generic grafana-admin \
    --from-literal=admin-user=admin \
    --from-literal=admin-password="$(openssl rand -hex 16)"
fi

echo ">>> Argo CD..."
helm upgrade --install argocd argo-cd \
  --repo https://argoproj.github.io/argo-helm \
  --version "$ARGOCD_CHART_VERSION" \
  --namespace argocd \
  --values argocd/values.yaml \
  --wait --timeout 10m

# Per-deployment facts from Terraform, as in deploy.sh — passed to the root
# app as values instead of to helm as a file.
case "$CLOUD" in
  gcp) TF_DIR="${TF_DIR:-terraform/gcp/3-apps}" ;;
  *) TF_DIR="${TF_DIR:-terraform/$CLOUD}" ;;
esac
PLATFORM_VALUES="{}"
if [ "$CLOUD" != "kind" ] && command -v terraform >/dev/null 2>&1; then
  if TF_JSON="$(terraform -chdir="$TF_DIR" output -json helm_values 2>/dev/null)" && [ -n "$TF_JSON" ]; then
    PLATFORM_VALUES="$TF_JSON"
    echo "    artifacts: $(terraform -chdir="$TF_DIR" output -raw artifact_uri)"
  fi
fi
if [ "$PLATFORM_VALUES" = "{}" ]; then
  echo "    no terraform outputs — MLflow will use PVC-local artifacts"
fi

echo ">>> Root application..."
# JSON is valid YAML, so the Terraform output drops in as the values object.
kubectl apply -f - <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: argus
  namespace: argocd
spec:
  project: default
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  source:
    repoURL: $REPO_URL
    targetRevision: $REVISION
    path: argocd/root
    helm:
      valuesObject:
        cloud: $CLOUD
        repoURL: $REPO_URL
        revision: $REVISION
        platformValues: $PLATFORM_VALUES
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
EOF

echo ""
echo ">>> Done. Argo CD is now syncing; first sync takes several minutes."
echo "  kubectl get applications -n argocd               # sync and health per app"
echo "  make argocd                                      # UI at http://localhost:8081"
echo "  make argocd-password                             # initial admin password"
