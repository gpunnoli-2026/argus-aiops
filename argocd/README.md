# GitOps deployment (Argo CD)

`make deploy-gitops CLOUD=aws|gcp|kind` deploys the same things as `make deploy`,
but through Argo CD: it installs Argo CD and one root Application, and Argo CD
then keeps the cluster in line with this repository.

> Status: verified on GKE — all eight Applications synced and healthy from a
> clean cluster, MLflow on the Terraform-provided bucket, deleted resources
> recreated, chaos experiments left alone. Not yet run on EKS or kind.

Use one path per cluster. `make deploy` installs Helm releases; Argo CD applies
rendered manifests. Running both on the same cluster gives two owners for the
same objects.

## Layout

| Path | What it is |
|---|---|
| `argocd/values.yaml` | Values for Argo CD itself |
| `argocd/root/` | The root app: a chart that renders one Application per component |
| `deploy/boutique/` | Online Boutique v0.10.2, minus its public load balancer |
| `scripts/bootstrap-argocd.sh` | Everything that has to exist before Argo CD can take over |

## Applications

| Application | Namespace | Source |
|---|---|---|
| `monitoring` | monitoring | kube-prometheus-stack chart + `helm/values/monitoring.yaml` + `helm/values/<cloud>/monitoring.yaml` |
| `chaos-mesh` | chaos | chaos-mesh chart (controller, daemons, dashboard — not experiments) |
| `argus-rules` | monitoring | `observability/rules/` |
| `boutique` | boutique | `deploy/boutique/` |
| `argus-platform` | aiops, mlflow | `helm/platform` + `helm/values/<cloud>/platform.yaml` + Terraform outputs; images at the synced commit |

There are no sync waves. The bootstrap script installs the Prometheus Operator
CRDs first, so nothing has to wait for another Application.

## What stays outside Argo CD

- **Argo CD and the root Application** — installed by the bootstrap script.
- **Namespaces and the Grafana admin secret** — the secret is generated, so it
  cannot live in git.
- **The Prometheus Operator CRDs** — installed and waited for before the first
  sync. An operator that starts before the Prometheus CRD is served never
  starts its Prometheus controller. Argo CD still owns the CRDs afterwards.
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
- **Images follow the commit.** CI builds the service and trainer images for
  every pushed commit, tagged with its SHA (`.github/workflows/images.yaml`).
  The `argus-platform` Application sets `image.tag` to the commit it is
  syncing, so a merge rolls out the matching images and a `git revert` rolls
  them back. Every new commit on the tracked branch restarts the three
  services, whatever it changed.
- **Pods can start before their images exist.** Argo CD may sync a new commit
  a minute or two before CI has published its images. The pull is retried and
  the pods start once the images are there.
- **Chaos Mesh regenerates its certificates on every render.** The
  `chaos-mesh` Application ignores those fields, otherwise each sync would
  restart its pods.
- **Only what git states is enforced.** The upstream demo-app manifest sets no
  `replicas`, so scaling one of its Deployments by hand is not reverted (a
  deleted Deployment is). That also means a remediation that scales a service
  will not be fought by Argo CD.
- **`make down` stops Argo CD first**, so it does not recreate the volumes
  that teardown deletes.
