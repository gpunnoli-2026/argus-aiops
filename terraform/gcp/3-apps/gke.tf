# Nodes run as a dedicated least-privilege service account. The default Compute
# Engine SA carries project Editor — far too much for a node pool.
resource "google_service_account" "nodes" {
  account_id   = "${var.cluster_name}-nodes"
  display_name = "Argus GKE nodes"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/artifactregistry.reader",
  ])

  project = local.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "argus" {
  name     = var.cluster_name
  location = var.zone # zonal: the GKE free tier covers one zonal cluster's management fee

  # Terraform refuses to destroy the cluster while this is true (provider
  # default). This environment is meant to be torn down after every session.
  deletion_protection = false

  # The cluster must be created with a node pool, which we then discard in
  # favour of the managed pool below.
  remove_default_node_pool = true
  initial_node_count       = 1

  # Shared VPC: the network lives in the host project (2-networks), which has
  # already granted GKE's service agents networkUser on this subnet.
  network    = local.net.network
  subnetwork = local.net.subnetwork

  networking_mode   = "VPC_NATIVE"
  datapath_provider = var.datapath_provider

  ip_allocation_policy {
    cluster_secondary_range_name  = local.net.pods_range_name
    services_secondary_range_name = local.net.services_range_name
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false # kubectl from a laptop, same as the EKS side
    master_ipv4_cidr_block  = local.net.master_ipv4_cidr_block
  }

  dynamic "master_authorized_networks_config" {
    for_each = length(var.master_authorized_cidrs) > 0 ? [1] : []
    content {
      dynamic "cidr_blocks" {
        for_each = var.master_authorized_cidrs
        content {
          cidr_block   = cidr_blocks.value.cidr_block
          display_name = cidr_blocks.value.display_name
        }
      }
    }
  }

  release_channel {
    channel = "REGULAR"
  }

  # Pod identity without service-account keys: a KSA is granted GCP roles
  # directly (see storage.tf).
  workload_identity_config {
    workload_pool = "${local.project_id}.svc.id.goog"
  }

  # Argus brings its own Prometheus. Google Managed Prometheus is on by default
  # on new clusters and would scrape everything a second time, for money.
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]

    managed_prometheus {
      enabled = false
    }
  }

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS"] # not WORKLOADS: pod logs would bill per GB
  }

  addons_config {
    http_load_balancing {
      disabled = true # no Ingress in this platform; the demo app uses an L4 Service
    }
  }

  lifecycle {
    precondition {
      condition     = startswith(var.zone, "${local.net.region}-")
      error_message = "zone ${var.zone} is outside the shared subnet's region (${local.net.region})."
    }
  }
}

resource "google_container_node_pool" "default" {
  name     = "default"
  cluster  = google_container_cluster.argus.id
  location = var.zone

  initial_node_count = var.node_desired_size

  autoscaling {
    min_node_count = var.node_min_size
    max_node_count = var.node_max_size
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.machine_type
    spot         = var.use_spot # cost: ~60-90% off; fine for a rebuildable sandbox
    image_type   = "COS_CONTAINERD"

    disk_size_gb = var.node_disk_size_gb
    disk_type    = "pd-balanced"

    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = {
      workload = "general"
    }

    # Required for Workload Identity: pods get KSA tokens, not the node's identity
    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }

  lifecycle {
    # the autoscaler owns the live node count
    ignore_changes = [initial_node_count]
  }

  # Nodes pull images through NAT, so destroy removes them before their egress.
  depends_on = [google_compute_router_nat.nat]
}
