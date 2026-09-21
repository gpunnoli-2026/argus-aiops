# Terraform fails with a cryptic 403 if these are off. disable_on_destroy is
# false deliberately: `make down` should delete this project's resources, not
# turn off APIs other things in the project may rely on.
resource "google_project_service" "required" {
  for_each = toset([
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "storage.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}
