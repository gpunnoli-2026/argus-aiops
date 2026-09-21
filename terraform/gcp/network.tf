resource "google_compute_network" "vpc" {
  name                    = "${var.cluster_name}-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.required]
}

# VPC-native cluster: pods and services get their own secondary ranges rather
# than an overlay, so pod IPs are routable inside the VPC.
resource "google_compute_subnetwork" "nodes" {
  name          = "${var.cluster_name}-nodes"
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

# Private nodes have no external IPs, but the platform still pulls from
# ghcr.io, docker.io and PyPI at pod startup — that egress goes through NAT.
resource "google_compute_router" "router" {
  name    = "${var.cluster_name}-router"
  network = google_compute_network.vpc.id
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  name                               = "${var.cluster_name}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = false # cost: NAT logs are not worth paying for in a sandbox
    filter = "ERRORS_ONLY"
  }
}
