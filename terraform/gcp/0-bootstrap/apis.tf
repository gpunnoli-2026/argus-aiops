# Without cloudresourcemanager and serviceusage the provider can't read the org
# or enable the rest. disable_on_destroy is false for the same reason as the app
# root: later stages depend on these too.
#
# The last three are here for stage 1, not for this one: org-, folder- and
# billing-level calls made as a stage SA bill quota to the SA's home project,
# which is this one.
resource "google_project_service" "required" {
  for_each = toset([
    "cloudbilling.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
    "billingbudgets.googleapis.com",
    "logging.googleapis.com",
    "orgpolicy.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}
