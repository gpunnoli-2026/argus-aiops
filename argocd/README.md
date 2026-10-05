# GitOps deployment (Argo CD)

`make deploy-gitops CLOUD=aws|gcp|kind` deploys the same things as `make deploy`,
but through Argo CD: it installs Argo CD and one root Application, and Argo CD
then keeps the cluster in line with this repository.

> Status: renders and validates offline (CI job `gitops`); not yet run on a cluster.

Use one path per cluster. `make deploy` installs Helm releases; Argo CD applies
rendered manifests. Running both on the same cluster gives two owners for the
same objects.

## Layout

| Path | What it is |
|---|---|
| `argocd/values.yaml` | Values for Argo CD itself |
| `argocd/root/` | The root app: a chart that renders one Application per component |
| `deploy/boutique/` | Online Boutique v0.10.2, minus its public load balancer |
| `services/kustomization.yaml` | Service code as ConfigMaps (until CI builds images) |
| `ml/training/kustomization.yaml` | Training code ConfigMap and the nightly retrain CronJob |
| `scripts/bootstrap-argocd.sh` | Everything that has to exist before Argo CD can take over |

## Applications

| Application | Namespace | Source |
|---|---|---|
| `monitoring` | monitoring | kube-prometheus-stack chart + `helm/values/monitoring.yaml` + `helm/values/<cloud>/monitoring.yaml` |
| `chaos-mesh` | chaos | chaos-mesh chart (controller, daemons, dashboard — not experiments) |
| `argus-rules` | monitoring | `observability/rules/` |
| `boutique` | boutique | `deploy/boutique/` |
| `argus-service-code` | aiops | `services/` |
| `argus-training` | aiops | `ml/training/` |
| `argus-platform` | aiops, mlflow | `helm/platform` + `helm/values/<cloud>/platform.yaml` + Terraform outputs |

There are no sync waves. Applications that need CRDs from `monitoring` fail
their first sync and succeed on retry.

## What stays outside Argo CD

- **Argo CD and the root Application** — installed by the bootstrap script.
- **Namespaces and the Grafana admin secret** — the secret is generated, so it
  cannot live in git.
- **Terraform outputs** (bucket URI, pod identity) — the bootstrap script reads
  them and sets them as values on the root Application.
- **One-off actions** — chaos experiments, load jobs, `make train`,
  `make frontend-public`. Argo CD only prunes what it created, so these are
  left alone.
- **Model promotion and rollback** — an MLflow alias, not a manifest.

## Things to know

- **Argo CD deploys what is pushed.** The bootstrap script uses the current
  branch (`REVISION=<branch>` overrides) and warns when local commits are not
  on the remote.
- **A code change does not restart a pod.** The code ConfigMaps keep fixed
  names, so after a change to a service: `kubectl rollout restart deploy/<name> -n aiops`.
- **Chaos Mesh regenerates its certificates on every render.** The
  `chaos-mesh` Application ignores those fields, otherwise each sync would
  restart its pods.
- **`make down` stops Argo CD first**, so it does not recreate the volumes
  that teardown deletes.
