# Registro delle immagini, avvisi e deposito dei segreti.

# --- ECR: da dove il cluster scarica le immagini ----------------------------
#
# In locale le immagini venivano importate a mano in ogni nodo (images.sh), con
# pullPolicy Never. Qui i nodi vanno e vengono da soli, quindi hanno bisogno di
# un registro da cui scaricarle.

locals {
  images = ["api-service", "worker-service", "notification-service", "frontend"]
}

resource "aws_ecr_repository" "app" {
  for_each = toset(local.images)
  name     = "${var.name}/${each.key}"

  # Un'etichetta si può ripubblicare. Comodo in un progetto di due giorni; in
  # produzione le etichette sarebbero immutabili e ogni build ne avrebbe una nuova.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  force_delete = true
}

# Si tengono solo le ultime cinque immagini di ogni servizio: ogni
# pubblicazione aggiungerebbe altrimenti qualche centinaio di megabyte
resource "aws_ecr_lifecycle_policy" "app" {
  for_each   = aws_ecr_repository.app
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Tieni le ultime cinque immagini"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# --- SNS: il canale degli avvisi --------------------------------------------
#
# In locale il notification-service scriveva l'avviso nel proprio registro. Qui
# lo pubblica su un argomento, e chi è iscritto lo riceve come email.

resource "aws_sns_topic" "notifications" {
  name = "${var.name}-notifications"
}

# L'iscrizione va confermata cliccando il link che manda AWS: fino ad allora
# SNS non consegna niente a quell'indirizzo. È la difesa di AWS contro chi
# iscrive la casella di qualcun altro.
resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# --- Secrets Manager: dove vivono le credenziali ----------------------------
#
# L'unica fonte di verità per le password. La parte `app` le legge da qui — non
# dalle uscite di questa parte — e le consegna ai pod come
# Secret di Kubernetes.

resource "aws_secretsmanager_secret" "app" {
  name        = "${var.name}/app"
  description = "Credenziali dell'applicazione: database, broker, firma dei token"

  # Cancellato subito al destroy. Il valore predefinito è una finestra di
  # recupero di 30 giorni, durante i quali il nome resta occupato e l'apply successivo fallirebbe.
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    postgres_user     = aws_db_instance.postgres.username
    postgres_password = random_password.postgres.result
    rabbitmq_user     = "media"
    rabbitmq_password = random_password.rabbitmq.result
    jwt_secret        = random_password.jwt.result
  })
}
