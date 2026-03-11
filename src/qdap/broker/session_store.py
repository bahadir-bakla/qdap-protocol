# src/qdap/broker/session_store.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import threading

@dataclass
class Session:
    client_id: str
    clean_session: bool
    writer: Optional[object] = None  # asyncio.StreamWriter
    pending_qos1: Dict[int, bytes] = field(default_factory=dict)
    next_packet_id: int = 1

    def get_packet_id(self) -> int:
        pid = self.next_packet_id
        self.next_packet_id = (self.next_packet_id % 65535) + 1
        return pid

class SessionStore:
    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.RLock()

    def create(self, client_id: str, clean_session: bool,
               writer) -> Session:
        with self._lock:
            if clean_session or client_id not in self._sessions:
                session = Session(client_id=client_id,
                                  clean_session=clean_session,
                                  writer=writer)
                self._sessions[client_id] = session
            else:
                self._sessions[client_id].writer = writer
            return self._sessions[client_id]

    def get(self, client_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(client_id)

    def remove(self, client_id: str):
        with self._lock:
            session = self._sessions.get(client_id)
            if session and session.clean_session:
                del self._sessions[client_id]
            elif session:
                session.writer = None  # disconnected but keep state

    def get_writer(self, client_id: str):
        with self._lock:
            s = self._sessions.get(client_id)
            return s.writer if s else None
