variable "project_id" {
  description = "The GCP Project ID where the Claude Code + Vertex AI Hybrid Demo will be deployed."
  type        = string
}

variable "region" {
  description = "The primary GCP region for networking and Vertex AI routing."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "The GCP zone for the Claude Code demo workstation VM."
  type        = string
  default     = "us-central1-a"
}

variable "owner_Check" {
  description = "Acknowledge project owner prerequisites."
  type        = string
  default     = "Yes"
}

variable "deployment_mode" {
  description = "Deployment mode for the demo environment."
  type        = string
  default     = "automated"
}
