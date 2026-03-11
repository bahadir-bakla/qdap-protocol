# src/qdap/broker/topic_tree.py
from typing import Dict, List, Set, Tuple
import threading

class TopicNode:
    def __init__(self):
        self.children: Dict[str, "TopicNode"] = {}
        self.subscribers: Dict[str, int] = {}  # client_id -> qos

class TopicTree:
    def __init__(self):
        self.root = TopicNode()
        self._lock = threading.RLock()

    def subscribe(self, client_id: str, topic_filter: str, qos: int):
        with self._lock:
            parts = topic_filter.split("/")
            node = self.root
            for part in parts:
                if part not in node.children:
                    node.children[part] = TopicNode()
                node = node.children[part]
            node.subscribers[client_id] = qos

    def unsubscribe(self, client_id: str, topic_filter: str):
        with self._lock:
            parts = topic_filter.split("/")
            self._unsubscribe_node(self.root, parts, 0, client_id)

    def _unsubscribe_node(self, node: TopicNode, parts: List[str],
                           idx: int, client_id: str):
        if idx == len(parts):
            node.subscribers.pop(client_id, None)
            return
        part = parts[idx]
        if part in node.children:
            self._unsubscribe_node(node.children[part], parts, idx + 1, client_id)

    def match(self, topic: str) -> List[Tuple[str, int]]:
        """Returns list of (client_id, qos) matching topic."""
        with self._lock:
            parts = topic.split("/")
            results: Dict[str, int] = {}
            self._match_node(self.root, parts, 0, results)
            return list(results.items())

    def _match_node(self, node: TopicNode, parts: List[str],
                    idx: int, results: Dict[str, int]):
        if idx == len(parts):
            for client_id, qos in node.subscribers.items():
                results[client_id] = max(results.get(client_id, 0), qos)
            return

        part = parts[idx]

        # Exact match
        if part in node.children:
            self._match_node(node.children[part], parts, idx + 1, results)

        # Single-level wildcard +
        if "+" in node.children:
            self._match_node(node.children["+"], parts, idx + 1, results)

        # Multi-level wildcard #
        if "#" in node.children:
            hash_node = node.children["#"]
            for client_id, qos in hash_node.subscribers.items():
                results[client_id] = max(results.get(client_id, 0), qos)

    def remove_client(self, client_id: str):
        with self._lock:
            self._remove_from_node(self.root, client_id)

    def _remove_from_node(self, node: TopicNode, client_id: str):
        node.subscribers.pop(client_id, None)
        for child in node.children.values():
            self._remove_from_node(child, client_id)
