# Versioni bloccate, come nella parte locale: il file di lock che scrive
# `terraform init` va committato.

terraform {
  required_version = ">= 1.10"

  # Lo stato vive su S3; bucket e regione li passa tf.sh al momento di init,
  # perché questo blocco non può leggere variabili. `use_lockfile` rende
  # impossibili due apply simultanei senza la tabella DynamoDB che le
  # configurazioni più vecchie usavano per il lucchetto.
  backend "s3" {
    key          = "infra/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

provider "aws" {
  region = var.region

  # Ogni risorsa riceve queste etichette: è così che la bolletta di AWS si può
  # filtrare su questo progetto — ed è così che si riconosce una risorsa dimenticata.
  default_tags {
    tags = {
      Project   = "media-platform"
      ManagedBy = "terraform"
    }
  }
}
