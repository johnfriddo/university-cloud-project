# I quattro archivi dati, adesso servizi gestiti.
#
# Nel cluster locale erano StatefulSet con un disco sul nodo, e distruggere il
# cluster distruggeva i dati. Qui vivono accanto al cluster, non dentro: la
# parte `app` si può distruggere e reinstallare e ogni utente, immagine e
# variante è ancora al suo posto.

# Chi può parlare al database e al broker: qualunque cosa dentro la VPC, che in
# pratica sono i pod. Niente da fuori — nessuno dei due ha un indirizzo pubblico.
resource "aws_security_group" "data" {
  name        = "${var.name}-data"
  description = "PostgreSQL e Amazon MQ, raggiungibili solo dalla VPC"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "PostgreSQL"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [module.vpc.vpc_cidr_block]
  }
  ingress {
    description = "AMQP su TLS"
    from_port   = 5671
    to_port     = 5671
    protocol    = "tcp"
    cidr_blocks = [module.vpc.vpc_cidr_block]
  }
  ingress {
    description = "API di gestione di RabbitMQ (dlq-watch, KEDA)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [module.vpc.vpc_cidr_block]
  }
}

# --- PostgreSQL → Amazon RDS ------------------------------------------------

resource "aws_db_subnet_group" "postgres" {
  name       = var.name
  subnet_ids = module.vpc.private_subnets
}

resource "aws_db_instance" "postgres" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = var.postgres_version
  # La classe Graviton più piccola: il database contiene utenti, immagini e
  # varianti, pochi kilobyte per immagine.
  instance_class = "db.t4g.micro"

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = "media"
  username = "media"
  password = random_password.postgres.result

  db_subnet_group_name   = aws_db_subnet_group.postgres.name
  vpc_security_group_ids = [aws_security_group.data.id]
  publicly_accessible    = false

  # I backup automatici che la versione locale non aveva (appendice B della
  # relazione). Un giorno basta a mostrarne il meccanismo.
  backup_retention_period = 1

  # Un database di laboratorio: distruggerlo non deve fermarsi su un'istantanea
  # finale che nessuno ripristinerà, né su una protezione che nessuno toglierà.
  skip_final_snapshot = true
  deletion_protection = false
  apply_immediately   = true
}

# --- RabbitMQ → Amazon MQ ---------------------------------------------------

resource "aws_mq_broker" "rabbitmq" {
  broker_name = var.name

  engine_type    = "RabbitMQ"
  engine_version = var.rabbitmq_version
  # Richiesto da Amazon MQ per RabbitMQ dalla 3.13 in poi: le versioni minori
  # vengono applicate durante la finestra di manutenzione.
  auto_minor_version_upgrade = true

  # La taglia più piccola che Amazon MQ offre ancora per RabbitMQ (la t3.micro è
  # stata ritirata). Istanza singola: un cluster di tre nodi triplicherebbe la
  # voce più cara della bolletta.
  host_instance_type = "mq.m7g.medium"
  deployment_mode    = "SINGLE_INSTANCE"

  publicly_accessible = false
  subnet_ids          = [module.vpc.private_subnets[0]]
  security_groups     = [aws_security_group.data.id]

  user {
    username = "media"
    password = random_password.rabbitmq.result
  }
}

# --- MongoDB → DynamoDB -----------------------------------------------------

resource "aws_dynamodb_table" "metadata" {
  name = "${var.name}-metadata"
  # Pagamento a richiesta: nessuna capacità da dimensionare, e costo zero quando
  # è fermo — che, per questo progetto, è quasi sempre.
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "asset_id"

  attribute {
    name = "asset_id"
    type = "S"
  }
  attribute {
    name = "user_id"
    type = "S"
  }

  # La ricerca sui metadati tecnici appartiene sempre a un utente solo. Questo
  # indice le fa leggere i documenti **di quell'utente** invece dell'intera
  # tabella: i filtri su obiettivo, ISO e il resto vengono comunque applicati
  # documento per documento, ma su una libreria, non su quella di tutti. È la
  # differenza fra una ricerca che rallenta al crescere del servizio e una che rallenta al crescere di un utente.
  global_secondary_index {
    name            = "by_user"
    projection_type = "ALL"

    key_schema {
      attribute_name = "user_id"
      key_type       = "HASH"
    }
  }
}

# --- MinIO → Amazon S3 ------------------------------------------------------

data "aws_caller_identity" "current" {}

locals {
  # I nomi dei bucket sono globali su ogni account AWS del mondo: il numero
  # dell'account li rende unici senza scriverlo nel repository.
  buckets = {
    originals = "${var.name}-originals-${data.aws_caller_identity.current.account_id}"
    derived   = "${var.name}-derived-${data.aws_caller_identity.current.account_id}"
  }
}

resource "aws_s3_bucket" "media" {
  for_each = local.buckets
  bucket   = each.value

  # Così `terraform destroy` funziona anche con le immagini ancora dentro. Per un laboratorio.
  force_destroy = true
}

# Niente è pubblico: ogni oggetto si legge o si scrive con un link firmato,
# esattamente come con MinIO.
resource "aws_s3_bucket_public_access_block" "media" {
  for_each = aws_s3_bucket.media
  bucket   = each.value.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

# La regola che permette al browser di caricare dall'indirizzo della pagina a
# quello di S3 la scrive la parte `app`: deve nominare l'indirizzo del load
# balancer, che non esiste finché l'Ingress dell'applicazione non è stato creato.

# --- Credentials ------------------------------------------------------------

# Solo lettere e cifre: finiscono dentro gli indirizzi di connessione, dove «:»,
# «@» o «/» andrebbero protetti, e Amazon MQ rifiuta parecchi simboli.
resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "random_password" "rabbitmq" {
  length  = 32
  special = false
}

resource "random_password" "jwt" {
  length  = 64
  special = false
}
