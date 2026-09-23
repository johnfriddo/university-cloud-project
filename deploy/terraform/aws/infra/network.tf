# La rete: una VPC su due zone di disponibilità.
#
# subnet pubbliche   il load balancer e il NAT gateway — nient'altro
# subnet private     nodi, database, broker: irraggiungibili da internet
#
# Due zone e non tre: EKS e RDS ne pretendono almeno due, e una terza
# comprerebbe una resistenza ai guasti che questo progetto non misura.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.7"

  name = var.name
  cidr = "10.0.0.0/16"
  azs  = local.azs

  # Le subnet private sono grandi di proposito: con la rete di EKS ogni pod
  # prende un indirizzo vero della VPC, quindi una /24 finirebbe molto prima dei nodi.
  private_subnets = ["10.0.0.0/19", "10.0.32.0/19"]
  public_subnets  = ["10.0.96.0/24", "10.0.97.0/24"]

  # Le subnet private raggiungono internet (per scaricare immagini, per chiamare
  # le API di AWS) attraverso un NAT gateway. Uno, non uno per zona: a 0,052 USD
  # l'ora ciascuno, il secondo comprerebbe una tolleranza al guasto di una zona
  # che a questo progetto non serve, e raddoppierebbe la voce di rete più cara.
  enable_nat_gateway = true
  single_nat_gateway = true

  enable_dns_hostnames = true
  enable_dns_support   = true

  # Come il load balancer trova le proprie subnet: EKS Auto Mode cerca queste
  # etichette quando un Ingress chiede un ALB affacciato su internet.
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}
