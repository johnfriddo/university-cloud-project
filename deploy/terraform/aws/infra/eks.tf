# Il cluster: EKS in Auto Mode.
#
# Nel cluster locale kubeadm lasciava fuori tutto tranne Kubernetes stesso, e il
# resto lo aggiungeva Ansible: rete fra i pod, dischi, misuratore dei consumi,
# ingresso. In Auto Mode AWS li fa girare come parte del servizio — compreso il
# controller che trasforma un Ingress in un Application Load Balancer, e quello
# che aggiunge un nodo quando i pod non ci stanno più. Quest'ultimo è il punto
# del capitolo 9 della relazione: sul portatile KEDA aggiungeva pod ma non processori; qui può.
#
# Quali macchine Auto Mode possa accendere lo decide la parte `app`, con un
# NodePool: è un oggetto di Kubernetes, e questa parte con Kubernetes non parla
# mai.

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.25"

  name               = var.name
  kubernetes_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # Il server delle API è raggiungibile da internet, protetto da IAM: kubectl e
  # Terraform girano su un portatile, non dentro la VPC. Un endpoint solo privato
  # richiederebbe una VPN o un bastione, e nessuno dei due è nello stack.
  endpoint_public_access = true

  # Chi lancia `terraform apply` diventa amministratore del cluster. Senza questo
  # il cluster sarebbe creato da un'identità che poi non può usarlo.
  enable_cluster_creator_admin_permissions = true

  # Solo il gruppo predefinito «system», per i componenti che AWS stessa fa
  # girare come pod. Porta anche la NodeClass «default» a cui si riferisce il
  # NodePool dell'applicazione. Il gruppo general-purpose resta fuori: lascerebbe
  # scegliere ad AWS anche macchine che non vogliamo.
  compute_config = {
    enabled    = true
    node_pools = ["system"]
  }

  # IRSA: il fornitore d'identità OIDC che permette a un pod di assumere un ruolo
  # IAM. È così che l'api-service raggiunge S3 e DynamoDB senza nessuna chiave (irsa.tf).
  enable_irsa = true

  # I log del piano di controllo andrebbero su CloudWatch, fatturati al gigabyte
  # ingerito. Quello di audit in particolare è prolisso, e qui non lo leggerebbe nessuno.
  enabled_log_types           = []
  create_cloudwatch_log_group = false
}
