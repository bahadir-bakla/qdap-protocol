#!/usr/bin/env python3
"""
IoT senaryosu: 100 sensör, karışık mesaj akışı
MQTT: her sensör ayrı bağlantı (gerçek MQTT pattern)
QDAP: tek bağlantı, AmplitudeEncoder priority

Karşılaştırılan:
  1. Bağlantı sayısı
  2. Emergency deadline hit %
  3. ACK overhead
"""

import asyncio
import random
import time
import threading
from dataclasses import dataclass
from typing import List

import paho.mqtt.client as mqtt


@dataclass
class IoTComparisonResult:
    # MQTT
    mqtt_connections:          int
    mqtt_emergency_hit_pct:    float
    mqtt_ack_bytes:            int
    mqtt_throughput_msg_s:     float
    mqtt_deadline_miss_pct:    float
    # QDAP (mevcut iot_benchmark.json'dan al)
    qdap_connections:          int   = 1
    qdap_emergency_hit_pct:    float = 100.0
    qdap_ack_bytes:            int   = 0
    qdap_throughput_msg_s:     float = 0.0
    qdap_deadline_miss_pct:    float = 0.0


def run_mqtt_iot_benchmark(
    broker_host:   str = "172.20.0.30",
    n_sensors:     int = 100,
    n_emergency:   int = 100,
    n_routine:     int = 300,
    n_telemetry:   int = 600,
) -> IoTComparisonResult:
    """
    100 sensör simülasyonu.
    Her sensör ayrı MQTT bağlantısı açar (gerçek MQTT pattern).
    Mesajlar rastgele karışık gelir.
    Emergency mesajlar için 2ms deadline takip edilir.
    """
    # Mesaj listesi oluştur
    messages = []
    for i in range(n_emergency):
        messages.append({
            "type": "emergency",
            "sensor_id": i,
            "payload": b"FIRE_ALERT" + b"\x00" * 54,
            "deadline_ms": 2.0,
            "topic": f"sensors/{i}/emergency",
        })
    for i in range(n_routine):
        messages.append({
            "type": "routine",
            "sensor_id": i % n_sensors,
            "payload": b"TEMP_DATA" + b"\x00" * 991,
            "deadline_ms": 500.0,
            "topic": f"sensors/{i % n_sensors}/routine",
        })
    for i in range(n_telemetry):
        messages.append({
            "type": "telemetry",
            "sensor_id": i % n_sensors,
            "payload": b"BATT_SIG" + b"\x00" * 56,
            "deadline_ms": 5000.0,
            "topic": f"sensors/{i % n_sensors}/telemetry",
        })

    random.shuffle(messages)

    # 100 ayrı MQTT client (gerçek IoT pattern)
    clients = {}
    for sensor_id in range(n_sensors):
        c = mqtt.Client(
            client_id=f"sensor_{sensor_id}",
            protocol=mqtt.MQTTv5,
        )
        c.connect(broker_host, 1883, keepalive=60)
        c.loop_start()
        clients[sensor_id] = c

    time.sleep(1.0)  # Bağlantılar kurulsun

    emergency_hit   = 0
    total_ack_bytes = 0
    deadline_misses = 0
    t_start         = time.monotonic()

    for msg in messages:
        client = clients[msg["sensor_id"]]
        t0     = time.monotonic_ns()

        # QoS 1: gerçek IoT deployment standardı
        info = client.publish(
            msg["topic"],
            msg["payload"],
            qos=1,
        )
        info.wait_for_publish(timeout=10.0)

        elapsed_ms = (time.monotonic_ns() - t0) / 1e6
        total_ack_bytes += 4  # PUBACK = 4 byte

        if msg["type"] == "emergency":
            if elapsed_ms <= msg["deadline_ms"]:
                emergency_hit += 1
            else:
                deadline_misses += 1

    duration = time.monotonic() - t_start

    # Client'ları kapat
    for c in clients.values():
        c.loop_stop()
        c.disconnect()

    total = len(messages)

    return IoTComparisonResult(
        mqtt_connections=n_sensors,
        mqtt_emergency_hit_pct=emergency_hit / n_emergency * 100,
        mqtt_ack_bytes=total_ack_bytes,
        mqtt_throughput_msg_s=total / duration,
        mqtt_deadline_miss_pct=deadline_misses / n_emergency * 100,
    )
