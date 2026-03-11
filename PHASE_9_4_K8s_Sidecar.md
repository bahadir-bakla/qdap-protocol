# PHASE 9.4 — Kubernetes Sidecar (Auto-Inject)
## Gemini Agent İçin: Tam Kod, Sıfır Varsayım
## Tahmini Süre: 2-3 hafta | Zorluk: Yüksek
## Ön Koşul: Phase 9.1 (HTTP Proxy) tamamlanmış olmalı

---

## 1. Amaç

Kubernetes pod'larına annotation ile otomatik QDAP sidecar enjekte et.
`qdap.io/inject: "true"` annotation'ı olan her pod'a sidecar container eklenir.
Uygulama kodu **sıfır değişiklik** — tüm trafik otomatik QDAP üzerinden akar.

---

## 2. Mimari

```
Pod (qdap.io/inject: "true")
├── app container          → localhost:8080 (HTTP)
└── qdap-sidecar container → intercept + QDAP transport
    ├── Port 15001: inbound  (iptables redirect → sidecar)
    └── Port 15000: outbound (iptables redirect → sidecar)

MutatingAdmissionWebhook
└── k8s/webhook/main.py    → her yeni pod'u intercept et, sidecar ekle
```

---

## 3. Dizin Yapısı

```
Dockerfile.sidecar
k8s/
├── webhook/
│   ├── main.py            # MutatingAdmissionWebhook server
│   ├── Dockerfile
│   └── certs/             # TLS (webhook HTTPS zorunlu)
│       ├── gen_certs.sh
│       ├── server.crt
│       └── server.key
├── manifests/
│   ├── webhook-deployment.yaml
│   ├── webhook-service.yaml
│   ├── mutating-webhook-config.yaml
│   └── rbac.yaml
chart/
└── qdap/
    ├── Chart.yaml
    ├── values.yaml
    └── templates/
        ├── deployment.yaml
        ├── service.yaml
        ├── webhook.yaml
        └── _helpers.tpl
```

---

## 4. Dockerfile.sidecar

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY src/ src/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# iptables için
RUN apt-get update && apt-get install -y iptables && rm -rf /var/lib/apt/lists/*

COPY k8s/sidecar_entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 15000 15001
ENTRYPOINT ["/entrypoint.sh"]
```

---

## 5. k8s/sidecar_entrypoint.sh

```bash
#!/bin/bash
set -e

# Inbound trafik → 15001'e yönlendir (app dışındaki tüm portlar)
iptables -t nat -A PREROUTING -p tcp --dport 8080 -j REDIRECT --to-port 15001 || true

# Outbound trafik → 15000'e yönlendir
iptables -t nat -A OUTPUT -p tcp ! -d 127.0.0.1 -j REDIRECT --to-port 15000 || true

# QDAP proxy başlat
exec python -m src.qdap.proxy.proxy_server \
    --listen-port 15001 \
    --outbound-port 15000 \
    --mode sidecar
```

---

## 6. k8s/webhook/main.py — MutatingAdmissionWebhook

```python
# k8s/webhook/main.py
"""
MutatingAdmissionWebhook: pod'lara qdap.io/inject=true annotation'ı varsa
sidecar container + init container (iptables) ekle.
"""
import base64, json, logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import ssl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("qdap-webhook")

SIDECAR_IMAGE = "qdap/sidecar:latest"

SIDECAR_CONTAINER = {
    "name": "qdap-sidecar",
    "image": SIDECAR_IMAGE,
    "imagePullPolicy": "IfNotPresent",
    "securityContext": {
        "capabilities": {"add": ["NET_ADMIN"]},
        "runAsUser": 1337
    },
    "ports": [
        {"containerPort": 15000, "name": "outbound"},
        {"containerPort": 15001, "name": "inbound"}
    ],
    "resources": {
        "requests": {"cpu": "10m", "memory": "32Mi"},
        "limits":   {"cpu": "100m", "memory": "128Mi"}
    },
    "env": [
        {"name": "QDAP_MODE", "value": "sidecar"},
        {"name": "POD_NAME",
         "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
        {"name": "POD_NAMESPACE",
         "valueFrom": {"fieldRef": {"fieldPath": "metadata.namespace"}}}
    ]
}

INIT_CONTAINER = {
    "name": "qdap-init",
    "image": SIDECAR_IMAGE,
    "command": ["/entrypoint.sh", "--init-only"],
    "securityContext": {
        "capabilities": {"add": ["NET_ADMIN"]},
        "runAsNonRoot": False,
        "runAsUser": 0
    }
}

def should_inject(pod: dict) -> bool:
    annotations = pod.get("metadata", {}).get("annotations", {})
    return annotations.get("qdap.io/inject", "false").lower() == "true"

def already_injected(pod: dict) -> bool:
    containers = pod.get("spec", {}).get("containers", [])
    return any(c["name"] == "qdap-sidecar" for c in containers)

def build_patch(pod: dict) -> list:
    patch = []
    containers = pod.get("spec", {}).get("containers", [])
    init_containers = pod.get("spec", {}).get("initContainers", [])

    # Add sidecar
    if not containers:
        patch.append({"op": "add", "path": "/spec/containers", "value": []})
    patch.append({
        "op": "add",
        "path": "/spec/containers/-",
        "value": SIDECAR_CONTAINER
    })

    # Add init container
    if not init_containers:
        patch.append({"op": "add", "path": "/spec/initContainers", "value": []})
    patch.append({
        "op": "add",
        "path": "/spec/initContainers/-",
        "value": INIT_CONTAINER
    })

    # Add injected annotation
    patch.append({
        "op": "add",
        "path": "/metadata/annotations/qdap.io~1injected",
        "value": "true"
    })

    return patch

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        admission_review = json.loads(body)

        request = admission_review.get("request", {})
        uid = request.get("uid", "")
        pod = request.get("object", {})

        if should_inject(pod) and not already_injected(pod):
            patch = build_patch(pod)
            patch_b64 = base64.b64encode(
                json.dumps(patch).encode()).decode()
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": uid,
                    "allowed": True,
                    "patchType": "JSONPatch",
                    "patch": patch_b64
                }
            }
            logger.info(f"Injecting QDAP sidecar into pod "
                        f"{pod.get('metadata', {}).get('name', 'unknown')}")
        else:
            response = {
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {"uid": uid, "allowed": True}
            }

        resp_body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def log_message(self, format, *args):
        logger.info(f"HTTP {format % args}")

def main():
    server = HTTPServer(("0.0.0.0", 8443), WebhookHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("k8s/webhook/certs/server.crt",
                        "k8s/webhook/certs/server.key")
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    logger.info("QDAP Webhook server listening on :8443")
    server.serve_forever()

if __name__ == "__main__":
    main()
```

---

## 7. k8s/certs/gen_certs.sh

```bash
#!/bin/bash
# Self-signed cert for development. Production: cert-manager kullan.
NAMESPACE=${1:-qdap-system}
SERVICE="qdap-webhook"

openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout server.key -out server.crt \
  -subj "/CN=${SERVICE}.${NAMESPACE}.svc" \
  -addext "subjectAltName=DNS:${SERVICE}.${NAMESPACE}.svc,DNS:${SERVICE}.${NAMESPACE}.svc.cluster.local"

# Base64 for webhook config
CA_BUNDLE=$(cat server.crt | base64 | tr -d '\n')
echo "CA_BUNDLE: ${CA_BUNDLE}"
echo "Paste this into mutating-webhook-config.yaml caBundle field"
```

---

## 8. k8s/manifests/mutating-webhook-config.yaml

```yaml
apiVersion: admissionregistration.k8s.io/v1
kind: MutatingWebhookConfiguration
metadata:
  name: qdap-sidecar-injector
webhooks:
  - name: sidecar.qdap.io
    admissionReviewVersions: ["v1"]
    sideEffects: None
    failurePolicy: Ignore  # Don't block pods if webhook is down
    rules:
      - operations: ["CREATE"]
        apiGroups: [""]
        apiVersions: ["v1"]
        resources: ["pods"]
    namespaceSelector:
      matchLabels:
        qdap-injection: enabled
    objectSelector:
      matchExpressions:
        - key: qdap.io/inject
          operator: In
          values: ["true"]
    clientConfig:
      service:
        name: qdap-webhook
        namespace: qdap-system
        path: /mutate
        port: 443
      caBundle: "<BASE64_CA_CERT>"  # gen_certs.sh çıktısını buraya yapıştır
```

---

## 9. k8s/manifests/webhook-deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qdap-webhook
  namespace: qdap-system
spec:
  replicas: 2
  selector:
    matchLabels:
      app: qdap-webhook
  template:
    metadata:
      labels:
        app: qdap-webhook
    spec:
      serviceAccountName: qdap-webhook
      containers:
        - name: webhook
          image: qdap/webhook:latest
          ports:
            - containerPort: 8443
          volumeMounts:
            - name: certs
              mountPath: /app/k8s/webhook/certs
              readOnly: true
          resources:
            requests:
              cpu: "50m"
              memory: "64Mi"
            limits:
              cpu: "200m"
              memory: "256Mi"
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8443
              scheme: HTTPS
            initialDelaySeconds: 10
      volumes:
        - name: certs
          secret:
            secretName: qdap-webhook-certs
---
apiVersion: v1
kind: Service
metadata:
  name: qdap-webhook
  namespace: qdap-system
spec:
  selector:
    app: qdap-webhook
  ports:
    - port: 443
      targetPort: 8443
```

---

## 10. chart/qdap/Chart.yaml

```yaml
apiVersion: v2
name: qdap
description: QDAP Quantum-Inspired Adaptive Protocol - Kubernetes Integration
type: application
version: 0.1.0
appVersion: "1.0.0"
keywords:
  - networking
  - mqtt
  - quantum-inspired
  - sidecar
maintainers:
  - name: Bahadir Selim Bakla
    email: bahadirselimbakla@icloud.com
```

---

## 11. chart/qdap/values.yaml

```yaml
webhook:
  enabled: true
  image:
    repository: qdap/webhook
    tag: latest
    pullPolicy: IfNotPresent
  replicaCount: 2
  resources:
    requests:
      cpu: 50m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 256Mi

sidecar:
  image:
    repository: qdap/sidecar
    tag: latest
  resources:
    requests:
      cpu: 10m
      memory: 32Mi
    limits:
      cpu: 100m
      memory: 128Mi

broker:
  enabled: false
  port: 1883

namespace: qdap-system
```

---

## 12. Kurulum & Test

```bash
# 1. Namespace oluştur
kubectl create namespace qdap-system

# 2. Cert oluştur
cd k8s/webhook/certs
bash gen_certs.sh qdap-system
kubectl create secret tls qdap-webhook-certs \
  --cert=server.crt --key=server.key \
  -n qdap-system

# 3. Webhook deploy et
kubectl apply -f k8s/manifests/

# 4. Test namespace'ini etiketle
kubectl label namespace default qdap-injection=enabled

# 5. Test pod'u başlat (annotation ile)
kubectl run test-app --image=nginx \
  --annotations="qdap.io/inject=true"

# 6. Sidecar enjekte edildi mi kontrol et
kubectl get pod test-app -o jsonpath='{.spec.containers[*].name}'
# Çıktı: nginx qdap-sidecar

# 7. Helm ile deploy (alternatif)
helm install qdap chart/qdap/ \
  --namespace qdap-system \
  --set webhook.enabled=true

# 8. Helm test
helm test qdap -n qdap-system
```

---

## 13. tests/test_webhook.py

```python
# tests/test_webhook.py
import json, base64, pytest
from k8s.webhook.main import should_inject, already_injected, build_patch

def make_pod(annotations=None, containers=None):
    return {
        "metadata": {"name": "test-pod", "annotations": annotations or {}},
        "spec": {"containers": containers or [{"name": "app", "image": "nginx"}]}
    }

def test_should_inject_true():
    pod = make_pod(annotations={"qdap.io/inject": "true"})
    assert should_inject(pod) is True

def test_should_inject_false():
    pod = make_pod()
    assert should_inject(pod) is False

def test_already_injected_false():
    pod = make_pod()
    assert already_injected(pod) is False

def test_already_injected_true():
    pod = make_pod(containers=[
        {"name": "app", "image": "nginx"},
        {"name": "qdap-sidecar", "image": "qdap/sidecar:latest"}
    ])
    assert already_injected(pod) is True

def test_build_patch_contains_sidecar():
    pod = make_pod()
    patch = build_patch(pod)
    names = [op.get("value", {}).get("name") for op in patch
             if op.get("op") == "add" and "containers" in op.get("path", "")]
    assert "qdap-sidecar" in names

def test_build_patch_contains_init():
    pod = make_pod()
    patch = build_patch(pod)
    names = [op.get("value", {}).get("name") for op in patch
             if op.get("op") == "add" and "initContainers" in op.get("path", "")]
    assert "qdap-init" in names

def test_build_patch_adds_annotation():
    pod = make_pod()
    patch = build_patch(pod)
    annotation_ops = [op for op in patch
                      if "injected" in op.get("path", "")]
    assert len(annotation_ops) == 1
    assert annotation_ops[0]["value"] == "true"

def test_build_patch_sidecar_has_net_admin():
    pod = make_pod()
    patch = build_patch(pod)
    for op in patch:
        val = op.get("value", {})
        if val.get("name") == "qdap-sidecar":
            caps = val["securityContext"]["capabilities"]["add"]
            assert "NET_ADMIN" in caps
```

---

## 14. Başarı Kriterleri

| Metrik | Hedef |
|--------|-------|
| Webhook testler | 8/8 geçmeli |
| Sidecar enjeksiyon | annotation ile otomatik |
| Nginx pod | sidecar ile başlamalı |
| HTTP trafik | sidecar üzerinden geçmeli |
| failurePolicy: Ignore | webhook down → pod yine de başlar |
| Helm install | hatasız tamamlanmalı |
| Helm test | pass |

---

## 15. Paper Entegrasyonu

> "The QDAP Kubernetes sidecar (Section V-E) enables zero-code-change deployment in container orchestration environments. A MutatingAdmissionWebhook automatically injects the QDAP proxy container into annotated pods, transparently routing all inter-service communication through QDAP priority channels."

---

## 16. Sonraki Adım

Phase 9.4 tamamlandıktan sonra → **Phase 10.1 (SIGCOMM Paper)** başlatılır.
