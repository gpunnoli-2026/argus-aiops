# Keyless GitHub Actions: the workflow's OIDC token is exchanged for a
# short-lived token of a stage SA. No JSON key exists to leak.
#
# Pool IDs are soft-deleted for 30 days after a destroy and can't be reused in
# that window; a destroy-and-reapply needs a new pool ID.
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository"    = "assertion.repository"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.ref"           = "assertion.ref"
  }

  # Every GitHub repo shares this issuer, so without a condition any workflow
  # on github.com could present a token. Pinned by numeric ID, not name.
  attribute_condition = "assertion.repository_id == '${var.github_repository_id}' && assertion.repository_owner_id == '${var.github_owner_id}'"
}

# Any workflow in this repo may act as any stage SA for now. The CI apply
# pipeline will tighten the apply path to attribute.ref == refs/heads/main.
resource "google_service_account_iam_member" "github" {
  for_each = google_service_account.stage

  service_account_id = each.value.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}
