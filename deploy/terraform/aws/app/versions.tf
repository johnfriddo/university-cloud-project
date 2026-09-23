terraform {
  required_version = ">= 1.10"

  backend "s3" {
    key          = "app/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.2"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3"
    }
  }
}

# Quello che ha costruito l'altra parte. Letto dallo stato condiviso invece che
# copiato a mano: un indirizzo che esiste in due posti è un indirizzo che prima
# o poi finirà per non essere d'accordo con se stesso.
data "terraform_remote_state" "infra" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "infra/terraform.tfstate"
    region = var.region
  }
}

locals {
  infra = data.terraform_remote_state.infra.outputs
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "media-platform"
      ManagedBy = "terraform"
    }
  }
}

# Come Terraform parla al cluster: nessun file kubeconfig, nessuna credenziale
# di lunga durata. `aws eks get-token` chiede ad AWS un token che dura minuti,
# esattamente come fa kubectl.
provider "kubernetes" {
  host                   = local.infra.cluster_endpoint
  cluster_ca_certificate = base64decode(local.infra.cluster_certificate_authority_data)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", local.infra.cluster_name, "--region", var.region]
  }
}

provider "helm" {
  kubernetes = {
    host                   = local.infra.cluster_endpoint
    cluster_ca_certificate = base64decode(local.infra.cluster_certificate_authority_data)

    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", local.infra.cluster_name, "--region", var.region]
    }
  }
}
