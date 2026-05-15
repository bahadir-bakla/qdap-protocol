"""
V2X Simulation Engine — discrete-event, 10 ms timestep.

Scenarios
---------
urban    : 400m x 400m intersection, buildings, mixed traffic
highway  : 2km dual-lane highway, high-speed platoon
cascade  : highway + pedestrian crossing -> emergency DENM propagation
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import time

from agents import Agent, AgentType
from messages import Message, MsgType, Priority
from channel import compute_snr_db, compute_cbr, is_los
from protocols import (
    DSRCProtocol, IEEE80211bdProtocol, CV2XProtocol,
    UDPProtocol, MQTTProtocol, QDAPProtocol,
)

ALL_PROTOCOLS = [
    QDAPProtocol,
    DSRCProtocol,
    IEEE80211bdProtocol,
    CV2XProtocol,
    UDPProtocol,
    MQTTProtocol,
]

MAX_RANGE_M = 500.0       # default one-hop communication range
DT_S = 0.05               # 50 ms timestep (position updates)
BSM_INTERVAL_S = 0.10     # 10 Hz BSM rate (SAE J2735)
SIM_DURATION_S = 20.0     # 20 s per run (full mode)
SIM_DURATION_QUICK_S = 5.0  # 5 s per run (quick mode)


# ─────────────────────────────────────────────────────────────────────────────
# Metrics container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimMetrics:
    """Collected metrics for one (protocol, n_agents, run) combination."""
    protocol_name: str
    n_agents: int
    scenario: str

    bsm_sent: int = 0
    bsm_delivered: int = 0
    bsm_met_deadline: int = 0
    denm_sent: int = 0
    denm_delivered: int = 0
    denm_met_deadline: int = 0

    latencies_normal_ms: List[float] = field(default_factory=list)
    latencies_emergency_ms: List[float] = field(default_factory=list)
    cascade_times_ms: List[float] = field(default_factory=list)

    cbr_samples: List[float] = field(default_factory=list)

    # ── Derived properties ────────────────────────────────────────────────

    @property
    def bsm_pdr(self) -> float:
        return self.bsm_delivered / max(self.bsm_sent, 1)

    @property
    def denm_pdr(self) -> float:
        return self.denm_delivered / max(self.denm_sent, 1)

    @property
    def bsm_deadline_rate(self) -> float:
        return self.bsm_met_deadline / max(self.bsm_sent, 1)

    @property
    def denm_deadline_rate(self) -> float:
        return self.denm_met_deadline / max(self.denm_sent, 1)

    @property
    def latency_p50(self) -> float:
        lats = self.latencies_emergency_ms + self.latencies_normal_ms
        return float(np.percentile(lats, 50)) if lats else 0.0

    @property
    def latency_p99(self) -> float:
        lats = self.latencies_emergency_ms + self.latencies_normal_ms
        return float(np.percentile(lats, 99)) if lats else 0.0

    @property
    def emergency_p99(self) -> float:
        return (
            float(np.percentile(self.latencies_emergency_ms, 99))
            if self.latencies_emergency_ms
            else 0.0
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario builders
# ─────────────────────────────────────────────────────────────────────────────

def setup_urban_intersection(n_agents: int, rng: np.random.Generator):
    """
    400m x 400m urban intersection.
    Road layout : two perpendicular roads, 40m wide.
    Buildings   : four 170m x 170m AABB corners.
    Traffic mix : 70% cars, 20% motorcycles, 10% pedestrians.
    Returns     : (agents, buildings, road_bounds)
    """
    agents = []
    buildings = [
        (10,  10,  170, 170),   # SW corner
        (220, 10,  170, 170),   # SE corner
        (10,  220, 170, 170),   # NW corner
        (220, 220, 170, 170),   # NE corner
    ]

    road_positions: List[Tuple[float, float, float, float]] = []

    # Horizontal road: y in [180, 220], x in [0, 400]
    for _ in range(int(n_agents * 0.4)):
        x = rng.uniform(0, 400)
        y = rng.uniform(180, 220)
        speed = rng.uniform(6, 14)  # 22–50 km/h urban
        heading = 0.0 if rng.random() < 0.5 else np.pi
        road_positions.append((x, y, speed, heading))

    # Vertical road: x in [180, 220], y in [0, 400]
    for _ in range(int(n_agents * 0.4)):
        x = rng.uniform(180, 220)
        y = rng.uniform(0, 400)
        speed = rng.uniform(6, 14)
        heading = np.pi / 2 if rng.random() < 0.5 else -np.pi / 2
        road_positions.append((x, y, speed, heading))

    # Remaining: scattered (parked, side streets)
    while len(road_positions) < n_agents:
        x = rng.uniform(0, 400)
        y = rng.uniform(0, 400)
        speed = rng.uniform(0, 5)
        heading = rng.uniform(0, 2 * np.pi)
        road_positions.append((x, y, speed, heading))

    rng.shuffle(road_positions)  # type: ignore[arg-type]

    for i, (x, y, speed, heading) in enumerate(road_positions[:n_agents]):
        r = rng.random()
        pos = np.array([x, y])
        if r < 0.10:
            agents.append(Agent.make_pedestrian(i, pos, rng))
        elif r < 0.30:
            agents.append(Agent.make_motorcycle(i, pos, speed * 1.2, heading))
        else:
            agents.append(Agent.make_car(i, pos, speed, heading))

    return agents, buildings, (0.0, 400.0, 0.0, 400.0)


def setup_highway_platoon(n_agents: int, rng: np.random.Generator):
    """
    2 km straight dual-lane highway.
    Speed: 27–36 m/s (100–130 km/h).
    Mix  : 85% cars, 15% motorcycles.
    Returns: (agents, buildings, road_bounds)
    """
    agents = []
    buildings: list = []  # open highway

    spacing_m = max(2000.0 / max(n_agents // 2, 1), 25.0)

    for i in range(n_agents):
        lane = i % 2
        x = (i // 2) * spacing_m + rng.uniform(-3, 3)
        y = 3.5 + lane * 3.5   # 3.5 m lane width
        speed = rng.uniform(27, 36) + lane * 2  # overtaking lane slightly faster
        heading = 0.0
        pos = np.array([x, y])

        if rng.random() < 0.15:
            agents.append(Agent.make_motorcycle(i, pos, speed, heading))
        else:
            agents.append(Agent.make_car(i, pos, speed, heading))

    return agents, buildings, (0.0, 2000.0, 0.0, 7.0)


def setup_emergency_cascade(n_agents: int, rng: np.random.Generator):
    """
    Highway with a pedestrian crossing the road.
    Lead vehicle sees the hazard, brakes, and sends a DENM cascade.
    Returns: (agents, buildings, road_bounds)
    """
    agents, buildings, bounds = setup_highway_platoon(n_agents, rng)

    # Pedestrian stepping off the kerb at x=500 m
    ped = Agent.make_pedestrian(
        n_agents, np.array([500.0, 3.5]), rng
    )
    ped.vel = np.array([0.0, 1.0])  # slowly crossing
    agents.append(ped)

    return agents, buildings, bounds


# ─────────────────────────────────────────────────────────────────────────────
# Core simulation runner
# ─────────────────────────────────────────────────────────────────────────────

def run_scenario(
    scenario_name: str,
    protocol_cls,
    n_agents: int,
    n_runs: int = 5,
    seed: int = 42,
    duration_s: float = SIM_DURATION_S,
) -> List[SimMetrics]:
    """
    Monte-Carlo simulation: runs `n_runs` independent replications.

    Parameters
    ----------
    scenario_name : "urban" | "highway" | "cascade"
    protocol_cls  : uninstantiated protocol class (one of ALL_PROTOCOLS)
    n_agents      : number of traffic participants
    n_runs        : number of independent Monte-Carlo runs
    seed          : base RNG seed (each run gets seed + run_idx*1000)
    duration_s    : simulated time per run

    Returns
    -------
    List of SimMetrics, one per run.
    """
    results: List[SimMetrics] = []

    for run_idx in range(n_runs):
        rng = np.random.default_rng(seed + run_idx * 1000)
        protocol = protocol_cls()

        # ── Build scenario ─────────────────────────────────────────────
        if scenario_name == "urban":
            agents, buildings, bounds = setup_urban_intersection(n_agents, rng)
        elif scenario_name == "highway":
            agents, buildings, bounds = setup_highway_platoon(n_agents, rng)
        elif scenario_name == "cascade":
            agents, buildings, bounds = setup_emergency_cascade(n_agents, rng)
        else:
            raise ValueError(f"Unknown scenario: {scenario_name!r}")

        metrics = SimMetrics(
            protocol_name=protocol.name,
            n_agents=n_agents,
            scenario=scenario_name,
        )

        msg_id = 0
        t_s = 0.0
        active_radios = [a for a in agents if a.has_radio]
        N = len(active_radios)

        # Cascade-specific state
        cascade_triggered = False
        cascade_trigger_time_ms: Optional[float] = None
        cascade_received: Dict[int, float] = {}

        # BSM every 100ms — track which steps fire BSMs
        bsm_step_interval = max(1, int(BSM_INTERVAL_S / DT_S))
        step = 0
        total_steps = int(duration_s / DT_S)

        # Deterministic emergency injection — same across all protocols for fair comparison.
        # urban/highway: 2 events per run, at 25% and 65% of sim duration.
        # cascade: single event (lead vehicle, handled separately).
        n_radio = len(active_radios)
        if scenario_name in ("urban", "highway") and n_radio >= 2:
            e1_step = int(0.25 * total_steps)
            e2_step = int(0.65 * total_steps)
            e1_vid = 0 % n_radio
            e2_vid = (n_radio // 2) % n_radio
            clear_dur = int(1.5 / DT_S)
            emerg_inject = {e1_step: e1_vid, e2_step: e2_vid}
            emerg_clear  = {e1_step + clear_dur: e1_vid, e2_step + clear_dur: e2_vid}
        else:
            emerg_inject = {}
            emerg_clear  = {}

        # ── Discrete-event loop ────────────────────────────────────────
        while t_s < duration_s:
            t_ms = t_s * 1000.0

            # 1. Kinematics update
            for agent in agents:
                agent.update(DT_S, road_bounds=bounds)

            # 2a. Deterministic emergency inject / clear
            if step in emerg_inject:
                active_radios[emerg_inject[step]].emergency = True
            if step in emerg_clear:
                active_radios[emerg_clear[step]].emergency = False

            # 2b. Cascade trigger: lead vehicle brakes at t=2s
            if scenario_name == "cascade" and not cascade_triggered and t_s > 2.0:
                lead = min(active_radios, key=lambda a: a.pos[0], default=None)
                if lead is not None:
                    lead.braking = True
                    lead.emergency = True
                    cascade_triggered = True
                    cascade_trigger_time_ms = t_ms

            # 3 & 4. BSM + DENM broadcast — every bsm_step_interval steps
            if step % bsm_step_interval == 0 and N > 0:
                # Vectorized distance matrix: (N, 2) → (N, N) distances
                positions = np.array([a.pos for a in active_radios])  # (N, 2)
                diff = positions[:, None, :] - positions[None, :, :]  # (N, N, 2)
                dist_matrix = np.linalg.norm(diff, axis=-1)            # (N, N)

                # LOS approximation: LOS if dist < 350m (same-road V2V line-of-sight).
                # Cross-road NLOS (behind buildings) modeled by 350m threshold.
                # Full ray-cast is O(N²×buildings) — too slow for large N.
                los_matrix = dist_matrix < 350.0

                for i, agent in enumerate(active_radios):
                    # ── BSM ─────────────────────────────────────────
                    in_range_mask = (dist_matrix[i] > 0) & (dist_matrix[i] <= MAX_RANGE_M)
                    n_nbr = int(in_range_mask.sum())
                    cbr = compute_cbr(n_nbr)  # local CBR: neighbours only

                    for j, rx in enumerate(active_radios):
                        if i == j or not in_range_mask[j]:
                            continue
                        snr = compute_snr_db(
                            dist_matrix[i, j], bool(los_matrix[i, j]),
                            rng, agent.tx_power_dbm,
                        )
                        msg = Message.make_bsm(
                            msg_id, agent.id, t_ms, is_vru=agent.is_vru,
                        )
                        msg_id += 1
                        result = protocol.deliver(msg, snr, cbr, n_nbr, rng)
                        metrics.bsm_sent += 1
                        if result.delivered:
                            metrics.bsm_delivered += 1
                            metrics.latencies_normal_ms.append(result.latency_ms)
                            if result.latency_ms <= msg.deadline_ms:
                                metrics.bsm_met_deadline += 1

                    # ── DENM (emergency agents only) ─────────────────
                    if not agent.emergency:
                        continue

                    denm_range = MAX_RANGE_M * 1.2
                    denm_mask = (dist_matrix[i] > 0) & (dist_matrix[i] <= denm_range)
                    n_denm_nbr = int(denm_mask.sum())
                    cbr_denm = compute_cbr(n_denm_nbr)  # local CBR for DENM range

                    for j, rx in enumerate(active_radios):
                        if i == j or not denm_mask[j]:
                            continue
                        snr = compute_snr_db(
                            dist_matrix[i, j], bool(los_matrix[i, j]),
                            rng, agent.tx_power_dbm + 2.0,
                        )
                        msg = Message.make_denm(msg_id, agent.id, t_ms)
                        msg_id += 1
                        result = protocol.deliver(
                            msg, snr, cbr_denm, n_denm_nbr, rng
                        )
                        metrics.denm_sent += 1
                        if result.delivered:
                            metrics.denm_delivered += 1
                            metrics.latencies_emergency_ms.append(result.latency_ms)
                            if result.latency_ms <= msg.deadline_ms:
                                metrics.denm_met_deadline += 1
                            if (scenario_name == "cascade"
                                    and cascade_trigger_time_ms is not None
                                    and rx.id not in cascade_received):
                                cascade_received[rx.id] = (
                                    t_ms + result.latency_ms - cascade_trigger_time_ms
                                )

            # 5. Sample CBR
            metrics.cbr_samples.append(compute_cbr(N))
            t_s += DT_S
            step += 1

        if scenario_name == "cascade":
            metrics.cascade_times_ms = list(cascade_received.values())

        results.append(metrics)

    return results
