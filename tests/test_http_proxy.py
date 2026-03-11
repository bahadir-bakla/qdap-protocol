# tests/test_http_proxy.py

import pytest
from qdap.proxy.priority_mapper import content_type_to_priority
from qdap.proxy.http_bridge import (
    http_to_qdap_payload,
    qdap_payload_to_http,
    build_response_payload,
    parse_response_payload,
)


class TestPriorityMapper:

    def test_audio_high_priority(self):
        p, d = content_type_to_priority("audio/mpeg", {})
        assert p >= 900
        assert d <= 100.0

    def test_text_low_priority(self):
        p, d = content_type_to_priority("text/html", {})
        assert p <= 200
        assert d >= 500.0

    def test_x_qdap_priority_override(self):
        p, d = content_type_to_priority(
            "text/html",
            {"X-QDAP-Priority": "999", "X-QDAP-Deadline-Ms": "5.0"}
        )
        assert p == 999
        assert d == 5.0

    def test_emergency_content_type(self):
        p, _ = content_type_to_priority("application/x-emergency", {})
        assert p >= 950


class TestHTTPBridge:

    def test_roundtrip_get(self):
        payload, priority, deadline = http_to_qdap_payload(
            method="GET", path="/api/test",
            headers={"Content-Type": "application/json"},
            body=b"",
        )
        method, path, headers, body = qdap_payload_to_http(payload)
        assert method == "GET"
        assert path   == "/api/test"
        assert body   == b""

    def test_roundtrip_post_with_body(self):
        original_body = b'{"key": "value"}'
        payload, _, _ = http_to_qdap_payload(
            method="POST", path="/api/data",
            headers={"Content-Type": "application/json"},
            body=original_body,
        )
        _, _, _, body = qdap_payload_to_http(payload)
        assert body == original_body

    def test_qdap_headers_stripped(self):
        payload, _, _ = http_to_qdap_payload(
            method="GET", path="/",
            headers={
                "Content-Type": "text/html",
                "X-QDAP-Priority": "999",
            },
            body=b"",
        )
        _, _, headers, _ = qdap_payload_to_http(payload)
        assert "X-QDAP-Priority" not in headers

    def test_response_roundtrip(self):
        original = b"Hello World"
        payload  = build_response_payload(200, {"X-Test": "1"}, original)
        status, headers, body = parse_response_payload(payload)
        assert status         == 200
        assert headers["X-Test"] == "1"
        assert body           == original

    def test_binary_body(self):
        import os
        binary = os.urandom(1024)
        payload, _, _ = http_to_qdap_payload(
            "POST", "/upload",
            {"Content-Type": "application/octet-stream"},
            binary,
        )
        _, _, _, body = qdap_payload_to_http(payload)
        assert body == binary
