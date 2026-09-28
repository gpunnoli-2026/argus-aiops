# Later stages read these; GitHub Actions stores them as repository variables.
# None are secrets.

output "state_bucket" {
  value = google_storage_bucket.tfstate.name
}

output "stage_service_accounts" {
  description = "Stage name -> SA email, for impersonate_service_account and the CI auth step"
  value       = { for k, sa in google_service_account.stage : k => sa.email }
}

output "workload_identity_provider" {
  description = "Full provider resource name for google-github-actions/auth"
  value       = google_iam_workload_identity_pool_provider.github.name
}
