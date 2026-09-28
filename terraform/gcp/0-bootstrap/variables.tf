variable "org_id" {
  description = "Numeric ID of the gklabs.fyi organization (gcloud organizations list)"
  type        = string
}

variable "billing_account" {
  description = "Billing account the stage-1 SA may link new projects to (XXXXXX-XXXXXX-XXXXXX)"
  type        = string
}

# Created by hand and kept out of Terraform: importing it would let a stray
# destroy take out the state bucket's home.
variable "seed_project_id" {
  description = "The hand-made seed project in the boot/ folder"
  type        = string
  default     = "gk-argus-boot-seed"
}

variable "region" {
  description = "Location of the state bucket; us-west1 keeps it next to the cluster"
  type        = string
  default     = "us-west1"
}

variable "prefix" {
  description = "Naming prefix shared by every landing-zone resource (docs/gcp-port-design.md §12)"
  type        = string
  default     = "gk-argus"
}

variable "admin_principal" {
  description = "Human allowed to impersonate the stage SAs for local runs, e.g. user:gk@gklabs.fyi"
  type        = string
}

# Numeric IDs, not names: a renamed or deleted repo frees its name for someone
# else to claim, and a name-based WIF condition would then trust their workflows.
variable "github_repository_id" {
  description = "Numeric ID of gpunnoli-2026/argus-aiops (GitHub API: .id)"
  type        = string
}

variable "github_owner_id" {
  description = "Numeric ID of the repository owner (GitHub API: .owner.id)"
  type        = string
}
