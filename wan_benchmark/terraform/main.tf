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


# ─── EC2 Instances (on-demand t3.micro — free tier compatible) ─────────────

resource "aws_instance" "sender" {
  provider               = aws.ireland
  ami                    = "ami-0f9ae27ecf629cbe3"   # Ubuntu 22.04 eu-west-1
  instance_type          = "t3.micro"
  key_name               = aws_key_pair.qdap_ireland.key_name
  vpc_security_group_ids = [aws_security_group.qdap_sg_ireland.id]

  user_data = <<-EOF
    #!/bin/bash
    apt-get update -y
    apt-get install -y python3 python3-pip python3-venv
  EOF

  tags = { Name = "qdap-wan-sender" }
}

resource "aws_instance" "receiver" {
  provider               = aws.singapore
  ami                    = "ami-0434196a03595d088"   # Ubuntu 22.04 ap-southeast-1
  instance_type          = "t3.micro"
  key_name               = aws_key_pair.qdap_singapore.key_name
  vpc_security_group_ids = [aws_security_group.qdap_sg_singapore.id]

  user_data = <<-EOF
    #!/bin/bash
    apt-get update -y
    apt-get install -y python3 python3-pip python3-venv
  EOF

  tags = { Name = "qdap-wan-receiver" }
}


# ─── Outputs ────────────────────────────────────────────────────────────────

output "sender_ip" {
  value = aws_instance.sender.public_ip
}

output "receiver_ip" {
  value = aws_instance.receiver.public_ip
}
