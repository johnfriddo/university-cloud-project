# L'applicazione sul cluster locale.
#
# Terraform qui non crea infrastruttura: il cluster esiste già quando parte, e
# l'ha costruito Ansible. Il suo compito è **dichiarare cosa ci deve stare
# sopra**, e il chart è il come.
#
# La linea fra i due strumenti: Ansible tocca le macchine, Terraform tocca il
# cluster. In Fase 7 quella linea resta, ma Terraform avrà anche l'altro
# mestiere — creare VPC, EKS, RDS — che in locale non ha senso.

provider "kubernetes" {
  config_path = var.kubeconfig_path
}

provider "helm" {
  kubernetes = {
    config_path = var.kubeconfig_path
  }
}

# Il recinto dell'applicazione.
#
# Creato qui e non da Helm (`create_namespace`) perché così Terraform sa di
# possederlo: un `terraform destroy` lo porta via insieme a tutto il resto,
# mentre un namespace creato da Helm resterebbe lì, vuoto, a ricordare che
# qualcosa non è stato pulito.
# Il suffisso `_v1` non è un vezzo: il provider nomina le risorse con la
# versione dell'API Kubernetes che usano, e la forma senza suffisso è
# deprecata. Adottarla adesso costa cinque caratteri.
resource "kubernetes_namespace_v1" "app" {
  # Blocco e non attributo: il provider kubernetes, a differenza di quello di
  # Helm, usa ancora la forma a blocco. È il motivo per cui più sotto si legge
  # `metadata[0].name` — un blocco può comparire più volte, quindi si indicizza.
  metadata {
    name = var.namespace

    labels = {
      "app.kubernetes.io/name"       = "media-platform"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

locals {
  # L'impronta di tutti i file del chart.
  #
  # Serve a rimediare a una trappola trovata sul campo: per Terraform il chart
  # è la **stringa** "../../helm/media-platform", e modificare un template non
  # cambia quella stringa. Dopo aver riscritto l'Ingress, `terraform apply` ha
  # risposto «0 changed» e non ha installato niente — silenziosamente.
  #
  # Passando l'impronta come un valore qualsiasi, una modifica a un qualunque
  # file del chart cambia i valori della release, e Terraform se ne accorge.
  # Il chart ignora questo valore: serve solo a farsi notare.
  chart_checksum = sha1(join("", [
    for f in fileset(var.chart_path, "**") : filesha1("${var.chart_path}/${f}")
  ]))
}

resource "helm_release" "media_platform" {
  name      = var.release_name
  chart     = var.chart_path
  namespace = kubernetes_namespace_v1.app.metadata[0].name

  # I valori, credenziali comprese, letti dal file che git ignora. È lo stesso
  # file che usa `app.sh install`: una sola fonte, due modi di installare.
  values = [file(var.values_file)]

  set = [{
    name  = "chartChecksum"
    value = local.chart_checksum
  }]

  # Torna quando i pod sono pronti davvero, non quando Kubernetes ha accettato
  # la richiesta. Senza, `terraform apply` direbbe «fatto» mentre l'applicazione
  # sta ancora partendo, e il passo successivo — i test — fallirebbe per fretta.
  wait          = true
  wait_for_jobs = true
  timeout       = var.install_timeout_seconds

  # Se l'installazione fallisce a metà, disfa quello che ha fatto invece di
  # lasciare il cluster in uno stato intermedio che nessuno ha descritto.
  atomic = true
}
