# net-host owns the network; gke-apps borrows it. Both need compute.xpnAdmin,
# which sa-tf-net holds on the nonprod/ folder.
resource "google_compute_shared_vpc_host_project" "host" {
  project = local.host_project
}

resource "google_compute_shared_vpc_service_project" "gke_apps" {
  host_project    = google_compute_shared_vpc_host_project.host.project
  service_project = local.apps_project.id
}
