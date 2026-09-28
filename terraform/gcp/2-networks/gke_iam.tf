# What a GKE cluster in a Shared VPC service project needs from the host.
# Both service agents appear once the container API is enabled on gke-apps
# (stage 1). If a grant fails with "does not exist", re-apply after a minute.

locals {
  gke_agent  = "serviceAccount:service-${local.apps_project.number}@container-engine-robot.iam.gserviceaccount.com"
  apis_agent = "serviceAccount:${local.apps_project.number}@cloudservices.gserviceaccount.com"

  subnet_users = {
    gke_agent  = local.gke_agent                   # nodes and pods in the shared subnet
    apis_agent = local.apis_agent                  # managed instance groups for node pools
    apps_sa    = "serviceAccount:${local.apps_sa}" # stage 3 creates the cluster against it
  }
}

resource "google_compute_subnetwork_iam_member" "network_user" {
  for_each = local.subnet_users

  project    = local.host_project
  region     = var.region
  subnetwork = google_compute_subnetwork.gke.name
  role       = "roles/compute.networkUser"
  member     = each.value
}

resource "google_project_iam_member" "gke_host_agent" {
  project = local.host_project
  role    = "roles/container.hostServiceAgentUser"
  member  = local.gke_agent
}

# Lets GKE create and maintain its own firewall rules in the host (control
# plane to node webhooks, health checks, intra-cluster). Without it those rules
# must be hand-kept, and a missing one fails silently. A firewall-only custom
# role would be tighter; it needs iam.roleAdmin, which no stage SA holds.
resource "google_project_iam_member" "gke_firewall" {
  project = local.host_project
  role    = "roles/compute.securityAdmin"
  member  = local.gke_agent
}
