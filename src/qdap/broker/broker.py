# src/qdap/broker/broker.py
import asyncio
import logging
import argparse
from typing import Dict

from .packet_parser import (
    parse_packet, build_connack, build_suback, build_unsuback,
    build_puback, build_publish, PINGRESP,
    MQTT_CONNECT, MQTT_PUBLISH, MQTT_SUBSCRIBE, MQTT_UNSUBSCRIBE,
    MQTT_PINGREQ, MQTT_DISCONNECT
)
from .topic_tree import TopicTree
from .session_store import SessionStore
from .qdap_transport import topic_to_priority

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [QDAP-BROKER] %(message)s")
logger = logging.getLogger(__name__)

class QDAPBroker:
    def __init__(self, host: str = "0.0.0.0", port: int = 1883):
        self.host = host
        self.port = port
        self.topic_tree = TopicTree()
        self.sessions = SessionStore()
        self._stats = {"published": 0, "delivered": 0, "connected": 0}

    async def handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        logger.info(f"New connection from {addr}")
        client_id = None
        buffer = b""

        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=120)
                if not chunk:
                    break
                buffer += chunk

                # Parse all complete packets in buffer
                while len(buffer) >= 2:
                    from .packet_parser import decode_remaining_length
                    try:
                        rem_len, offset = decode_remaining_length(buffer, 1)
                    except IndexError:
                        break  # Length header is cut off, wait for more data

                    consumed = offset + rem_len
                    if len(buffer) < consumed:
                        break  # Payload is cut off, wait for more data

                    pkt = parse_packet(buffer[:consumed])
                    if pkt is None:
                        break

                    buffer = buffer[consumed:]

                    client_id = await self._handle_packet(
                        pkt, writer, client_id)

        except asyncio.TimeoutError:
            logger.warning(f"Client {client_id} timed out")
        except Exception as e:
            logger.error(f"Client {client_id} error: {e}")
        finally:
            if client_id:
                self.sessions.remove(client_id)
                self.topic_tree.remove_client(client_id)
            writer.close()
            logger.info(f"Client {client_id} disconnected")

    async def _handle_packet(self, pkt, writer, client_id):
        if pkt.ptype == MQTT_CONNECT:
            client_id = pkt.client_id or f"auto_{id(writer)}"
            session = self.sessions.create(client_id, pkt.clean_session, writer)
            session_present = not pkt.clean_session and client_id in str(session)
            writer.write(build_connack(session_present=False, reason_code=0))
            await writer.drain()
            self._stats["connected"] += 1
            logger.info(f"CONNECT: {client_id}")

        elif pkt.ptype == MQTT_PUBLISH:
            self._stats["published"] += 1
            priority = topic_to_priority(pkt.topic, len(pkt.payload))

            # Deliver to subscribers
            subscribers = self.topic_tree.match(pkt.topic)
            for sub_client_id, sub_qos in subscribers:
                if sub_client_id == client_id:
                    continue  # no echo
                sub_writer = self.sessions.get_writer(sub_client_id)
                if sub_writer:
                    effective_qos = min(pkt.qos, sub_qos)
                    session = self.sessions.get(sub_client_id)
                    pid = session.get_packet_id() if effective_qos > 0 else None
                    pub = build_publish(pkt.topic, pkt.payload,
                                       qos=effective_qos, packet_id=pid,
                                       retain=pkt.retain)
                    sub_writer.write(pub)
                    try:
                        await sub_writer.drain()
                        self._stats["delivered"] += 1
                    except Exception:
                        pass

            # Send PUBACK for QoS 1
            if pkt.qos == 1 and pkt.packet_id is not None:
                writer.write(build_puback(pkt.packet_id))
                await writer.drain()

            if priority >= 1000:
                logger.info(f"🚨 EMERGENCY publish: {pkt.topic} "
                            f"({len(pkt.payload)}B) → {len(subscribers)} subs")

        elif pkt.ptype == MQTT_SUBSCRIBE:
            reason_codes = []
            for topic_filter, qos in pkt.subscriptions:
                self.topic_tree.subscribe(client_id, topic_filter, qos)
                reason_codes.append(qos)
                logger.info(f"SUBSCRIBE: {client_id} → {topic_filter} (QoS{qos})")
            writer.write(build_suback(pkt.packet_id, reason_codes))
            await writer.drain()

        elif pkt.ptype == MQTT_UNSUBSCRIBE:
            for topic_filter, _ in pkt.subscriptions:
                self.topic_tree.unsubscribe(client_id, topic_filter)
            writer.write(build_unsuback(pkt.packet_id, len(pkt.subscriptions)))
            await writer.drain()

        elif pkt.ptype == MQTT_PINGREQ:
            writer.write(PINGRESP)
            await writer.drain()

        elif pkt.ptype == MQTT_DISCONNECT:
            raise ConnectionResetError("Client disconnected gracefully")

        return client_id

    async def start(self):
        server = await asyncio.start_server(
            self.handle_client, self.host, self.port)
        addr = server.sockets[0].getsockname()
        logger.info(f"QDAP Broker listening on {addr[0]}:{addr[1]}")
        logger.info("Emergency topic detection: ACTIVE")
        async with server:
            await server.serve_forever()

    def get_stats(self) -> dict:
        return dict(self._stats)

def main():
    parser = argparse.ArgumentParser(description="QDAP MQTT Broker")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=1883)
    args = parser.parse_args()
    broker = QDAPBroker(host=args.host, port=args.port)
    asyncio.run(broker.start())

if __name__ == "__main__":
    main()
