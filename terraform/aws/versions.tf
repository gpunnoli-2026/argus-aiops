terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # Local state is fine for a single-operator project.
  # Swap for an S3 backend if you ever work from multiple machines.
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "argus-aiops"
      ManagedBy = "terraform"
    }
  }
}
