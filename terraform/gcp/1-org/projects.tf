locals {
  # Workload APIs are enabled here rather than in the app root, so sa-tf-apps
  # doesn't need serviceUsageAdmin.
  projects = {
    "shared-ops" = {
      folder = "shared"
      apis   = ["cloudkms", "logging", "monitoring", "secretmanager"]
    }
    # Shared VPC host and service project both need container for GKE.
    "nonprod-net-host" = {
      folder = "nonprod"
      apis   = ["compute", "container"]
    }
    "nonprod-gke-apps" = {
      folder = "nonprod"
      apis   = ["compute", "container", "iam", "storage"]
    }
  }

  project_apis = merge([
    for p, cfg in local.projects : {
      for api in cfg.apis : "${p}/${api}" => { project = p, service = "${api}.googleapis.com" }
    }
  ]...)
}

resource "google_project" "this" {
  for_each = local.projects

  project_id      = "${var.prefix}-${each.key}"
  name            = "${var.prefix}-${each.key}"
  folder_id       = google_folder.this[each.value.folder].name
  billing_account = var.billing_account

  # Belt and braces with compute.skipDefaultNetworkCreation, which can take a
  # few minutes to propagate after the first apply.
  auto_create_network = false

  # A stray destroy fails instead of deleting a project. Tearing one down
  # means switching this to DELETE first, on purpose.
  deletion_policy = "PREVENT"

  labels = {
    environment = each.value.folder
  }

  depends_on = [
    google_org_policy_policy.enforced,
    google_org_policy_policy.no_vm_external_ip,
    google_org_policy_policy.us_only,
  ]
}

resource "google_project_service" "this" {
  for_each = local.project_apis

  project            = google_project.this[each.value.project].project_id
  service            = each.value.service
  disable_on_destroy = false
}
