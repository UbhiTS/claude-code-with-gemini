output "vm_name" {
  description = "Name of the provisioned Claude Code + Vertex AI demo VM"
  value       = module.compute.vm_name
}

output "vm_zone" {
  description = "Zone of the provisioned demo VM"
  value       = var.zone
}

output "rdp_external_ip" {
  description = "Public External IP address for Remote Desktop (RDP) connection"
  value       = module.compute.vm_external_ip
}

output "rdp_port" {
  description = "RDP TCP Port"
  value       = 3389
}

output "rdp_username" {
  description = "RDP login username"
  value       = "demo"
}

output "rdp_password" {
  description = "Auto-generated RDP password (stored in Secret Manager secret claude-vertex-rdp-password)"
  value       = module.security.rdp_password
  sensitive   = true
}

output "rdp_connection_info" {
  description = "Instructions to retrieve RDP credentials and connect via Microsoft Remote Desktop / Remmina"
  value       = "RDP Host: ${module.compute.vm_external_ip}:3389 | Username: demo | Retrieve Password: gcloud secrets versions access latest --secret=${module.security.rdp_secret_id} --project=${var.project_id}"
}

output "ssh_command" {
  description = "One-line gcloud IAP SSH command to connect directly to the demo workstation"
  value       = "gcloud compute ssh ${module.compute.vm_name} --project=${var.project_id} --zone=${var.zone} --tunnel-through-iap"
}

output "demo_quickstart" {
  description = "Commands to run inside the RDP terminal or SSH session"
  value       = "cd /opt/claude-code-with-gemini && ./small.sh"
}
