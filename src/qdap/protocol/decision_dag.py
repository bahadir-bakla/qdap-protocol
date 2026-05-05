# src/qdap/protocol/decision_dag.py
"""
Decision DAG — NattyNet PoA Analogu
=====================================
NattyNet [Arch-TCOM'26] Dynamic Kernel'inde her node
local PoA (Plan of Actions) oluşturur, bu planlar birleşerek
global emergent DAG oluşturur.

QDAP Decision DAG:
  Node A: QFT decision → chunk_size=SMALL, priority=500
  Node B: Ghost check  → ghost=False, keepalive=0
  Node C: Security     → 0RTT=True, ticket_valid=True
  DAG:    A → B → C → SEND

Her karar bir öncekinin output'unu input olarak alır.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DecisionNode:
    name:     str
    input:    dict = field(default_factory=dict)
    output:   dict = field(default_factory=dict)
    duration_us: float = 0.0  # microseconds


@dataclass
class DecisionDAG:
    """
    Tek bir send/recv için karar zinciri.
    NattyNet PoA analogu — lokal, spekülatif.
    """
    device_id:  str
    timestamp:  float = 0.0
    nodes:      List[DecisionNode] = field(default_factory=list)
    final_stamp: Optional[object] = None  # ChannelStamp

    def add(self, name: str, input_dict: dict, output_dict: dict, us: float = 0.0):
        self.nodes.append(DecisionNode(name, input_dict, output_dict, us))

    def summary(self) -> str:
        steps = " → ".join(n.name for n in self.nodes)
        total_us = sum(n.duration_us for n in self.nodes)
        return f"DAG[{self.device_id}]: {steps} ({total_us:.1f}μs)"

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "steps": [
                {"name": n.name, "duration_us": n.duration_us,
                 "output": n.output}
                for n in self.nodes
            ],
        }


def build_send_dag(
    device_id:    str,
    payload_size: int,
    rtt_ms:       float,
    loss_rate:    float,
    is_emergency: bool,
    scheduler,
    ghost_session,
    timestamp:    float = 0.0,
) -> DecisionDAG:
    """
    Tek send operasyonu için DecisionDAG oluştur.
    NattyNet'in local PoA construction'ı analogu.
    """
    import time
    dag = DecisionDAG(device_id=device_id, timestamp=timestamp or time.time())

    # Step 1: QFT Scheduling decision
    t0 = time.perf_counter()
    try:
        decision = scheduler.decide(payload_size, rtt_ms, loss_rate)
        chunk_size = decision.chunk_size_bytes if hasattr(decision, 'chunk_size_bytes') else payload_size
        strategy   = decision.strategy_name if hasattr(decision, 'strategy_name') else "MEDIUM"
        confidence = decision.confidence if hasattr(decision, 'confidence') else 0.5
    except Exception:
        chunk_size = min(payload_size, 65536)
        strategy   = "MEDIUM"
        confidence = 0.5
    t1 = time.perf_counter()
    dag.add("QFT", {"payload": payload_size, "rtt": rtt_ms, "loss": loss_rate},
            {"chunk": chunk_size, "strategy": str(strategy), "conf": round(confidence, 3)},
            us=(t1-t0)*1e6)

    # Step 2: Ghost Session check
    t0 = time.perf_counter()
    try:
        ghost_state = ghost_session.state.value
        ghost_session.on_data_received()
        keepalive_b = ghost_session.keepalive_bytes_per_minute()
    except Exception:
        ghost_state = "ACTIVE"
        keepalive_b = 0
    t1 = time.perf_counter()
    dag.add("Ghost", {"state": ghost_state},
            {"keepalive_bpm": keepalive_b, "new_state": ghost_state},
            us=(t1-t0)*1e6)

    # Step 3: Priority assignment
    t0 = time.perf_counter()
    priority = 1000 if is_emergency else min(999, int(confidence * 500) + 100)
    t1 = time.perf_counter()
    dag.add("Priority", {"emergency": is_emergency, "confidence": confidence},
            {"priority": priority},
            us=(t1-t0)*1e6)

    # Step 4: Channel stamp generation
    from qdap.protocol.channel_stamps import stamp_from_scheduler, ChannelStamp
    t0 = time.perf_counter()
    try:
        stamp = stamp_from_scheduler(
            scheduler, ghost_active=(ghost_state == "GHOST"),
            emergency=is_emergency,
        )
    except Exception:
        stamp = ChannelStamp()
    t1 = time.perf_counter()
    dag.add("Stamp", {}, {"stamp": stamp.summary()}, us=(t1-t0)*1e6)
    dag.final_stamp = stamp

    return dag
