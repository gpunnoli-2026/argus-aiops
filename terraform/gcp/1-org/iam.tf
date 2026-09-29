# Hand the later stages exactly what they need, scoped to what this stage just
# created. sa-tf-org itself needs nothing: creating a project makes it Owner.

locals {
  stage_sa = data.terraform_remote_state.bootstrap.outputs.stage_service_accounts

  project_grants = {
    "nonprod-net-host" = {
      sa = "net"
      roles = [
        "roles/compute.networkAdmin",
        "roles/compute.securityAdmin",
        # GKE's service agent needs hostServiceAgentUser on the host project.
        "roles/resourcemanager.projectIamAdmin",
      ]
    }
    "nonprod-gke-apps" = {
      sa = "apps"
      roles = [
        "roles/container.admin",
        "roles/storage.admin",
        "roles/iam.serviceAccountAdmin",
        # The node pool runs as the node SA, which needs actAs.
        "roles/iam.serviceAccountUser",
        # Node SA roles and the MLflow bucket binding.
        "roles/resourcemanager.projectIamAdmin",
        # Read-only. The provider reads back the node pool's instance group
        # after GKE creates it (found on the first make up).
        "roles/compute.viewer",
      ]
    }
  }

  project_grant_pairs = merge([
    for p, g in local.project_grants : {
      for role in g.roles : "${p}/${role}" => { project = p, sa = g.sa, role = role }
    }
  ]...)
}

resource "google_project_iam_member" "stage" {
  for_each = local.project_grant_pairs

  project = google_project.this[each.value.project].project_id
  role    = each.value.role
  member  = "serviceAccount:${local.stage_sa[each.value.sa]}"
}

# Shared VPC admin must be granted at folder or org level. It covers enabling
# the host, attaching gke-apps and granting networkUser on the shared subnet.
resource "google_folder_iam_member" "net_xpn_admin" {
  folder = google_folder.this["nonprod"].name
  role   = "roles/compute.xpnAdmin"
  member = "serviceAccount:${local.stage_sa["net"]}"
}

# Stage 3 attaches Cloud NAT to the host's router and removes it again with the
# cluster, so an idle NAT doesn't bill for its IP. That needs router updates in
# net-host and nothing else; compute.networkAdmin would also let it rewrite the
# VPC. sa-tf-org can define a project-level role here because it owns net-host.
resource "google_project_iam_custom_role" "nat_operator" {
  project     = google_project.this["nonprod-net-host"].project_id
  role_id     = "natOperator"
  title       = "Cloud NAT operator"
  description = "Add and remove Cloud NAT on existing routers; no other network changes"
  # networks.updatePolicy: creating a NAT also updates the VPC's policy
  # (found on the first make up). It can't create firewall rules on its own;
  # that needs compute.firewalls.create, which this role doesn't hold.
  permissions = [
    "compute.networks.updatePolicy",
    "compute.regionOperations.get",
    "compute.routers.get",
    "compute.routers.list",
    "compute.routers.update",
  ]
}

resource "google_project_iam_member" "apps_nat_operator" {
  project = google_project.this["nonprod-net-host"].project_id
  role    = google_project_iam_custom_role.nat_operator.id
  member  = "serviceAccount:${local.stage_sa["apps"]}"
}

# Scoped to nonprod/: cluster credentials and full Kubernetes RBAC, plus read
# access for disks, forwarding rules and logs. Nothing on boot/ or shared/.
locals {
  operator_grants = merge([
    for m in var.operators : {
      for role in ["roles/container.admin", "roles/viewer"] : "${m}/${role}" => { member = m, role = role }
    }
  ]...)
}

resource "google_folder_iam_member" "operators" {
  for_each = local.operator_grants

  folder = google_folder.this["nonprod"].name
  role   = each.value.role
  member = each.value.member
}

# Read the central audit trail: the Log Router and every project's audit logs
# (logging.viewer, org-wide) and the org-audit bucket's views in shared-ops,
# which Observability Analytics queries (viewAccessor; logging.viewer alone
# covers only the built-in _Default and _Required buckets). Read-only.
resource "google_organization_iam_member" "operators_log_viewer" {
  for_each = toset(var.operators)

  org_id = var.org_id
  role   = "roles/logging.viewer"
  member = each.value
}

resource "google_project_iam_member" "operators_audit_access" {
  for_each = toset(var.operators)

  project = google_project.this["shared-ops"].project_id
  role    = "roles/logging.viewAccessor"
  member  = each.value
}
