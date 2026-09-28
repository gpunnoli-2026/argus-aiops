# Remote state for every stage, one prefix each (0-bootstrap, 1-org, ...).
# Google-managed encryption for now: a CMEK key would live in shared-ops, which
# stage 1 creates.
resource "google_storage_bucket" "tfstate" {
  name     = "${var.prefix}-boot-tfstate"
  location = upper(var.region)

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  # Every apply writes a new version; keep enough to roll back a bad one.
  lifecycle_rule {
    condition {
      num_newer_versions = 10
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  # Unlike the artifacts bucket, losing this orphans every stage's resources.
  force_destroy = false
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required]
}

# objectUser: read/write/lock state, no bucket admin. Bucket-wide rather than
# per-prefix: IAM conditions on object names don't cover the list call the
# backend makes, so prefix scoping would cost more than it's worth here.
resource "google_storage_bucket_iam_member" "stage_state" {
  for_each = google_service_account.stage

  bucket = google_storage_bucket.tfstate.name
  role   = "roles/storage.objectUser"
  member = each.value.member
}
