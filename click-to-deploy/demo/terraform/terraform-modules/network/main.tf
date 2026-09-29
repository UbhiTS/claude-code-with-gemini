variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = "claude-vertex-demo-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  project                  = var.project_id
  name                     = "claude-vertex-demo-subnet"
  ip_cidr_range            = "10.20.0.0/24"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = true
}

resource "google_compute_router" "router" {
  project = var.project_id
  name    = "claude-vertex-demo-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "claude-vertex-demo-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

resource "google_compute_firewall" "allow_iap_ssh" {
  project = var.project_id
  name    = "claude-vertex-allow-iap-ssh"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  # Google Cloud IAP TCP forwarding range
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["claude-vertex-demo-vm"]
}

output "network_self_link" {
  value = google_compute_network.vpc.self_link
}

output "subnetwork_self_link" {
  value = google_compute_subnetwork.subnet.self_link
}
