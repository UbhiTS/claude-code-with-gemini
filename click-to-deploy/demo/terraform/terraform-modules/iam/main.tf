variable "project_id" {
  type = string
}

resource "google_service_account" "demo_sa" {
  project      = var.project_id
  account_id   = "claude-code-vertex-sa"
  display_name = "Claude Code + Vertex AI Hybrid Demo Service Account"
}

resource "google_project_iam_member" "vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.demo_sa.email}"
}

resource "google_project_iam_member" "log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.demo_sa.email}"
}

output "service_account_email" {
  value = google_service_account.demo_sa.email
}
