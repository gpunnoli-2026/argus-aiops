# Same five names as terraform/aws/outputs.tf — that shared contract is what
# lets scripts/deploy.sh and the Makefile stay cloud-agnostic.

output "cluster_name" {
  value = google_container_cluster.argus.name
}

output "location" {
  description = "Region or zone the cluster runs in"
  value       = google_container_cluster.argus.location
}

output "kubeconfig_command" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.argus.name} --zone ${var.zone} --project ${var.project_id}"
}

output "artifact_uri" {
  description = "MLflow artifact root"
  value       = "gs://${google_storage_bucket.artifacts.name}/mlartifacts"
}

output "helm_values" {
  description = "Per-deployment Helm values — valid values YAML as JSON, applied last by deploy.sh"
  value = {
    artifactUri = "gs://${google_storage_bucket.artifacts.name}/mlartifacts"
    # No mlflow block: Workload Identity needs no annotation, and the metadata
    # server supplies credentials and project. Contrast with the AWS output.
  }
}

output "network" {
  value = google_compute_network.vpc.name
}
