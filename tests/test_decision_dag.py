# tests/test_decision_dag.py
import pytest
from qdap.protocol.decision_dag import (
    DecisionDAG, DecisionNode, build_send_dag
)
from qdap.scheduler.qft_scheduler import QFTScheduler
from qdap.broker.ghost_session_adaptive import AdaptiveGhostSession


def test_dag_build():
    dag = DecisionDAG(device_id="test")
    dag.add("Step1", {"in": 1}, {"out": 2}, us=10.0)
    dag.add("Step2", {"in": 2}, {"out": 3}, us=5.0)
    assert len(dag.nodes) == 2
    assert dag.nodes[0].name == "Step1"


def test_dag_summary():
    dag = DecisionDAG(device_id="dev_001")
    dag.add("QFT", {}, {}, 10.0)
    dag.add("Ghost", {}, {}, 2.0)
    dag.add("Priority", {}, {}, 1.0)
    summary = dag.summary()
    assert "QFT" in summary
    assert "Ghost" in summary
    assert "dev_001" in summary


def test_build_send_dag():
    sched = QFTScheduler()
    ghost = AdaptiveGhostSession("dag_test")
    ghost.on_data_received()

    dag = build_send_dag(
        device_id="sensor_01",
        payload_size=1024,
        rtt_ms=20.0,
        loss_rate=0.01,
        is_emergency=False,
        scheduler=sched,
        ghost_session=ghost,
    )

    assert len(dag.nodes) >= 3
    assert dag.final_stamp is not None
    assert dag.device_id == "sensor_01"


def test_emergency_dag_priority():
    """Emergency DAG → priority=1000."""
    sched = QFTScheduler()
    ghost = AdaptiveGhostSession("emrg_test")
    ghost.on_data_received()

    dag = build_send_dag(
        device_id="icu_01",
        payload_size=512,
        rtt_ms=300.0,
        loss_rate=0.35,
        is_emergency=True,
        scheduler=sched,
        ghost_session=ghost,
    )

    prio_node = next((n for n in dag.nodes if n.name == "Priority"), None)
    assert prio_node is not None
    assert prio_node.output.get("priority") == 1000


def test_dag_to_dict():
    dag = DecisionDAG(device_id="test")
    dag.add("Step", {}, {"x": 1}, 5.0)
    d = dag.to_dict()
    assert "device_id" in d
    assert "steps" in d
    assert d["steps"][0]["name"] == "Step"
