# One identity per later stage, so each stage's blast radius is its own roles.
locals {
  stages = {
    org  = "Stage 1-org: folders, projects, org policies, log sink"
    net  = "Stage 2-networks: Shared VPC host"
    apps = "Stage 3-apps: GKE and the artifacts bucket"
  }

  # Only sa-tf-org gets roles here: the org is the only scope that exists yet.
  # 1-org grants sa-tf-net and sa-tf-apps roles on the folder and projects it
  # creates, so nothing holds org-wide rights it doesn't need.
  org_roles = [
    "roles/logging.configWriter",
    "roles/orgpolicy.policyAdmin",
    "roles/resourcemanager.folderAdmin",
    "roles/resourcemanager.organizationViewer",
    "roles/resourcemanager.projectCreator",
  ]
}

resource "google_service_account" "stage" {
  for_each = local.stages

  account_id   = "sa-tf-${each.key}"
  display_name = "Terraform ${each.key}"
  description  = each.value

  depends_on = [google_project_service.required]
}

resource "google_organization_iam_member" "org_stage" {
  for_each = toset(local.org_roles)

  org_id = var.org_id
  role   = each.value
  member = google_service_account.stage["org"].member
}

# billing.user lets 1-org link the projects it creates to billing;
# costsManager lets it own the budget. Neither can change payment settings.
resource "google_billing_account_iam_member" "org_stage" {
  for_each = toset([
    "roles/billing.costsManager",
    "roles/billing.user",
  ])

  billing_account_id = var.billing_account
  role               = each.value
  member             = google_service_account.stage["org"].member
}

# The billing.user grant predates the for_each; keep it rather than recreate it.
moved {
  from = google_billing_account_iam_member.org_stage
  to   = google_billing_account_iam_member.org_stage["roles/billing.user"]
}

# Local runs impersonate the stage SA, so a laptop apply and a CI apply act as
# the same identity with the same permissions.
resource "google_service_account_iam_member" "admin_impersonation" {
  for_each = google_service_account.stage

  service_account_id = each.value.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.admin_principal
}

# Org-level grants live here, applied as gk@ (Organization Administrator):
# sa-tf-org can't edit org IAM, and shouldn't be able to, since that would let
# it grant itself anything. Read-only: the Log Router and every project's audit
# logs. 1-org adds viewAccessor on the org-audit bucket in shared-ops.
resource "google_organization_iam_member" "admin_log_viewer" {
  org_id = var.org_id
  role   = "roles/logging.viewer"
  member = var.admin_principal
}
