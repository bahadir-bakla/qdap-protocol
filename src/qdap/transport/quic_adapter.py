"""
QDAP over QUIC Adapter (Stub)
==============================

QUIC stream multiplexing + QDAP frame multiplexing.
Her QFrame tipi farklı QUIC stream'e map edilir.

Not: Full implementation requires aioquic integration.
This is a structural stub for Phase 0.
"""

from __future__ import annotations

from dataclasses import dataclass

from qdap.frame.qframe import QFrame, FrameType


# QUIC stream mapping: each frame type → dedicated stream
STREAM_MAP = {
    FrameType.DATA: 0,    # Bidirectional stream 0
    FrameType.CTRL: 2,    # Bidirectional stream 2
    FrameType.GHOST: 4,   # Unidirectional stream 4
    FrameType.PROBE: 6,
    FrameType.SYNC: 8,
}


@dataclass
class QDAPOverQUIC:
    """
    QDAP frame'lerini QUIC üzerinden taşır.

    Phase 0 stub — full aioquic integration in Phase 2.
    """

    async def send_frame(self, frame: QFrame) -> None:
        """Send a QFrame over the appropriate QUIC stream."""
        stream_id = STREAM_MAP.get(frame.frame_type, 0)
        _data = frame.serialize()
        # TODO: Phase 2 — aioquic integration
        # self._quic.send_stream_data(stream_id=stream_id, data=data, end_stream=False)
        raise NotImplementedError("QUIC adapter will be implemented in Phase 2")

    async def recv_frame(self) -> QFrame:
        """Receive a QFrame from QUIC streams."""
        # TODO: Phase 2 — aioquic integration
        raise NotImplementedError("QUIC adapter will be implemented in Phase 2")
