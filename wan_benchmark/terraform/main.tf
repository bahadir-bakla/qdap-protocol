# wan_benchmark/terraform/main.tf

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ─── eu-west-1 Provider (İrlanda — SENDER) ─────────────────────────────────
provider "aws" {
  alias  = "ireland"
  region = "eu-west-1"
}

# ─── ap-southeast-1 Provider (Singapur — RECEIVER) ─────────────────────────
provider "aws" {
  alias  = "singapore"
  region = "ap-southeast-1"
}


# ─── SSH Key Pair ───────────────────────────────────────────────────────────

resource "aws_key_pair" "qdap_ireland" {
  provider   = aws.ireland
  key_name   = "qdap-wan-key"
  public_key = file("~/.ssh/qdap-eu.pem.pub")
}

resource "aws_key_pair" "qdap_singapore" {
  provider   = aws.singapore
  key_name   = "qdap-wan-key"
  public_key = file("~/.ssh/qdap-sg.pem.pub")
}


# ─── Security Groups ────────────────────────────────────────────────────────

resource "aws_security_group" "qdap_sg_ireland" {
  provider    = aws.ireland
  name        = "qdap-wan-sg"
  description = "QDAP WAN benchmark"

  # SSH
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # QDAP ports
  ingress {
    from_port   = 19600
    to_port     = 19603
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "qdap_sg_singapore" {
  provider    = aws.singapore
  name        = "qdap-wan-sg"
  description = "QDAP WAN benchmark"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 19600
    to_port     = 19603
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}


# ─── EC2 Instances (t3.medium spot) ────────────────────────────────────────

resource "aws_spot_instance_request" "sender" {
  provider             = aws.ireland
  ami                  = "ami-0f9ae27ecf629cbe3"
  instance_type        = "t3.micro"
  spot_price           = "0.05"        # max $0.05/saat (genelde $0.01-0.02)
  wait_for_fulfillment = true
  key_name             = aws_key_pair.qdap_ireland.key_name
  security_groups      = [aws_security_group.qdap_sg_ireland.name]

  user_data = <<-EOF
    #!/bin/bash
    apt-get update -y
    apt-get install -y python3 python3-pip git
    pip3 install cryptography aiohttp
    cd /home/ubuntu
    git clone $${var.repo_url} quantum-protocol
    chown -R ubuntu:ubuntu quantum-protocol
  EOF

  tags = { Name = "qdap-wan-sender" }
}

resource "aws_spot_instance_request" "receiver" {
  provider             = aws.singapore
  ami                  = "ami-0434196a03595d088"
  instance_type        = "t3.micro"
  spot_price           = "0.05"
  wait_for_fulfillment = true
  key_name             = aws_key_pair.qdap_singapore.key_name
  security_groups      = [aws_security_group.qdap_sg_singapore.name]

  user_data = <<-EOF
    #!/bin/bash
    apt-get update -y
    apt-get install -y python3 python3-pip git
    pip3 install cryptography aiohttp
    cd /home/ubuntu
    git clone $${var.repo_url} quantum-protocol
    chown -R ubuntu:ubuntu quantum-protocol
  EOF

  tags = { Name = "qdap-wan-receiver" }
}


# ─── Outputs ────────────────────────────────────────────────────────────────

output "sender_ip" {
  value = aws_spot_instance_request.sender.public_ip
}

output "receiver_ip" {
  value = aws_spot_instance_request.receiver.public_ip
}
