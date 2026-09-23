variable "region" {
  description = "Regione AWS. La passa tf.sh."
  type        = string
}

variable "state_bucket" {
  description = "Bucket dello stato: qui serve davvero, per leggere quello dell'altra parte."
  type        = string
}

variable "namespace" {
  description = "Il namespace dell'applicazione. Lo stesso nome vale nelle condizioni dei ruoli IRSA."
  type        = string
  default     = "media-platform"
}

variable "chart_path" {
  description = "Il chart, lo stesso del deployment locale."
  type        = string
  default     = "../../../helm/media-platform"
}

variable "image_tag" {
  description = "L'etichetta delle immagini su ECR. La scrive scripts/push-images.sh."
  type        = string
  default     = "latest"
}

variable "keda_version" {
  description = "Versione di KEDA, la stessa del cluster locale."
  type        = string
  default     = "2.20.2"
}

variable "install_timeout_seconds" {
  description = "Quanto Terraform aspetta che i pod siano pronti."
  type        = number
  default     = 900
}
