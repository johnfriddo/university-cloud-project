# L'applicazione su EKS: lo stesso chart del cluster locale, con i valori
# che descrivono AWS.
#
# Nessuno copia a mano un indirizzo o una password. Gli indirizzi arrivano dallo
# stato dell'altra parte, le credenziali da Secrets Manager: questo file è
# l'unico posto in cui i due si incontrano.

# Le credenziali, lette da dove vivono. Non dalle uscite di `infra`: una
# password che passa da un'uscita è una password in un posto in più.
data "aws_secretsmanager_secret_version" "app" {
  secret_id = local.infra.secret_arn
}

locals {
  credentials = jsondecode(data.aws_secretsmanager_secret_version.app.secret_string)

  # Lo stesso trucco della parte locale: per Terraform il chart è una stringa,
  # quindi un template modificato passerebbe altrimenti inosservato («0 changed»
  # mentre non è stato installato niente). L'impronta rende visibile ogni modifica.
  chart_checksum = sha1(join("", [
    for f in fileset(var.chart_path, "**") : filesha1("${var.chart_path}/${f}")
  ]))

  values = {
    platform = "aws"

    aws = {
      region        = var.region
      postgresHost  = local.infra.postgres_address
      rabbitmqHost  = replace(replace(local.infra.rabbitmq_endpoint, "amqps://", ""), ":5671", "")
      metadataTable = local.infra.metadata_table
      snsTopicArn   = local.infra.sns_topic_arn
      roles = {
        apiService          = local.infra.service_role_arns["api-service"]
        workerService       = local.infra.service_role_arns["worker-service"]
        notificationService = local.infra.service_role_arns["notification-service"]
      }
    }

    # Solo x86, perché è quello che il piano gratuito permette a questo account di
    # accendere (vedi il NodePool in cluster.tf). Le immagini sono costruite di conseguenza.
    nodeSelector = {
      "kubernetes.io/arch" = "amd64"
    }

    image = {
      # ECR, un repository per servizio. Il chart compone
      # <repository>/<servizio>:<etichetta>, che è esattamente la forma di ECR.
      repository = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com/media-platform"
      tag        = var.image_tag
      # «Never» andava bene su un cluster senza registro. Qui un registro c'è.
      pullPolicy = "IfNotPresent"
    }

    app = {
      bucketOriginals = local.infra.buckets["originals"]
      bucketDerived   = local.infra.buckets["derived"]
    }

    workerService = {
      autoscaling = {
        # Il chart ne permette 3, che era il numero giusto sul portatile: oltre
        # quello, sui due nodi da 1,4 GB Kubernetes non trovava dove mettere la
        # replica. Qui il tetto può essere più alto proprio perché il limite di
        # là non esiste: quando i pod non entrano più, Karpenter aggiunge una
        # macchina. È la differenza fra scalare i pod e scalare i nodi, ed è
        # l'unico modo di vederla accadere.
        maxReplicas = 8
      }
    }

    secrets = {
      postgresUser     = local.credentials.postgres_user
      postgresPassword = local.credentials.postgres_password
      rabbitmqUser     = local.credentials.rabbitmq_user
      rabbitmqPassword = local.credentials.rabbitmq_password
      jwtSecret        = local.credentials.jwt_secret
    }
  }
}

data "aws_caller_identity" "current" {}

resource "helm_release" "media_platform" {
  name      = "media-platform"
  chart     = var.chart_path
  namespace = kubernetes_namespace_v1.app.metadata[0].name

  values = [yamlencode(local.values)]

  set = [{
    name  = "chartChecksum"
    value = local.chart_checksum
  }]

  wait          = true
  wait_for_jobs = true
  timeout       = var.install_timeout_seconds
  atomic        = true

  depends_on = [
    helm_release.keda,
    kubernetes_manifest.ingress_class,
  ]
}

# --- La regola che permette al browser di caricare su S3 --------------------
#
# In locale tutto rispondeva su un nome solo, quindi per il browser la pagina e
# l'archivio erano la stessa origine e non serviva nessun permesso. Qui la
# pagina sta sul load balancer e il file va su S3: due indirizzi diversi, e il
# browser non invia se S3 non dichiara che quell'indirizzo può.
#
# Si noti cosa questo **non** protegge: il caricamento in sé è autorizzato dalla
# firma, che copre chiave, metodo e tipo di contenuto e dura quindici minuti.
# Il CORS decide soltanto da quali pagine un browser può usarla.

# L'indirizzo del load balancer, che esiste solo dopo che EKS l'ha creato.
resource "terraform_data" "wait_for_load_balancer" {
  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${local.infra.cluster_name} --region ${var.region} >/dev/null
      kubectl wait --namespace ${var.namespace} --timeout=300s \
        --for=jsonpath='{.status.loadBalancer.ingress[0].hostname}' \
        ingress/media-platform
    EOT
  }

  depends_on = [helm_release.media_platform]
}

data "kubernetes_ingress_v1" "app" {
  metadata {
    name      = "media-platform"
    namespace = var.namespace
  }
  depends_on = [terraform_data.wait_for_load_balancer]
}

locals {
  app_origin = "http://${data.kubernetes_ingress_v1.app.status[0].load_balancer[0].ingress[0].hostname}"
}

resource "aws_s3_bucket_cors_configuration" "originals" {
  bucket = local.infra.buckets["originals"]

  cors_rule {
    # Solo la pagina di questo deployment, non «*»: un link firmato che sfugge
    # non può poi essere usato da un sito qualunque del mondo.
    allowed_origins = [local.app_origin]
    allowed_methods = ["PUT"]
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }

  lifecycle {
    precondition {
      condition     = local.app_origin != "http://"
      error_message = "L'ALB non ha ancora un indirizzo: rilancia `tf.sh app apply` fra un minuto."
    }
  }
}
