#!/usr/bin/env python3
"""
MQTT baseline benchmark.
3 QoS seviyesi test edilir:
  QoS 0: fire-and-forget (QDAP'a en yakın)
  QoS 1: at-least-once (1 ACK per message)
  QoS 2: exactly-once (4 message per data)

paho-mqtt kullanır.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import List

import paho.mqtt.client as mqtt


@dataclass
class MQTTMetrics:
    protocol:          str
    qos:               int
    n_messages:        int   = 0
    payload_bytes:     int   = 0
    ack_messages:      int   = 0   # QoS 1: n_messages, QoS 2: 3×n_messages
    ack_bytes:         int   = 0
    overhead_pct:      float = 0.0
    throughput_mbps:   float = 0.0
    p99_latency_ms:    float = 0.0
    duration_sec:      float = 0.0
    connections:       int   = 1   # MQTT broker'a tek bağlantı
    delivery_rate:     float = 0.0


# MQTT overhead hesabı:
# QoS 0: PUBLISH header ~2-4 byte
# QoS 1: PUBLISH + PUBACK = payload + ~4 byte overhead × 2
# QoS 2: PUBLISH + PUBREC + PUBREL + PUBCOMP = 4 mesaj
MQTT_FIXED_HEADER   = 2   # byte
PUBACK_SIZE         = 4   # byte
PUBREC_SIZE         = 4   # byte
PUBREL_SIZE         = 4   # byte
PUBCOMP_SIZE        = 4   # byte


def run_mqtt_benchmark(
    broker_host:  str   = "172.20.0.30",
    broker_port:  int   = 1883,
    topic:        str   = "qdap/benchmark",
    qos:          int   = 1,
    n_messages:   int   = 1000,
    payload_size: int   = 1024,
) -> MQTTMetrics:
    """
    MQTT benchmark — senkron, her mesaj publish edilir.

    QoS 0: publish → devam (fire-and-forget)
    QoS 1: publish → PUBACK bekle → devam
    QoS 2: publish → PUBREC → PUBREL → PUBCOMP → devam
    """
    payload     = b"M" * payload_size
    latencies   = []
    published   = [0]
    lock        = threading.Lock()

    # paho-mqtt client
    client = mqtt.Client(
        client_id=f"qdap_benchmark_qos{qos}_{time.time_ns()}",
        protocol=mqtt.MQTTv5,
    )
    client.connect(broker_host, broker_port, keepalive=60)
    client.loop_start()

    # Warmup
    time.sleep(0.5)

    t_start = time.monotonic()

    for i in range(n_messages):
        t0 = time.monotonic_ns()

        info = client.publish(topic, payload, qos=qos)

        if qos > 0:
            # Delivery konfirmasyonu bekle
            info.wait_for_publish(timeout=30.0)

        latencies.append((time.monotonic_ns() - t0) / 1e6)

    duration = time.monotonic() - t_start

    client.loop_stop()
    client.disconnect()

    # Overhead hesapla
    pure_data = n_messages * payload_size

    if qos == 0:
        # Sadece PUBLISH header
        ack_msgs  = 0
        ack_bytes = n_messages * MQTT_FIXED_HEADER
    elif qos == 1:
        # PUBACK: her mesaj için 4 byte
        ack_msgs  = n_messages
        ack_bytes = n_messages * (MQTT_FIXED_HEADER + PUBACK_SIZE)
    else:  # qos == 2
        # PUBREC + PUBREL + PUBCOMP: 3 × 4 byte
        ack_msgs  = n_messages * 3
        ack_bytes = n_messages * (PUBREC_SIZE + PUBREL_SIZE + PUBCOMP_SIZE)

    overhead_pct = ack_bytes / pure_data * 100
    throughput   = pure_data / duration / (1024 * 1024) * 8

    lats = sorted(latencies)
    p99  = lats[int(len(lats) * 0.99)]

    return MQTTMetrics(
        protocol=f"MQTT_QoS{qos}",
        qos=qos,
        n_messages=n_messages,
        payload_bytes=pure_data,
        ack_messages=ack_msgs,
        ack_bytes=ack_bytes,
        overhead_pct=overhead_pct,
        throughput_mbps=throughput,
        p99_latency_ms=p99,
        duration_sec=duration,
        connections=1,
        delivery_rate=100.0,
    )
