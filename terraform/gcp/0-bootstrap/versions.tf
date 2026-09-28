terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

# Applied as gk@ with user ADC, the only stage that is. User credentials carry
# no project of their own, so org- and billing-level calls need an explicit
# quota project or they fail with a misleading 403.
provider "google" {
  project               = var.seed_project_id
  region                = var.region
  billing_project       = var.seed_project_id
  user_project_override = true

  default_labels = {
    project    = "argus-aiops"
    stage      = "0-bootstrap"
    managed-by = "terraform"
  }
}
