# Le versioni, bloccate.
#
# Un `terraform apply` che si comporta diversamente oggi e fra un mese non è
# infrastruttura come codice: è infrastruttura come speranza. Il file
# .terraform.lock.hcl che nasce con `terraform init` va committato, e fissa le
# versioni esatte e le loro impronte.

terraform {
  required_version = ">= 1.9"

  required_providers {
    # Installa il chart. È il sostituto di `app.sh install`.
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3"
    }
    # Serve per il namespace: crearlo qui, invece di lasciarlo fare a Helm,
    # significa che Terraform sa di possederlo e lo rimuove con `destroy`.
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.2"
    }
  }
}
