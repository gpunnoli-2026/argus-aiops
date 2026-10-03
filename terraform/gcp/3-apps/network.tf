# The VPC, subnet and router belong to 2-networks in the host project. This
# stage owns only the NAT, so it lives and dies with the cluster: an idle NAT
# still bills for its external IP. sa-tf-apps can touch the host's router only
# through the natOperator role from 1-org.
#
# Private nodes have no external IPs, but the platform still pulls from
# ghcr.io, docker.io and PyPI at pod startup — that egress goes through NAT.
#
# Only the GKE subnet: an ALL_SUBNETWORKS NAT forbids any other NAT in this
# network and region, and the VM-Cluster lab subnet has its own.
resource "google_compute_router_nat" "nat" {
  project                            = local.net.host_project
  name                               = "nonprod-nat-usw1"
  router                             = local.net.router
  region                             = local.net.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  # Primary range (nodes) plus the pods and services secondaries.
  subnetwork {
    name                    = local.net.subnetwork
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = false # cost: NAT logs are not worth paying for in a sandbox
    filter = "ERRORS_ONLY"
  }
}
