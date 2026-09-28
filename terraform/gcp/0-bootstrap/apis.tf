# Without cloudresourcemanager and serviceusage the provider can't read the org
# or enable the rest. disable_on_destroy is false for the same reason as the app
# root: later stages depend on these too.
resource "google_project_service" "required" {
  for_each = toset([
    "cloudbilling.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}
