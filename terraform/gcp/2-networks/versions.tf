terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  backend "gcs" {
    bucket                      = "gk-argus-boot-tfstate"
    prefix                      = "2-networks"
    impersonate_service_account = "sa-tf-net@gk-argus-boot-seed.iam.gserviceaccount.com"
  }
}

provider "google" {
  impersonate_service_account = local.net_sa
  region                      = var.region

  default_labels = {
    project    = "argus-aiops"
    stage      = "2-networks"
    managed-by = "terraform"
  }
}

locals {
  net_sa = "sa-tf-net@${var.seed_project_id}.iam.gserviceaccount.com"
}

data "terraform_remote_state" "bootstrap" {
  backend = "gcs"
  config = {
    bucket                      = "${var.prefix}-boot-tfstate"
    prefix                      = "0-bootstrap"
    impersonate_service_account = local.net_sa
  }
}

data "terraform_remote_state" "org" {
  backend = "gcs"
  config = {
    bucket                      = "${var.prefix}-boot-tfstate"
    prefix                      = "1-org"
    impersonate_service_account = local.net_sa
  }
}

locals {
  host_project = data.terraform_remote_state.org.outputs.projects["nonprod-net-host"].id
  apps_project = data.terraform_remote_state.org.outputs.projects["nonprod-gke-apps"]
  apps_sa      = data.terraform_remote_state.bootstrap.outputs.stage_service_accounts["apps"]
}
