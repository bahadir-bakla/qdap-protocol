"""
V2X message types based on SAE J2735 (BSM) and ETSI EN 302 637 (DENM/CPM).
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np


class MsgType(Enum):
    BSM  = "BSM"   # Basic Safety Message — 10 Hz, periodic
    DENM = "DENM"  # Decentralized Environmental Notification — event-driven, emergency
    CPM  = "CPM"   # Collective Perception Message — sensor fusion
    SPAT = "SPAT"  # Signal Phase And Timing


class Priority(Enum):
    EMERGENCY = 0  # DENM: collision warning, emergency brake
    HIGH = 1       # BSM from VRU (pedestrian detected, motorcycle)
    NORMAL = 2     # BSM from vehicles
    LOW = 3        # CPM, SPAT


# SAE J2735 / ETSI EN 302 637 payload sizes (bytes)
MSG_SIZES_BYTES = {
    MsgType.BSM:  400,
    MsgType.DENM: 250,
    MsgType.CPM:  2000,
    MsgType.SPAT: 500,
}

# Maximum acceptable one-way latency (ms)
MSG_DEADLINES_MS = {
    MsgType.DENM: 50.0,
    MsgType.BSM:  100.0,
    MsgType.SPAT: 500.0,
    MsgType.CPM:  1000.0,
}


@dataclass
class Message:
    id: int
    src_id: int
    msg_type: MsgType
    priority: Priority
    payload_bytes: int
    sent_at_ms: float          # simulation wall-clock time (ms)
    deadline_ms: float         # max acceptable one-way latency (ms)
    src_pos: Optional[np.ndarray] = None
    received_at_ms: Optional[float] = None
    delivered: bool = False

    @property
    def latency_ms(self) -> Optional[float]:
        if self.received_at_ms is None:
            return None
        return self.received_at_ms - self.sent_at_ms

    @property
    def met_deadline(self) -> bool:
        lat = self.latency_ms
        return lat is not None and lat <= self.deadline_ms

    @classmethod
    def make_bsm(cls, msg_id: int, src_id: int, t_ms: float,
                 is_vru: bool = False,
                 src_pos: Optional[np.ndarray] = None) -> 'Message':
        return cls(
            id=msg_id, src_id=src_id, msg_type=MsgType.BSM,
            priority=Priority.HIGH if is_vru else Priority.NORMAL,
            payload_bytes=MSG_SIZES_BYTES[MsgType.BSM],
            sent_at_ms=t_ms, deadline_ms=MSG_DEADLINES_MS[MsgType.BSM],
            src_pos=src_pos,
        )

    @classmethod
    def make_denm(cls, msg_id: int, src_id: int, t_ms: float,
                  src_pos: Optional[np.ndarray] = None) -> 'Message':
        return cls(
            id=msg_id, src_id=src_id, msg_type=MsgType.DENM,
            priority=Priority.EMERGENCY,
            payload_bytes=MSG_SIZES_BYTES[MsgType.DENM],
            sent_at_ms=t_ms, deadline_ms=MSG_DEADLINES_MS[MsgType.DENM],
            src_pos=src_pos,
        )
