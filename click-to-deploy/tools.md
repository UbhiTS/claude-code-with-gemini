# Prerequisites & Tools for Click-to-Deploy

- **Google Cloud SDK (`gcloud`)**: `>= 470.0.0` (with IAP TCP tunneling support)
- **Terraform**: `>= 1.5.0`
- **Google Cloud IAM Permissions**:
  - `roles/owner` or `roles/editor` on the target demo project
  - `roles/orgpolicy.policyAdmin` (if deploying inside an Argolis organization to relax `compute.vmExternalIpAccess` and `compute.requireOsLogin`)
  - Vertex AI Model Garden access enabled for **Claude Opus 5.5**, **Claude Sonnet 5**, and **Gemini 3.8 Flash**
