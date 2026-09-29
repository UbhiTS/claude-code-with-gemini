variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "zone" {
  type = string
}

variable "network_self_link" {
  type = string
}

variable "subnetwork_self_link" {
  type = string
}

variable "service_account_email" {
  type = string
}

resource "google_compute_instance" "demo_vm" {
  project      = var.project_id
  name         = "claude-code-vertex-demo"
  machine_type = "e2-standard-4"
  zone         = var.zone
  tags         = ["claude-vertex-demo-vm"]

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = 50
      type  = "pd-ssd"
    }
  }

  network_interface {
    network    = var.network_self_link
    subnetwork = var.subnetwork_self_link
    # Outbound access is handled via Cloud NAT; SSH is handled via IAP TCP forwarding
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  service_account {
    email  = var.service_account_email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  metadata = {
    enable-oslogin = "FALSE"
  }

  metadata_startup_script = file("${path.module}/../../scripts/vm_startup.sh")
}

output "vm_name" {
  value = google_compute_instance.demo_vm.name
}
