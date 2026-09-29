output "vm_name" {
  description = "Name of the provisioned Claude Code + Vertex AI demo VM"
  value       = module.compute.vm_name
}

output "vm_zone" {
  description = "Zone of the provisioned demo VM"
  value       = var.zone
}

output "ssh_command" {
  description = "One-line gcloud IAP SSH command to connect directly to the demo workstation"
  value       = "gcloud compute ssh ${module.compute.vm_name} --project=${var.project_id} --zone=${var.zone} --tunnel-through-iap"
}

output "demo_quickstart" {
  description = "Commands to run inside the SSH session"
  value       = "cd /opt/claude-code-with-gemini && ./small.sh"
}
