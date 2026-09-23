# Quello che deve esistere nel cluster prima dell'applicazione: le macchine su
# cui può girare, la classe d'ingresso e l'autoscaler.
#
# Sono l'equivalente di ciò che Ansible installa sul cluster locale — rete,
# dischi, controller d'ingresso, KEDA. Su EKS la maggior parte arriva col
# servizio, e quel che resta sono tre oggetti e un chart.

resource "kubernetes_namespace_v1" "app" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "media-platform"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# --- Quali macchine EKS può accendere ---------------------------------------

resource "kubernetes_manifest" "node_pool" {
  manifest = {
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata   = { name = "media-platform" }
    spec = {
      template = {
        spec = {
          nodeClassRef = {
            group = "eks.amazonaws.com"
            kind  = "NodeClass"
            name  = "default"
          }
          # Questi tre requisiti non sono una preferenza: sono le uniche macchine che
          # questo account può accendere, e scoprirlo è costato
          # un'installazione fallita.
          #
          # L'account è sul piano gratuito di AWS, che rifiuta qualunque tipo di
          # macchina fuori dal free tier — Graviton t4g.medium compreso:
          #
          # The specified instance type is not eligible for Free Tier
          #
          # Ed EKS Auto Mode rifiuta qualunque taglia sotto «large». Le taglie
          # piccole del free tier (t3.micro, t4g.small) restano quindi fuori anche loro.
          # Quello che sopravvive a entrambe le regole è esattamente questo: le
          # macchine «flex» large, che sono Intel e non ARM — ed è il motivo per cui
          # le immagini si costruiscono per x86 (deploy/terraform/aws/images.sh).
          requirements = [
            {
              key      = "kubernetes.io/arch"
              operator = "In"
              values   = ["amd64"]
            },
            {
              key      = "eks.amazonaws.com/instance-family"
              operator = "In"
              values   = ["m7i-flex", "c7i-flex"]
            },
            {
              key      = "eks.amazonaws.com/instance-size"
              operator = "In"
              values   = ["large"]
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = ["on-demand"]
            },
          ]
        }
      }

      # Il tetto, e la ragione vera per cui sta qui: è il freno della bolletta.
      # Qualunque cosa chieda KEDA, AWS non accende macchine oltre questo — così un
      # errore nell'autoscaler non può diventare una sorpresa sulla fattura.
      limits = {
        cpu    = "8"
        memory = "32Gi"
      }

      disruption = {
        # I nodi vuoti si rimuovono; quelli con dei pod sopra si lasciano stare.
        # L'impostazione aggressiva ricompatterebbe i pod per risparmiare, spostando
        # il worker a metà immagine per pochi centesimi.
        consolidationPolicy = "WhenEmpty"
        consolidateAfter    = "1m"
      }
    }
  }
}

# --- Come un Ingress diventa un load balancer ------------------------------
#
# In Auto Mode non c'è nessun controller da installare: è EKS stessa a
# guardare gli oggetti Ingress. Quello che le serve è una classe che dica
# «questo è mio», e le impostazioni specifiche di AWS, che in un Ingress non
# stanno, e vivono in un oggetto loro.

resource "kubernetes_manifest" "ingress_class_params" {
  manifest = {
    apiVersion = "eks.amazonaws.com/v1"
    kind       = "IngressClassParams"
    metadata   = { name = "alb" }
    spec = {
      # Su internet, nelle subnet pubbliche. I nodi restano privati: il load
      # balancer è l'unica cosa con un indirizzo pubblico.
      scheme = "internet-facing"
    }
  }
}

resource "kubernetes_manifest" "ingress_class" {
  manifest = {
    apiVersion = "networking.k8s.io/v1"
    kind       = "IngressClass"
    metadata   = { name = "alb" }
    spec = {
      controller = "eks.amazonaws.com/alb"
      parameters = {
        apiGroup = "eks.amazonaws.com"
        kind     = "IngressClassParams"
        name     = "alb"
      }
    }
  }
  depends_on = [kubernetes_manifest.ingress_class_params]
}

# --- KEDA -------------------------------------------------------------------
#
# La stessa versione del cluster locale, e lo stesso mestiere: legge la
# lunghezza della coda e la trasforma in un numero di repliche. Qui legge
# Amazon MQ invece di un RabbitMQ in un pod — su TLS, ed è tutta la differenza.

resource "helm_release" "keda" {
  name             = "keda"
  repository       = "https://kedacore.github.io/charts"
  chart            = "keda"
  version          = var.keda_version
  namespace        = "keda"
  create_namespace = true

  set = [
    {
      name  = "nodeSelector.kubernetes\\.io/arch"
      value = "amd64"
    },
  ]

  wait    = true
  timeout = 600

  depends_on = [kubernetes_manifest.node_pool]
}
