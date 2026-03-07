import json, sys

def check_quic(path="docker_benchmark/results/quic_benchmark.json"):
    with open(path) as f: data = json.load(f)
    errors = []
    for r in data["results"]:
        if r.get("qdap_ack_bytes", -1) != 0:
            errors.append(f"❌ QUIC {r['label']}: ack_bytes != 0")
    if "QUIC" not in data["metadata"].get("transport", "") and "UDP" not in data["metadata"].get("transport", ""):
        errors.append("❌ QUIC metadata transport alanı yanlış")
    return errors

def check_iot(path="docker_benchmark/results/iot_benchmark.json"):
    with open(path) as f: data = json.load(f)
    errors = []
    for r in data["results"]:
        if r.get("qdap_ack_bytes", -1) != 0:
            errors.append(f"❌ IoT run {r['run']}: ack_bytes != 0")
        if r.get("qdap_connections", 999) != 1:
            errors.append(f"❌ IoT run {r['run']}: QDAP connections != 1")
        if r.get("qdap_emergency_deadline_pct", 0) < 80:
            errors.append(
                f"⚠️  IoT run {r['run']}: emergency deadline "
                f"%{r['qdap_emergency_deadline_pct']:.1f} < 80%"
            )
    return errors

def check_keepalive(path="docker_benchmark/results/keepalive_benchmark.json"):
    with open(path) as f: data = json.load(f)
    errors = []
    ghost_oh = data["ghost_session"]["overhead_bytes"]
    tcp_oh   = data["tcp_keepalive"]["overhead_bytes"]
    if ghost_oh > tcp_oh:
        errors.append(
            f"❌ Ghost Session overhead ({ghost_oh}B) > "
            f"TCP keepalive ({tcp_oh}B)!"
        )
    if tcp_oh == 0:
        errors.append("❌ TCP keepalive overhead = 0, ölçüm çalışmadı")
    return errors

all_errors = []
all_errors += check_quic()
all_errors += check_iot()
all_errors += check_keepalive()

for e in all_errors: print(e)
if not all_errors:
    print("✅ Tüm yeni benchmark'lar temiz")
else:
    sys.exit(1)
