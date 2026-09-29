variable "project_id" {
  type = string
}

resource "google_project_service" "required_apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "compute.googleapis.com",
    "secretmanager.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com"
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "random_password" "rdp_password" {
  length  = 16
  special = false
}

resource "google_secret_manager_secret" "rdp_secret" {
  project   = var.project_id
  secret_id = "claude-vertex-rdp-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required_apis]
}

resource "google_secret_manager_secret_version" "rdp_secret_version" {
  secret      = google_secret_manager_secret.rdp_secret.id
  secret_data = random_password.rdp_password.result
}

output "rdp_password" {
  value     = random_password.rdp_password.result
  sensitive = true
}

output "rdp_secret_id" {
  value = google_secret_manager_secret.rdp_secret.secret_id
}
