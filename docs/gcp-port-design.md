# Argus on GCP — Port Design

**Status:** 📋 Proposed (for review) · **Scope:** Phase 6 "GKE port to prove the multi-cloud claim"

## 1. Goal and scope

Run the entire implemented Argus platform (Phases 0–3 + the implemented Phase 5 pieces) on
GKE with the **same Helm charts, services, rules, chaos library, and demo flow**, provisioned
by `make up CLOUD=gcp`. AWS keeps working unchanged; `CLOUD` is **required** on every
infrastructure target (no default — see D11).

**Success = parity, measured the same way as on EKS:**
CPU fault → anomaly score > 0.8 in < 2 min; the chaos-cpu run folds into one incident with the
correct root cause; forecasts are sane (no false "disk exhausted" alarms); nightly retrain promotes
through the gate; artifacts land in GCS; `make down` leaves nothing billing.

**In scope:** `terraform/gcp/`, a cloud-neutral contract between Terraform ↔ deploy ↔ Helm,
per-cloud values overlays, Makefile/deploy/CI changes, docs.
**Out of scope:** Phase 4 (Slack/orchestrator/executor), Azure, remote Terraform state,
container images/Artifact Registry (services still ride in ConfigMaps; noted as future work).

## 2. What actually changes

The core principle from `architecture.md` §1.4 — *Kubernetes-native core, cloud-specific edge* —
holds. Everything in-cluster is already portable **except four leaks**:

| Leak | Where | Fix |
|---|---|---|
| `gp2` StorageClass hardcoded | `helm/platform/values.yaml`, `helm/values/monitoring.yaml` | Per-cloud values overlay |
| S3-only MLflow wiring (`s3://`, `boto3`, `AWS_REGION`, IRSA annotation) | `helm/platform/templates/mlflow.yaml`, `values.yaml` | Generic `artifactUri` + `serviceAccountAnnotations` + `extraPipPackages` |
| `deploy.sh` reads `terraform/aws` outputs by name | `scripts/deploy.sh` | TF emits a `helm_values` output; deploy is cloud-blind |
| Disk forecast assumes `mountpoint="/"` is the writable disk | `services/capacity-forecaster/main.py` | `DISK_MOUNTPOINT` env var (see §6 R3) |

## 3. Service mapping

| Concern | AWS (today) | GCP (proposed) | Notes |
|---|---|---|---|
| Cluster | EKS 1.31, managed node group | **GKE Standard, zonal** (`us-west1-b`), REGULAR release channel | Autopilot rejected — see D1 |
| Nodes | 3× t3.medium **spot** (1–4) | 3× **e2-standard-2 Spot** (1–4), COS_CONTAINERD | e2-medium is shared-core; CPU-stress results would be skewed |
| Network | VPC /16, 2 AZ, private + public subnets | Custom VPC, 1 subnet + secondary ranges (pods/services), VPC-native | |
| Egress | 1 NAT GW | Cloud Router + **Cloud NAT** | Nodes still `pip install` + pull ghcr/docker.io at start |
| Control plane access | Public endpoint | Public endpoint, private nodes | Optional `master_authorized_networks` var |
| Object storage | S3, versioned, SSE, public-block | **GCS**, versioned, uniform bucket-level access, public-access-prevention *enforced* | Google-managed encryption by default |
| Pod identity | IRSA role (`mlflow:mlflow`) | **Workload Identity Federation** — direct principal binding on the bucket | No GSA, no KSA annotation, no keys — see D3 |
| Block storage | EBS CSI (`gp2`) | PD CSI (`standard-rwo`, pd-balanced) | Built into GKE |
| Load balancer | ELB (boutique `frontend-external`) | **ClusterIP by default**; `make frontend-public` flips to an L4 LB | k6 drives the in-cluster `frontend` Service, so no LB is needed to run the demo — see D12 |
| Kubeconfig | `aws eks update-kubeconfig` | `gcloud container clusters get-credentials` | Needs `gke-gcloud-auth-plugin` |
| Secrets (Phase 4) | ESO → Secrets Manager | ESO → **Secret Manager** via Workload Identity | Unchanged design, different store |
| LLM (Phase 4 wiring) | Anthropic API key | Anthropic API key **or Claude on Vertex AI** via Workload Identity | Optional; see D8 |

## 4. Target topology

```
GCP project <project_id>          (APIs: container, compute, iam, storage)
└── VPC argus-vpc (custom mode)
    ├── subnet argus-nodes   10.10.0.0/20   (Private Google Access on)
    │     ├── secondary "pods"      10.20.0.0/16
    │     └── secondary "services"  10.30.0.0/20
    ├── Cloud Router + Cloud NAT   (egress for private nodes)
    └── GKE Standard "argus" — zonal (us-west1-b), private nodes,
        public endpoint, Workload Identity, managed Prometheus OFF,
        Cloud Logging/Monitoring = SYSTEM_COMPONENTS only
        └── node pool "default": e2-standard-2 Spot ×3 (1–4), COS_CONTAINERD,
            50 GB pd-balanced, dedicated least-privilege node SA,
            GKE_METADATA, shielded (secure boot)
            ├── ns: boutique   ns: monitoring   ns: chaos
            ├── ns: aiops      ns: mlflow       ns: loadgen       (identical to EKS)
GCS: argus-artifacts-<project_id>     — MLflow artifacts (gs://…/mlartifacts)
IAM: roles/storage.objectUser on the bucket →
     principal://…/<project_id>.svc.id.goog/subject/ns/mlflow/sa/mlflow
```

## 5. Design decisions

**D1 — GKE Standard, not Autopilot.** Chaos Mesh's `chaos-daemon` needs privileged pods and a
hostPath to the containerd socket; Autopilot forbids both. Standard also gives node-exporter host
access and lets us choose Spot VMs explicitly. Containerd socket path on COS is
`/run/containerd/containerd.sock` — the existing `deploy.sh` flags work as-is.

**D2 — Zonal cluster in `us-west1` (Oregon).** `us-west1` is GCP's Oregon region — the
geographic twin of the current `us-west-2` — and sits in the same low-cost tier as `us-central1`,
so the region choice costs nothing against the original plan (`us-west2` LA / `us-west3` SLC /
`us-west4` LV all price higher). Zone `us-west1-b`; region and zone are single variables.

Zonal rather than regional: the GKE free tier credits the management fee for one zonal cluster
(the EKS control plane is ~$0.10/h). Single-zone is acceptable for a disposable sandbox, and it
keeps the Prometheus/MLflow zonal PDs reattachable after a Spot preemption.

**D3 — Workload Identity Federation with a direct principal binding.** Grant
`roles/storage.objectUser` on *the bucket only* to the `mlflow/mlflow` KSA principal. No Google
service account, no annotation, no JSON keys. Matches the IRSA posture (bucket-scoped, one
workload) with less machinery. GCS supports direct principal access; `google-cloud-storage`
(MLflow's GCS artifact backend) picks up credentials from the metadata server automatically.

**D4 — Only MLflow gets bucket access.** MLflow runs with `--serve-artifacts`, so training jobs and
the detector reach artifacts through the MLflow proxy, never the bucket. The AWS IRSA role also
trusts `aiops:retraining`, which is unnecessary privilege. The GCP binding omits it; a separate
commit removes it from AWS (verify with one train + rollback on kind/EKS first).

**D5 — Cloud-neutral contract: Terraform decides *what exists*, overlays describe *what the cloud
is*.**
- *Dynamic, per-deployment facts* (bucket name, identity wiring) → each TF root emits one output,
  `helm_values`, a JSON object that is valid Helm values. `deploy.sh` does
  `terraform output -json helm_values > tmp && helm … -f tmp`. No cloud branching in the script.
- *Static, per-cloud facts* (StorageClass, kube-proxy presence, pip packages, disk mountpoint)
  → `helm/values/<cloud>/{monitoring,platform}.yaml`, selected by `CLOUD`.
- All TF roots expose the same output names: `cluster_name`, `location`, `kubeconfig_command`,
  `artifact_uri`, `helm_values`.

**D6 — Raw `google_*` resources, not `terraform-google-modules`.** The AWS side uses community
modules because raw EKS means hand-writing IAM roles, an OIDC provider, launch templates, add-ons
and `aws-auth`. GCP has none of that: cluster + node pool are two resources and the whole root
module lands at ~150 readable lines. The module's input surface is far larger than what we
configure, it adds version churn, and it would hide the Workload Identity binding — the part most
worth showing. Symmetry with the AWS side is the only counter-argument and it doesn't pay for
itself here.

**D7 — Dataplane V2 on.** The chaos library's only network fault (`network-delay.yaml`) is `netem`
applied inside the pod's own netns, which is dataplane-independent — so demo risk is low. DPv2
gives NetworkPolicy (planned in §2.5) without Calico pods costing ~150 MB per node on a 3-node
sandbox, and it is the dataplane Google is actively developing. Consequences: no kube-proxy
(disable that scrape target, R2), and `datapath_provider` changes **force cluster replacement**
(~15 min rebuild — acceptable for a disposable sandbox, but not a mid-session flip). Residual risk
is Chaos Mesh's *partition* fault, which is iptables-based rather than netem and is the interaction
least likely to behave identically under eBPF; no partition experiment exists today (R4).

**D8 — LLM provider stays Anthropic API; Vertex is an option, not part of this port.** The LLM
layer is standalone/offline today. When Phase 4 wires it in-cluster on GCP, an
`LLM_PROVIDER=anthropic|vertex` switch using `AnthropicVertex` would remove the API key and bill
through the project via Workload Identity. Noted so Phase 4 designs the seam; not built here.

**D9 — GKE add-on telemetry trimmed.** New clusters enable Google Managed Prometheus and
workload logging by default. We run kube-prometheus-stack, so GMP would double-scrape and cost
money: set `managed_prometheus.enabled=false`, logging/monitoring to `SYSTEM_COMPONENTS`.

**D10 — Local state, same as AWS.** GCS backend documented as the multi-machine option.

**D11 — `CLOUD` is required, and teardown checks the live context.** `make up|plan|down|kubeconfig`
fail immediately if `CLOUD` is unset or not in `aws gcp`; `deploy` also accepts `kind`. A second
guard makes `down` compare the current kubectl context against `CLOUD` (`arn:aws:eks…` vs `gke_…`)
and refuse on mismatch. The first guard catches the empty default; the second catches the typo that
passes the first.

**D12 — Demo app is ClusterIP by default; the public LB is opt-in.** k6 drives
`frontend.boutique.svc.cluster.local`, so the upstream `frontend-external` LoadBalancer exists only
for human viewing. Leaving it on means a permanently internet-reachable, unauthenticated demo app,
~$0.025/h on each cloud, plus forwarding-rule/firewall (GCP) or ELB (AWS) resources that can orphan
and keep billing; it also drops mid-demo when chaos restarts the frontend pod. `deploy.sh` patches
it to ClusterIP; `make frontend-public` flips it back for recording or sharing, and `make down`
deletes it either way. Trade-off accepted: no shareable URL by default, and the cloud LB controller
— a genuine cross-cloud parity signal — is exercised only on demand.

## 6. Risks and GKE-specific gotchas

| # | Risk | Mitigation |
|---|---|---|
| R1 | `terraform destroy` fails: `google_container_cluster.deletion_protection` defaults to **true** | Set `false` explicitly (sandbox) |
| R2 | kube-prometheus-stack fires a permanent `KubeProxyDown` on Dataplane V2 → noise straight into the correlator | `kubeProxy.enabled: false` in `helm/values/gcp/monitoring.yaml` (etcd/scheduler/controller-manager already off) |
| R3 | **COS root `/` is a small read-only verity partition** — `node_disk` would read near-full and fire forecast alerts forever | `DISK_MOUNTPOINT` env on the forecaster: `/` on AWS, `/mnt/stateful_partition` on GKE. Verify the value against `node_filesystem_*` on a live node before committing |
| R4 | NetworkChaos on Dataplane V2 — netem delay is low-risk; an iptables-based partition fault (not in the library today) is the real unknown | Acceptance run does `make chaos-latency` and checks p95 in Grafana; validate any future partition experiment on DPv2 before relying on it. The D7 fallback is a cluster rebuild, not a flag flip |
| R5 | Orphaned PDs / forwarding rules bill after destroy | `make down` deletes LoadBalancer Services **and PVCs** before `terraform destroy`, then lists leftover disks/forwarding rules. Applies to EKS (orphaned EBS) too |
| R6 | Spot preemption mid-demo | Same exposure as EKS; 3 nodes + PDBs-less sandbox tolerates it. Pool autoscaling 1–4 |
| R7 | Pod CIDR exhaustion / IP planning | /16 pods range = ample for 4 nodes × 110 pods |
| R8 | GKE default node SA (Compute Engine default) has Editor | Dedicated node SA with only log/metric writer + `artifactregistry.reader` |
| R9 | `gke-gcloud-auth-plugin` missing on Windows → kubeconfig works but kubectl fails | `make up CLOUD=gcp` prechecks for it and prints the install command |

## 7. Cost (rough, us-west1 vs us-west-2; verify in each pricing calculator)

| Item | EKS session | GKE session |
|---|---|---|
| Control plane | ~$0.10/h | $0 (free-tier credit, one zonal cluster) |
| NAT | ~$0.045/h + data | Cloud NAT ~ $0.004/h (per-VM) + data |
| 3 spot nodes | ~$0.04/h | ~$0.06–0.09/h (e2-standard-2 Spot; prices float) |
| LB for boutique frontend | $0 (opt-in, D12) | $0 (opt-in, D12) |
| **Total** | **~$0.19/h** | **~$0.07–0.10/h** |

Larger nodes (8 GB vs 4 GB) cost slightly more per node but remove memory pressure on the
monitoring stack; net session cost still roughly halves because of the control plane.

## 8. Changes by file

**New — `terraform/gcp/`**
```
versions.tf    google ~> 6.x provider, required_version >= 1.7, default labels
variables.tf   project_id (required), region=us-west1, zone=us-west1-b,
               cluster_name=argus, machine_type=e2-standard-2, node_{desired,min,max}=3/1/4,
               datapath=ADVANCED_DATAPATH, master_authorized_cidrs=[]
apis.tf        google_project_service ×4 (disable_on_destroy=false)
network.tf     network, subnetwork (+secondary ranges), router, router_nat
gke.tf         cluster (remove_default_node_pool, private nodes, WI pool, GMP off,
               deletion_protection=false), node pool (spot), node service account + roles
storage.tf     bucket, bucket IAM (WI principal → objectUser)
outputs.tf     cluster_name, location, kubeconfig_command, artifact_uri, helm_values
example.tfvars
```

**Changed**
- `terraform/aws/outputs.tf` — add `location`, `artifact_uri`, `helm_values` (keep old outputs for
  one release, then drop). `storage.tf` — drop `aiops:retraining` from IRSA trust (D4, own commit).
- `helm/platform/values.yaml` — replace `artifactBucket`/`mlflowRoleArn`/`awsRegion` with
  `artifactUri: ""`, `mlflow.serviceAccountAnnotations: {}`, `mlflow.extraPipPackages: ""`,
  `mlflow.extraEnv: []`, `mlflow.storageClassName: ""` (empty = cluster default).
- `helm/platform/templates/mlflow.yaml` — render annotations map; `--artifacts-destination
  {{ .Values.artifactUri | default "/data/mlartifacts" }}`; pip install from value; env from value.
- `helm/platform/templates/capacity-forecaster.yaml` + `services/capacity-forecaster/main.py`
  — `DISK_MOUNTPOINT` env (default `/`).
- `helm/values/monitoring.yaml` — remove `storageClassName` (moves to overlays).
- New overlays: `helm/values/{aws,gcp,kind}/{monitoring,platform}.yaml`.
- `scripts/deploy.sh` — require `CLOUD` (`aws|gcp|kind`); apply the matching overlays; read
  `helm_values` from `terraform/$CLOUD` when state exists; patch `frontend-external` to ClusterIP (D12).
- `Makefile` — no `CLOUD` default + a `guard-cloud` prerequisite (D11); `TF_DIR := terraform/$(CLOUD)`;
  `down` verifies the kubectl context matches `CLOUD`, deletes LoadBalancer Services **and** PVCs,
  then destroys; `up` prechecks per-cloud CLIs; new `frontend-public` target.
- `.github/workflows/ci.yaml` — add `terraform init -backend=false && terraform validate`
  matrix over `aws`, `gcp` (fmt already recursive).
- `.gitignore` — add `*-sa-key.json`, `application_default_credentials.json` (belt-and-braces;
  design uses no keys).
- `.env.example` — `GCP_PROJECT`, `GCP_REGION`.
- Docs: `architecture.md` §2.1 (add GCP topology), §2.5 (Workload Identity), §2.6 (fill GCP
  column as ✅); README stack + quickstart (`make up CLOUD=gcp`); `plan.md` Phase 6 status.

## 9. Proposed commit sequence

Each commit leaves AWS and kind working.

1. `refactor(helm): cloud-neutral MLflow values and per-cloud overlays` — values/template/overlays;
   AWS overlay reproduces today's rendering exactly (verify with `helm template` diff).
2. `refactor(terraform): common output contract incl. helm_values` — AWS outputs + deploy.sh reads
   `helm_values`; `CLOUD` plumbing in Makefile.
3. `fix(security): scope bucket access to the MLflow SA only` — D4, AWS side.
4. `feat(forecaster): configurable disk mountpoint` — + unit test for the query builder.
5. `feat(terraform): GKE + GCS + Workload Identity root module` — `terraform/gcp/`.
6. `feat(gcp): GKE overlays (standard-rwo, kube-proxy off, GCS pip, stateful mountpoint)`.
7. `ci: terraform validate for aws and gcp roots`.
8. `chore(make): teardown removes PVCs; per-cloud prereq checks`.
9. `feat(demo): make the boutique frontend LB opt-in` — D12, both clouds.
10. `docs: GCP topology, portability table, quickstart` — after the acceptance run, with measured
   numbers from GKE.

## 10. Acceptance run (before commit 9)

```
make up                                       # must fail: CLOUD is required (D11)
make up CLOUD=gcp && make deploy CLOUD=gcp
kubectl get pods -A                          # all Running, no Pending on PVCs
make load-varied   (bake ≥ 2h)  → make train # model v1 registered; object visible in gs://…/mlartifacts
make chaos-cpu     → make scores / incidents  # score > 0.8 < 2 min; 1 incident, root = cartservice
make chaos-latency                            # R4: p95 rises in Grafana
make forecasts                                # node_disk not ~1.0 (R3)
kubectl -n aiops create job --from=cronjob/argus-retrain-anomaly t1   # gate passes/blocks cleanly
Alertmanager: no KubeProxyDown / other always-firing infra alerts (R2)
make frontend-public CLOUD=gcp                # D12: LB appears, app reachable; revert after
make down CLOUD=aws                           # must refuse: live context is GKE (D11)
make down CLOUD=gcp
gcloud compute disks list; gcloud compute forwarding-rules list    # both empty (R5)
```

## 11. Decisions taken at review (2026-09-20)

| # | Question | Decision |
|---|---|---|
| 1 | Region/zone | **`us-west1-b`** — Oregon-for-Oregon with `us-west-2`, same low-cost tier as `us-central1` (D2) |
| 2 | Dataplane V2 vs legacy + Calico | **Dataplane V2** (D7) |
| 3 | Terraform style | **Raw `google_*` resources** (D6) |
| 4 | `CLOUD` default | **Required, plus a kubectl-context check on `down`** (D11) |
| 5 | Boutique frontend LB | **ClusterIP by default, `make frontend-public` opt-in** (D12) |

Still to confirm during the build: the GKE value for `DISK_MOUNTPOINT` (R3), checked against
`node_filesystem_*` on a live COS node.
