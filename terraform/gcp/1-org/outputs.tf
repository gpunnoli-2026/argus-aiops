output "folders" {
  description = "Folder name -> folders/<id>"
  value       = { for k, f in google_folder.this : k => f.name }
}

# 2-networks needs the gke-apps number: GKE's service agents are named after it
# and need networkUser on the shared subnet.
output "projects" {
  description = "Short name -> project ID and number"
  value = {
    for k, p in google_project.this : k => {
      id     = p.project_id
      number = p.number
    }
  }
}

output "org_audit_bucket" {
  value = google_logging_project_bucket_config.org_audit.id
}
