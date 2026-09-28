# boot/ was made by hand alongside the seed project; these are the rest.
# Folder names only need to be unique under their parent, so no prefix.
resource "google_folder" "this" {
  for_each = toset(["shared", "nonprod"])

  display_name = each.value
  parent       = "organizations/${var.org_id}"
}
