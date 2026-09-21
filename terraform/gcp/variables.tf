variable "project_id" {
  description = "GCP project ID (no default — every resource here bills to it)"
  type        = string
}

variable "region" {
  description = "GCP region. us-west1 is Oregon, matching the AWS side, and is in the low-cost tier"
  type        = string
  default     = "us-west1"
}

variable "zone" {
  description = "Zone for the zonal cluster (one zonal cluster is covered by the GKE free tier)"
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

variable "subnet_cidr" {
  description = "Primary node range"
  type        = string
  default     = "10.10.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range for pods"
  type        = string
  default     = "10.20.0.0/16"
}

variable "services_cidr" {
  description = "Secondary range for services"
  type        = string
  default     = "10.30.0.0/20"
}

variable "master_ipv4_cidr_block" {
  description = "/28 for the managed control plane (private nodes require it)"
  type        = string
  default     = "172.16.0.0/28"
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
