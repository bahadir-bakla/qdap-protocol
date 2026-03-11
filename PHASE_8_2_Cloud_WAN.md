# PHASE 8.2 — Gerçek WAN Testi: İki Cloud Instance
## AWS EC2 ile İnternet-Scale Doğrulama
## Tahmini Süre: 1-2 hafta | Zorluk: Kolay | Maliyet: ~$5-10

---

## Hedef

Paper'daki en büyük limitation'ı kapat:
```
Mevcut durum:
  ✅ LAN (WiFi, aynı AP) — 28× doğrulandı
  ❌ Internet-scale WAN — eksik

Reviewer sorusu (kesin gelecek):
  "Have you tested over a real WAN link,
   not just simulated delay?"

Bu phase sonrası:
  ✅ AWS eu-west-1 (İrlanda) ↔ ap-southeast-1 (Singapur)
  ✅ RTT: ~160-200ms (gerçek internet gecikme)
  ✅ Paper'da "cloud WAN" bölümü eklenmiş
```

---

## Mimari

```
┌─────────────────┐      ~180ms RTT       ┌─────────────────┐
│  EC2 eu-west-1  │ ←─── İNTERNET ───→    │ EC2 ap-south-1  │
│   (İrlanda)     │                        │   (Singapur)    │
│                 │                        │                 │
│  wan_sender.py  │     TCP trafiği        │ wan_receiver.py │
│  Classical TCP  │ ──────────────────────→│                 │
│  QDAP Ghost     │                        │                 │
│  QDAP Secure    │                        │                 │
└─────────────────┘                        └─────────────────┘
```

---

## ÖN KOŞULLAR

1. AWS hesabı (free tier yeterli değil — spot instance lazım)
2. AWS CLI kurulu: `aws --version`
3. SSH key pair oluşturulmuş

---

## ADIM 1 — AWS CLI Kur ve Yapılandır

```bash
# macOS
brew install awscli

# AWS credentials
aws configure
# AWS Access Key ID: <IAM'dan al>
# AWS Secret Access Key: <IAM'dan al>
# Default region: eu-west-1
# Default output: json

# Test et
aws sts get-caller-identity
```

**IAM Kullanıcısı oluşturma (AWS Console):**
```
IAM → Users → Create user
  Username: qdap-wan-test
  Permissions: AmazonEC2FullAccess
  → Create access key → Download CSV
```

---

## ADIM 2 — Terraform ile Instance'ları Kur

```bash
# Terraform yükle
brew install terraform

# Proje kalsörü oluştur
mkdir -p wan_benchmark/terraform
cd wan_benchmark/terraform
```

```hcl
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
  public_key = file("~/.ssh/id_rsa.pub")  # mevcut SSH public key
}

resource "aws_key_pair" "qdap_singapore" {
  provider   = aws.singapore
  key_name   = "qdap-wan-key"
  public_key = file("~/.ssh/id_rsa.pub")
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

data "aws_ami" "ubuntu_ireland" {
  provider    = aws.ireland
  most_recent = true
  owners      = ["099720109477"]  # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

data "aws_ami" "ubuntu_singapore" {
  provider    = aws.singapore
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

resource "aws_spot_instance_request" "sender" {
  provider             = aws.ireland
  ami                  = data.aws_ami.ubuntu_ireland.id
  instance_type        = "t3.medium"   # 2 vCPU, 4GB RAM
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
    git clone ${var.repo_url} quantum-protocol
    chown -R ubuntu:ubuntu quantum-protocol
  EOF

  tags = { Name = "qdap-wan-sender" }
}

resource "aws_spot_instance_request" "receiver" {
  provider             = aws.singapore
  ami                  = data.aws_ami.ubuntu_singapore.id
  instance_type        = "t3.medium"
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
    git clone ${var.repo_url} quantum-protocol
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
```

```hcl
# wan_benchmark/terraform/variables.tf

variable "repo_url" {
  description = "QDAP GitHub repo URL"
  default     = "https://github.com/<username>/quantum-protocol.git"
}
```

---

## ADIM 3 — Deploy Script

```bash
#!/bin/bash
# wan_benchmark/scripts/deploy_aws.sh

set -e

REPO_URL="${1:-https://github.com/<username>/quantum-protocol.git}"
RESULT_DIR="wan_benchmark/results"

echo "=== QDAP Cloud WAN Deploy ==="
echo "Repo: $REPO_URL"
echo ""

# Terraform apply
cd wan_benchmark/terraform
terraform init
terraform apply -var="repo_url=$REPO_URL" -auto-approve

# IP'leri al
SENDER_IP=$(terraform output -raw sender_ip)
RECEIVER_IP=$(terraform output -raw receiver_ip)

echo ""
echo "✅ Instances ready:"
echo "  Sender (Ireland):    $SENDER_IP"
echo "  Receiver (Singapore): $RECEIVER_IP"

# SSH hazır olana kadar bekle
echo ""
echo "⏳ SSH hazır olana kadar bekleniyor..."
for i in {1..30}; do
  ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
      ubuntu@$SENDER_IP "echo OK" 2>/dev/null && break
  sleep 10
done

# RTT ölç
echo ""
echo "📡 RTT ölçülüyor..."
RTT=$(ping -c 10 $RECEIVER_IP | tail -1 | awk '{print $4}' | cut -d'/' -f2)
echo "  Ölçülen RTT: ${RTT}ms"

# Receiver'ı başlat
echo ""
echo "🚀 Receiver başlatılıyor (Singapur)..."
ssh -o StrictHostKeyChecking=no ubuntu@$RECEIVER_IP \
    "cd quantum-protocol && nohup python3 wan_benchmark/wan_receiver.py > /tmp/receiver.log 2>&1 &"

sleep 3

# Sender'ı çalıştır
echo ""
echo "🚀 Benchmark başlatılıyor (İrlanda → Singapur)..."
ssh -o StrictHostKeyChecking=no ubuntu@$SENDER_IP \
    "cd quantum-protocol && python3 wan_benchmark/wan_sender_wan.py \
     --host $RECEIVER_IP --rtt $RTT"

# Sonuçları çek
echo ""
echo "📥 Sonuçlar indiriliyor..."
mkdir -p $RESULT_DIR
scp -o StrictHostKeyChecking=no \
    ubuntu@$SENDER_IP:quantum-protocol/wan_benchmark/results/wan_benchmark.json \
    $RESULT_DIR/cloud_wan_benchmark.json

echo ""
echo "✅ cloud_wan_benchmark.json kaydedildi"
echo ""
cat $RESULT_DIR/cloud_wan_benchmark.json | python3 -m json.tool | head -50
```

---

## ADIM 4 — Manuel Alternatif (Terraform Yoksa)

```bash
# SENDER kur (İrlanda) — AWS Console'dan:
# EC2 → Launch Instance
#   AMI: Ubuntu 22.04
#   Type: t3.medium
#   Key pair: mevcut
#   Security group: port 22, 19600-19603 açık
#   User data: (yukarıdaki script)
# → Launch

# RECEIVER kur (Singapur) — aynı şekilde, region değiştir
# EC2 Console → Region: ap-southeast-1

# Her ikisinin IP'sini al

# Local'den sender'a SSH:
ssh ubuntu@<ireland_ip>
cd quantum-protocol
pip3 install -r requirements.txt

# Receiver'ı Singapur'da başlat:
ssh ubuntu@<singapore_ip>
cd quantum-protocol
pip3 install -r requirements.txt
python3 wan_benchmark/wan_receiver.py &

# Ireland'dan çalıştır:
ssh ubuntu@<ireland_ip>
ping <singapore_ip>  # RTT ölç
python3 wan_benchmark/wan_sender_wan.py \
    --host <singapore_ip> \
    --rtt <ölçülen_ms>

# Sonuçları indir:
scp ubuntu@<ireland_ip>:quantum-protocol/wan_benchmark/results/wan_benchmark.json .
```

---

## ADIM 5 — Temizlik (Para Ödememeye Dikkat!)

```bash
# Benchmark biter bitmez instance'ları KAPAT
cd wan_benchmark/terraform
terraform destroy -auto-approve

# Veya AWS Console'dan:
# EC2 → Instances → Select All → Actions → Terminate

# Kontrol et (0 instance kalmalı):
aws ec2 describe-instances --region eu-west-1 \
    --query 'Reservations[].Instances[].State.Name'
aws ec2 describe-instances --region ap-southeast-1 \
    --query 'Reservations[].Instances[].State.Name'
```

---

## Beklenen Sonuçlar

```
cloud_wan_benchmark.json içinde:

metadata:
  test_type:        "WAN — AWS eu-west-1 ↔ ap-southeast-1"
  sender:           "EC2 eu-west-1 (Ireland)"
  receiver:         "EC2 ap-southeast-1 (Singapore)"
  measured_rtt_ms:  ~160-200

results:
  1KB:
    Classical:  ~0.003 Mbps  (RTT 180ms → 1000/180 = 5.5 msg/s × 1KB = 0.044Mbs)
    Ghost:      ~5.5 Mbps    (ACK yok, pipeline dolu)
    ratio:      ~100-150×

  64KB:
    Classical:  ~2.8 Mbps
    Ghost:      ~8.5 Mbps
    ratio:      ~3×

  1MB:
    Classical:  ~8 Mbps
    Ghost:      ~9 Mbps
    ratio:      ~1.1×
```

**Why it works:** Classical'da her 1KB mesaj için 180ms RTT beklendiğinden throughput = 1KB / 180ms = ~44KB/s. Ghost'ta ACK olmadığından pipeline dolu, throughput sadece bant genişliğiyle sınırlı.

---

## Paper'a Ekleme (PHASE 8.2 Tamamlanınca)

Section 5.4 — Cloud WAN Validation (yeni):
```latex
\subsection{Cloud WAN Validation}

We validate QDAP over a geographically distributed WAN link
between AWS \texttt{eu-west-1} (Ireland) and
\texttt{ap-southeast-1} (Singapore), with measured RTT of
$\approx$180\,ms.

[Tablo: Cloud WAN Benchmark Results]

Ghost Session achieves $X\times$ throughput at 1\,KB,
consistent with the simulated results and confirming
that ACK elimination is the dominant factor
at high-latency paths.
```

---

## Maliyet Hesabı

```
t3.medium spot: ~$0.015/saat × 2 instance × 2 saat = $0.06
Veri transferi: ~100MB × $0.09/GB = $0.009
Network:        minimal

Toplam: ~$0.10 (on sent)

Terraform ile otomatik terminate → para kaybı yok
```

---

## DOKUNMA

```
Bu phase'de şunlar OLUŞTURULUR (hepsi yeni):
  wan_benchmark/terraform/main.tf       (yeni)
  wan_benchmark/terraform/variables.tf  (yeni)
  wan_benchmark/scripts/deploy_aws.sh   (yeni)
  wan_benchmark/results/cloud_wan_benchmark.json  (benchmark çıktısı)

Mevcut hiçbir şeye DOKUNMA:
  src/qdap/ → değişmez
  docker_benchmark/ → değişmez
  tests/ → değişmez
  QDAP_Paper_v3.tex → sadece yeni bölüm eklenir
```
