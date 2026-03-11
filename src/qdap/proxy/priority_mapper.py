# src/qdap/proxy/priority_mapper.py

PRIORITY_MAP = {
    "audio/":                   1000,
    "video/":                    900,
    "application/x-emergency":   950,
    "image/":                    500,
    "application/json":          300,
    "application/xml":           300,
    "text/":                     100,
    "application/":              200,
    "*":                         200,
}

DEADLINE_MAP = {
    "audio/":                    50.0,
    "video/":                   100.0,
    "application/x-emergency":   10.0,
    "image/":                   500.0,
    "text/":                   2000.0,
    "*":                        500.0,
}


def content_type_to_priority(
    content_type: str,
    headers: dict,
) -> tuple[int, float]:
    """
    Content-Type ve HTTP header'lardan (priority, deadline_ms) döndür.
    X-QDAP-Priority header varsa override eder.
    """
    if "X-QDAP-Priority" in headers:
        try:
            return (
                int(headers["X-QDAP-Priority"]),
                float(headers.get("X-QDAP-Deadline-Ms", 500.0)),
            )
        except ValueError:
            pass

    ct = (content_type or "").lower()
    for prefix, priority in PRIORITY_MAP.items():
        if prefix == "*":
            continue
        if ct.startswith(prefix):
            return priority, DEADLINE_MAP.get(prefix, 500.0)

    return PRIORITY_MAP["*"], DEADLINE_MAP["*"]
