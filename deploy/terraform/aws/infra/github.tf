# Come GitHub Actions può distribuire, senza una sola chiave nel repository.
#
# La stessa idea di IRSA, un livello più su: GitHub firma un token per ogni
# esecuzione del workflow, che dice a quale repository e a quale ramo
# appartiene; AWS lo verifica con questo fornitore e restituisce credenziali
# temporanee. Non c'è nessuna chiave da conservare fra i segreti del repository, e quindi nessuna da perdere.
#
# Si attiva valorizzando `github_repository`; lasciato vuoto, qui non si crea niente.

variable "github_repository" {
  description = "Il repository che può distribuire, come «utente/repo». Vuoto: nessun accesso da GitHub."
  type        = string
  default     = ""
}

variable "github_branch" {
  description = "Il ramo da cui si può distribuire. Solo quello."
  type        = string
  default     = "main"
}

locals {
  github_enabled = var.github_repository != ""
}

resource "aws_iam_openid_connect_provider" "github" {
  count = local.github_enabled ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # L'impronta della catena di certificati. AWS non la verifica più per questo
  # fornitore dal 2023, ma il campo è ancora obbligatorio.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_trust" {
  count = local.github_enabled ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Questo repository, questo ramo, e nient'altro. Senza questa condizione
    # **qualunque** repository GitHub del mondo potrebbe assumere il ruolo.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/${var.github_branch}"]
    }
  }
}

data "aws_iam_policy_document" "github_permissions" {
  count = local.github_enabled ? 1 : 0

  # Push the images.
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
    ]
    resources = [for r in aws_ecr_repository.app : r.arn]
  }

  # Legge lo stato, e prende il lucchetto mentre scrive.
  statement {
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.state_bucket}"]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/*"]
  }

  # Quello che serve alla parte `app` di Terraform: raggiungere il cluster,
  # leggere le credenziali e scrivere la regola CORS che nomina il load balancer.
  statement {
    actions   = ["eks:DescribeCluster"]
    resources = [module.eks.cluster_arn]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.app.arn]
  }
  statement {
    actions   = ["s3:GetBucketCors", "s3:PutBucketCors"]
    resources = [for b in aws_s3_bucket.media : b.arn]
  }
}

resource "aws_iam_role" "github" {
  count              = local.github_enabled ? 1 : 0
  name               = "${var.name}-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_trust[0].json
}

resource "aws_iam_role_policy" "github" {
  count  = local.github_enabled ? 1 : 0
  name   = "distribuzione"
  role   = aws_iam_role.github[0].id
  policy = data.aws_iam_policy_document.github_permissions[0].json
}

# Il permesso dentro il cluster. Un ruolo IAM che può chiamare EKS non è
# ancora autorizzato a fare niente **in** Kubernetes: quella è una concessione a parte, ed è questa.
# questa.
resource "aws_eks_access_entry" "github" {
  count         = local.github_enabled ? 1 : 0
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.github[0].arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "github" {
  count         = local.github_enabled ? 1 : 0
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.github[0].arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.github]
}

output "github_role_arn" {
  description = "Il ruolo che il workflow assume. Va messo fra le variabili del repository come AWS_ROLE_ARN."
  value       = local.github_enabled ? aws_iam_role.github[0].arn : ""
}
