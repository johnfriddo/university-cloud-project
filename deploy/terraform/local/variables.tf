# I valori che si possono cambiare senza toccare il resto.
#
# Nessuno di questi è una credenziale, ma questo **non** significa che le
# credenziali restino fuori da Terraform. È una cosa importante da sapere e
# facile da credere al contrario:
#
#   lo stato di Terraform contiene, in chiaro, tutto ciò che Terraform gestisce.
#
# Qui il chart riceve `values.local.yaml`, e il contenuto di quel file — le
# cinque password comprese — finisce dentro `terraform.tfstate`. Verificato,
# non supposto: ogni password compare due volte nello stato.
#
# Marcare una variabile `sensitive` non cambierebbe niente: nasconde il valore
# nell'uscita del comando, non nel file. Le difese vere sono altre — lo stato
# è escluso da git, va tenuto leggibile solo al proprietario, e in Fase 7 vive
# su S3 cifrato con il blocco su DynamoDB, come prevede il documento di
# progetto.

variable "kubeconfig_path" {
  description = "Le credenziali del cluster, che Ansible consegna al Mac a fine installazione"
  type        = string
  default     = "~/.kube/media-platform.conf"
}

variable "chart_path" {
  description = "Il chart dell'applicazione, dentro questa stessa repo"
  type        = string
  default     = "../../helm/media-platform"
}

variable "values_file" {
  description = "I valori del chart, credenziali comprese. Fuori da git."
  type        = string
  default     = "../../helm/values.local.yaml"
}

variable "namespace" {
  description = "Il recinto in cui vive l'applicazione"
  type        = string
  default     = "media-platform"
}

variable "release_name" {
  description = "Il nome con cui Helm registra l'installazione"
  type        = string
  default     = "media-platform"
}

variable "ingress_host" {
  description = "Il nome di dominio da cui si entra. Deve stare nel /etc/hosts del Mac."
  type        = string
  default     = "media-platform.test"
}

variable "install_timeout_seconds" {
  description = "Quanto aspettare che i pod siano pronti davvero"
  type        = number
  # La prima installazione crea quattro dischi e avvia quattro archivi dati su
  # due macchine virtuali che condividono lo stesso disco fisico.
  default = 600
}
