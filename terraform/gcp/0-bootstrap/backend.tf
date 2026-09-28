# This stage stores its state in the bucket it creates. The very first apply ran
# with local state (the bucket didn't exist yet) and was then moved here with
# `terraform init -migrate-state` (docs/gcp-port-design.md §13).
terraform {
  backend "gcs" {
    bucket = "gk-argus-boot-tfstate"
    prefix = "0-bootstrap"
  }
}
