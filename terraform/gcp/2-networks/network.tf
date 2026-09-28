# Names are per project, so no prefix.
resource "google_compute_network" "vpc" {
  project                 = local.host_project
  name                    = "nonprod-vpc"
  auto_create_subnetworks = false
}

# VPC-native cluster: pods and services get their own secondary ranges rather
# than an overlay, so pod IPs are routable inside the VPC. The range names are
# what gke.tf's ip_allocation_policy refers to.
resource "google_compute_subnetwork" "gke" {
  project       = local.host_project
  name          = "gke-usw1"
  network       = google_compute_network.vpc.id
  region        = var.region
  ip_cidr_range = var.subnet_cidr

  # Lets private nodes reach Google APIs (GCS, Artifact Registry) without NAT
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }
}

# The router is free and holds no IP, so it lives with the network. The NAT on
# it is created and destroyed with the cluster in stage 3: an idle NAT still
# bills for its external IP.
resource "google_compute_router" "router" {
  project = local.host_project
  name    = "nonprod-router-usw1"
  network = google_compute_network.vpc.id
  region  = var.region
}
