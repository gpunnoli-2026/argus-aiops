# Every project's audit trail lands in one place that project owners can't
# edit. Admin Activity, System Event and Policy Denied only: Data Access logs
# are the expensive ones and stay off.

resource "google_logging_project_bucket_config" "org_audit" {
  project        = google_project.this["shared-ops"].project_id
  location       = var.region
  bucket_id      = "org-audit"
  retention_days = 30 # Cloud Logging's free retention

  # Log Analytics: SQL over the bucket, no extra charge.
  #
  # With analytics on, creation runs asynchronously for minutes, and the
  # provider's follow-up update fails with "Buckets must be in an ACTIVE state"
  # and taints the bucket. Never re-apply over that taint: replacing deletes the
  # bucket, which then blocks its name for 7 days. Wait for ACTIVE, then
  # `terraform untaint` (or `gcloud logging buckets undelete` if already gone).
  enable_analytics = true

  # Not locked: a locked bucket can't be shortened or deleted until retention
  # runs out. Right for PHI, wrong for a sandbox.

  depends_on = [google_project_service.this["shared-ops/logging"]]
}

resource "google_logging_organization_sink" "org_audit" {
  name             = "org-audit-sink"
  org_id           = var.org_id
  destination      = "logging.googleapis.com/${google_logging_project_bucket_config.org_audit.id}"
  include_children = true
  filter           = "logName:\"cloudaudit.googleapis.com\""
}

resource "google_project_iam_member" "org_audit_writer" {
  project = google_project.this["shared-ops"].project_id
  role    = "roles/logging.bucketWriter"
  member  = google_logging_organization_sink.org_audit.writer_identity
}
