module "iam" {
  source     = "./terraform-modules/iam"
  project_id = var.project_id
}

module "security" {
  source     = "./terraform-modules/security"
  project_id = var.project_id
  depends_on = [module.iam]
}

module "network" {
  source     = "./terraform-modules/network"
  project_id = var.project_id
  region     = var.region
  depends_on = [module.security]
}

module "compute" {
  source                = "./terraform-modules/compute"
  project_id            = var.project_id
  region                = var.region
  zone                  = var.zone
  network_self_link     = module.network.network_self_link
  subnetwork_self_link  = module.network.subnetwork_self_link
  service_account_email = module.iam.service_account_email
  depends_on            = [module.network]
}
