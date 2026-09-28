# Step 2 of the apply sequence (docs/gcp-port-design.md §13): after the first
# apply creates the bucket, uncomment this and run `terraform init -migrate-state`.
# Until then state is local, because the bucket it would live in doesn't exist.
#
# terraform {
#   backend "gcs" {
#     bucket = "gk-argus-boot-tfstate"
#     prefix = "0-bootstrap"
#   }
# }
