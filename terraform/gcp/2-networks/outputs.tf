# Stage 3 reads these instead of creating its own VPC.

output "host_project" {
  value = local.host_project
}

output "region" {
  value = var.region
}

output "network" {
  value = google_compute_network.vpc.id
}

output "subnetwork" {
  value = google_compute_subnetwork.gke.id
}

output "pods_range_name" {
  value = "pods"
}

output "services_range_name" {
  value = "services"
}

output "router" {
  description = "Stage 3 attaches its Cloud NAT here"
  value       = google_compute_router.router.name
}

output "master_ipv4_cidr_block" {
  description = "Reserved for the GKE control plane; nothing else may overlap it"
  value       = var.master_ipv4_cidr_block
}
