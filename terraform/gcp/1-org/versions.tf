terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Remote from the first apply: 0-bootstrap already made the bucket. Backend
  # blocks can't read variables, hence the literal SA email.
  backend "gcs" {
    bucket                      = "gk-argus-boot-tfstate"
    prefix                      = "1-org"
    impersonate_service_account = "sa-tf-org@gk-argus-boot-seed.iam.gserviceaccount.com"
  }
}

# Everything runs as sa-tf-org, never as the human. Locally gk@'s ADC mints a
# short-lived token for it; in CI, WIF does.
provider "google" {
  impersonate_service_account = local.org_sa
  region                      = var.region

  default_labels = {
    project    = "argus-aiops"
    stage      = "1-org"
    managed-by = "terraform"
  }
}

locals {
  org_sa = "sa-tf-org@${var.seed_project_id}.iam.gserviceaccount.com"
}

# Stage SA emails come from bootstrap's state rather than being retyped here.
data "terraform_remote_state" "bootstrap" {
  backend = "gcs"
  config = {
    bucket                      = "${var.prefix}-boot-tfstate"
    prefix                      = "0-bootstrap"
    impersonate_service_account = local.org_sa
  }
}
