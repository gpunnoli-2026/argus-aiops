terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Local state, same as the AWS root. Swap for a GCS backend if you ever work
  # from more than one machine.
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone

  default_labels = {
    project    = "argus-aiops"
    managed-by = "terraform"
  }
}

data "google_project" "this" {}
