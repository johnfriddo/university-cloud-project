# IRSA: ruoli IAM che i pod assumono, al posto di chiavi scritte da qualche parte.
#
# Come funziona, in tre passi:
# 1. EKS firma un token per ogni pod, che dice «sono il service account X nel
# namespace Y» (il fornitore OIDC creato dal modulo eks);
# 2. la libreria dentro il pod presenta quel token ad AWS STS;
# 3. STS controlla la trust policy qui sotto e restituisce credenziali
# temporanee, valide un'ora e rinnovate da sole.
#
# Non esiste nessuna chiave d'accesso — ed è il motivo per cui la configurazione
# dell'archivio ha dovuto imparare a funzionare senza (appendice A della relazione).
#
# Un ruolo per servizio, ciascuno con i soli permessi di quel servizio. Il worker
# non può cancellare; il notification-service non tocca S3 affatto.

locals {
  namespace = "media-platform"
  oidc      = replace(module.eks.cluster_oidc_issuer_url, "https://", "")

  originals_arn = aws_s3_bucket.media["originals"].arn
  derived_arn   = aws_s3_bucket.media["derived"].arn
  table_arns = [
    aws_dynamodb_table.metadata.arn,
    "${aws_dynamodb_table.metadata.arn}/index/*",
  ]

  roles = {
    api-service = {
      statements = [
        {
          # HeadObject su una chiave che non c'è risponde 403 invece di 404 a meno
          # che il ruolo possa anche elencare il bucket: il passo di conferma
          # scambierebbe allora «non ancora caricato» per «vietato».
          actions   = ["s3:ListBucket"]
          resources = [local.originals_arn, local.derived_arn]
        },
        {
          # Put: firmare un link di caricamento è firmare per conto di questo ruolo.
          # Delete: l'endpoint di cancellazione e la pulizia notturna.
          actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
          resources = ["${local.originals_arn}/*", "${local.derived_arn}/*"]
        },
        {
          actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:DeleteItem", "dynamodb:DescribeTable"]
          resources = local.table_arns
        },
      ]
    }

    worker-service = {
      statements = [
        {
          actions   = ["s3:ListBucket"]
          resources = [local.originals_arn, local.derived_arn]
        },
        {
          actions   = ["s3:GetObject"]
          resources = ["${local.originals_arn}/*"]
        },
        {
          actions   = ["s3:PutObject"]
          resources = ["${local.derived_arn}/*"]
        },
        {
          actions   = ["dynamodb:PutItem", "dynamodb:DescribeTable"]
          resources = local.table_arns
        },
      ]
    }

    notification-service = {
      statements = [
        {
          actions   = ["sns:Publish"]
          resources = [aws_sns_topic.notifications.arn]
        },
      ]
    }
  }
}

data "aws_iam_policy_document" "trust" {
  for_each = local.roles

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    # Solo questo service account, in questo namespace. Senza la condizione
    # su «sub» qualunque pod del cluster potrebbe assumere il ruolo.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc}:sub"
      values   = ["system:serviceaccount:${local.namespace}:${each.key}"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "permissions" {
  for_each = local.roles

  dynamic "statement" {
    for_each = each.value.statements
    content {
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}

resource "aws_iam_role" "service" {
  for_each           = local.roles
  name               = "${var.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.trust[each.key].json
}

resource "aws_iam_role_policy" "service" {
  for_each = local.roles
  name     = "permessi"
  role     = aws_iam_role.service[each.key].id
  policy   = data.aws_iam_policy_document.permissions[each.key].json
}
