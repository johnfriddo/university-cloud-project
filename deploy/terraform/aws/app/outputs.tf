output "app_url" {
  description = "L'indirizzo dell'applicazione: l'ALB creato dall'Ingress."
  value       = local.app_origin
}

output "cluster_name" {
  value = local.infra.cluster_name
}

output "namespace" {
  value = kubernetes_namespace_v1.app.metadata[0].name
}

output "image_tag" {
  description = "L'etichetta delle immagini installate adesso."
  value       = var.image_tag
}
