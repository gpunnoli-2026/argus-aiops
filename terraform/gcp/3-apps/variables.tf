# The project comes from 1-org and the region, network and CIDRs from
# 2-networks, so nothing here is required and `make up` needs no tfvars.

variable "seed_project_id" {
  description = "Home project of the stage SAs (0-bootstrap)"
  type        = string
  default     = "gk-argus-boot-seed"
}

variable "prefix" {
  description = "Naming prefix shared by every landing-zone resource (docs/gcp-port-design.md §12)"
  type        = string
  default     = "gk-argus"
}

variable "zone" {
  description = "Zone for the zonal cluster (one zonal cluster is covered by the GKE free tier). Must be in the shared subnet's region"
  type        = string
  default     = "us-west1-b"
}

variable "cluster_name" {
  description = "GKE cluster name"
  type        = string
  default     = "argus"
}

variable "machine_type" {
  description = "Node machine type. e2-standard-2 over e2-medium: shared-core CPU would skew CPU-stress chaos results"
  type        = string
  default     = "e2-standard-2"
}

variable "use_spot" {
  description = "Spot nodes (~60-90% cheaper, preemptible any time). Set false if the billing account has no Spot quota — free trials often don't"
  type        = bool
  default     = true
}

variable "node_desired_size" {
  description = "Initial node count (3 fits monitoring + boutique + chaos + ML services)"
  type        = number
  default     = 3
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable "node_max_size" {
  type    = number
  default = 4
}

variable "node_disk_size_gb" {
  description = "Boot disk per node (GKE default is 100 GB; 50 is plenty for a sandbox)"
  type        = number
  default     = 50
}

variable "datapath_provider" {
  description = <<-EOT
    ADVANCED_DATAPATH (Dataplane V2, eBPF) gives NetworkPolicy without Calico pods.
    Changing this REPLACES the cluster. LEGACY_DATAPATH is the fallback if a future
    iptables-based Chaos Mesh experiment misbehaves under eBPF.
  EOT
  type        = string
  default     = "ADVANCED_DATAPATH"

  validation {
    condition     = contains(["ADVANCED_DATAPATH", "LEGACY_DATAPATH"], var.datapath_provider)
    error_message = "datapath_provider must be ADVANCED_DATAPATH or LEGACY_DATAPATH."
  }
}

variable "master_authorized_cidrs" {
  description = "Optional allowlist for the public control-plane endpoint. Empty = open (same posture as the EKS side)"
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}
