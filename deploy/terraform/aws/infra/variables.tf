variable "region" {
  description = "Regione AWS. La passa tf.sh, letta da `aws configure`."
  type        = string
}

variable "state_bucket" {
  description = "Bucket dello stato di Terraform. Lo passa tf.sh; qui non serve, ma tf.sh lo dà a entrambe le parti."
  type        = string
  default     = ""
}

variable "name" {
  description = "Prefisso di ogni risorsa."
  type        = string
  default     = "media-platform"
}

variable "kubernetes_version" {
  description = "Versione di Kubernetes su EKS."
  type        = string
  # La più recente in supporto standard a settembre 2026.
  default = "1.36"
}

variable "postgres_version" {
  description = "Versione di PostgreSQL su RDS: la stessa maggiore del locale (postgres:16)."
  type        = string
  default     = "16.15"
}

variable "rabbitmq_version" {
  description = "Versione di RabbitMQ su Amazon MQ."
  type        = string
  # In locale gira la 3.13. Amazon MQ offre 3.13, 4.2 e 4.3; si resta sulla
  # stessa maggiore, per non cambiare broker e ambiente nello stesso passo.
  default = "3.13"
}

variable "alert_email" {
  description = "Indirizzo che riceve gli avvisi del notification-service via SNS. Vuoto: nessuna iscrizione."
  type        = string
  default     = ""
}
