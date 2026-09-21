# Cosa sapere quando `terraform apply` ha finito.

output "indirizzo" {
  description = "Dove si apre l'applicazione"
  value       = "http://${var.ingress_host}"
}

output "namespace" {
  description = "Il recinto in cui vive l'applicazione"
  value       = kubernetes_namespace_v1.app.metadata[0].name
}

output "versione_installata" {
  description = "Chart e versione dell'applicazione effettivamente installati"
  value       = "${helm_release.media_platform.chart} ${helm_release.media_platform.version}"
}

output "come_verificare" {
  description = "I comandi che dicono se ha funzionato"
  value = join("\n", [
    "deploy/cluster/smoke.sh        un giro completo, come il browser",
    "deploy/cluster/app.sh test     i 54 test di integrazione, dentro il cluster",
    "deploy/cluster/faults.sh       cinque guasti veri",
  ])
}

output "promemoria" {
  description = "Il nome di dominio non si risolve da solo"
  value = join(" ", [
    "Perché ${var.ingress_host} funzioni dal Mac serve una riga nel suo /etc/hosts,",
    "con l'indirizzo del nodo che ospita l'ingresso.",
  ])
}
