# Quello che la parte `app` deve sapere. Lo legge attraverso lo stato condiviso
# su S3 (terraform_remote_state), così nessun valore viene copiato a mano fra le due.
#
# Nessuna password qui: la parte `app` le legge da Secrets Manager.

output "region" {
  value = var.region
}

output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "cluster_certificate_authority_data" {
  value = module.eks.cluster_certificate_authority_data
}

output "postgres_address" {
  value = aws_db_instance.postgres.address
}

output "postgres_database" {
  value = aws_db_instance.postgres.db_name
}

# amqps://b-….mq.eu-central-1.on.aws:5671
output "rabbitmq_endpoint" {
  value = aws_mq_broker.rabbitmq.instances[0].endpoints[0]
}

# https://b-….mq.eu-central-1.on.aws — l'API di gestione, per dlq-watch.
output "rabbitmq_console_url" {
  value = aws_mq_broker.rabbitmq.instances[0].console_url
}

output "metadata_table" {
  value = aws_dynamodb_table.metadata.name
}

output "buckets" {
  value = local.buckets
}

output "bucket_arns" {
  value = { for k, b in aws_s3_bucket.media : k => b.arn }
}

output "ecr_repositories" {
  value = { for k, r in aws_ecr_repository.app : k => r.repository_url }
}

output "sns_topic_arn" {
  value = aws_sns_topic.notifications.arn
}

output "secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "service_role_arns" {
  value = { for k, r in aws_iam_role.service : k => r.arn }
}
