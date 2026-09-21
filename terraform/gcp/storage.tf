# MLflow artifacts + training datasets. Bucket names are globally unique, hence
# the project suffix.
resource "google_storage_bucket" "artifacts" {
  name     = "${var.cluster_name}-artifacts-${var.project_id}"
  location = upper(var.region)

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  # Portfolio project: let `terraform destroy` remove a non-empty bucket
  force_destroy = true

  depends_on = [google_project_service.required]
}

# Workload Identity, direct principal binding: the mlflow/mlflow Kubernetes
# service account IS the IAM principal. No Google service account to maintain,
# no KSA annotation, no keys anywhere.
#
# Only MLflow appears here. The server runs with --serve-artifacts, so training
# jobs and the detector reach artifacts through its proxy and never touch the
# bucket themselves.
resource "google_storage_bucket_iam_member" "mlflow" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectUser" # objects: get/list/create/delete — no bucket admin
  member = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject/ns/mlflow/sa/mlflow"
}
