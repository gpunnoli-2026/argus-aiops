terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # The only stage `make up|down CLOUD=gcp` touches; 0–2 persist between
  # sessions. Remote from the first apply, unlike the AWS root.
  backend "gcs" {
    bucket                      = "gk-argus-boot-tfstate"
    prefix                      = "3-apps"
    impersonate_service_account = "sa-tf-apps@gk-argus-boot-seed.iam.gserviceaccount.com"
  }
}

# Runs as sa-tf-apps: gk@'s ADC mints a short-lived token for it, so no
# project_id or credentials need passing to `make up`.
provider "google" {
  impersonate_service_account = local.apps_sa
  project                     = local.project_id
  region                      = local.net.region
  zone                        = var.zone

  default_labels = {
    project    = "argus-aiops"
    stage      = "3-apps"
    managed-by = "terraform"
  }
}

locals {
  apps_sa = "sa-tf-apps@${var.seed_project_id}.iam.gserviceaccount.com"
}

data "terraform_remote_state" "org" {
  backend = "gcs"
  config = {
    bucket                      = "${var.prefix}-boot-tfstate"
    prefix                      = "1-org"
    impersonate_service_account = local.apps_sa
  }
}

data "terraform_remote_state" "net" {
  backend = "gcs"
  config = {
    bucket                      = "${var.prefix}-boot-tfstate"
    prefix                      = "2-networks"
    impersonate_service_account = local.apps_sa
  }
}

locals {
  project_id = data.terraform_remote_state.org.outputs.projects["nonprod-gke-apps"].id
  net        = data.terraform_remote_state.net.outputs
}

data "google_project" "this" {}
