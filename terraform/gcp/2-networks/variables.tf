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

variable "region" {
  description = "Region of the shared subnet; matches the cluster"
  type        = string
  default     = "us-west1"
}

# Same ranges the single-project root used, so nothing downstream changes.
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

# Not a subnet range: the cluster sets it (stage 3). Declared here so the one
# place that owns the address plan also records it.
variable "master_ipv4_cidr_block" {
  description = "/28 reserved for the GKE control plane"
  type        = string
  default     = "172.16.0.0/28"
}
