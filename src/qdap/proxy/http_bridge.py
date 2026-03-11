# src/qdap/proxy/http_bridge.py

import base64
import json
from .priority_mapper import content_type_to_priority


def http_to_qdap_payload(
    method:  str,
    path:    str,
    headers: dict,
    body:    bytes,
) -> tuple[bytes, int, float]:
    """
    HTTP request → (QDAP payload bytes, priority, deadline_ms)

    Payload formatı (JSON envelope):
    {
        "method":   "POST",
        "path":     "/api/data",
        "headers":  {...},
        "body_b64": "<base64>"
    }
    """
    content_type          = headers.get("Content-Type", "")
    priority, deadline_ms = content_type_to_priority(content_type, headers)

    envelope = {
        "method":   method,
        "path":     path,
        "headers":  {k: v for k, v in headers.items()
                     if not k.startswith("X-QDAP-")},
        "body_b64": base64.b64encode(body).decode(),
    }

    payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    return payload, priority, deadline_ms


def qdap_payload_to_http(payload: bytes) -> tuple[str, str, dict, bytes]:
    """QDAP payload → (method, path, headers, body)"""
    envelope = json.loads(payload.decode("utf-8"))
    return (
        envelope["method"],
        envelope["path"],
        envelope["headers"],
        base64.b64decode(envelope["body_b64"]),
    )


def build_response_payload(
    status:  int,
    headers: dict,
    body:    bytes,
) -> bytes:
    """HTTP response → QDAP payload"""
    return json.dumps({
        "status":   status,
        "headers":  headers,
        "body_b64": base64.b64encode(body).decode(),
    }).encode("utf-8")


def parse_response_payload(
    payload: bytes,
) -> tuple[int, dict, bytes]:
    """QDAP response payload → (status, headers, body)"""
    try:
        data = json.loads(payload.decode("utf-8"))
        return (
            data.get("status", 200),
            data.get("headers", {}),
            base64.b64decode(data.get("body_b64", "")),
        )
    except Exception:
        return 200, {}, payload
