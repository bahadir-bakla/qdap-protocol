"""
Traffic agents: Car, Motorcycle, Pedestrian with realistic kinematics.
"""
import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class AgentType(Enum):
    CAR = "car"
    MOTORCYCLE = "motorcycle"
    PEDESTRIAN = "pedestrian"
    TRUCK = "truck"


@dataclass
class Agent:
    id: int
    agent_type: AgentType
    pos: np.ndarray          # [x, y] meters
    vel: np.ndarray          # [vx, vy] m/s
    heading: float           # radians
    mass_kg: float
    length_m: float
    width_m: float
    antenna_height_m: float
    tx_power_dbm: float
    max_speed_mps: float
    max_accel_mps2: float
    max_decel_mps2: float
    has_radio: bool = True
    is_vru: bool = False      # Vulnerable Road User
    braking: bool = False
    emergency: bool = False
    target_speed_mps: float = 0.0
    lane: int = 0             # lane index for multi-lane roads

    @classmethod
    def make_car(cls, id: int, pos: np.ndarray, speed_mps: float,
                 heading: float) -> 'Agent':
        return cls(
            id=id, agent_type=AgentType.CAR,
            pos=pos.copy(),
            vel=np.array([np.cos(heading) * speed_mps, np.sin(heading) * speed_mps]),
            heading=heading,
            mass_kg=1500.0, length_m=4.5, width_m=1.8,
            antenna_height_m=1.5, tx_power_dbm=23.0,
            max_speed_mps=36.1, max_accel_mps2=3.0, max_decel_mps2=8.0,
            target_speed_mps=speed_mps,
        )

    @classmethod
    def make_motorcycle(cls, id: int, pos: np.ndarray, speed_mps: float,
                        heading: float) -> 'Agent':
        a = cls(
            id=id, agent_type=AgentType.MOTORCYCLE,
            pos=pos.copy(),
            vel=np.array([np.cos(heading) * speed_mps, np.sin(heading) * speed_mps]),
            heading=heading,
            mass_kg=200.0, length_m=2.2, width_m=0.8,
            antenna_height_m=1.2, tx_power_dbm=20.0,
            max_speed_mps=44.4, max_accel_mps2=4.0, max_decel_mps2=9.0,
            target_speed_mps=speed_mps,
        )
        a.is_vru = True
        return a

    @classmethod
    def make_pedestrian(cls, id: int, pos: np.ndarray,
                        rng: np.random.Generator) -> 'Agent':
        heading = rng.uniform(0, 2 * np.pi)
        speed = rng.uniform(1.0, 1.8)
        a = cls(
            id=id, agent_type=AgentType.PEDESTRIAN,
            pos=pos.copy(),
            vel=np.array([np.cos(heading) * speed, np.sin(heading) * speed]),
            heading=heading,
            mass_kg=70.0, length_m=0.5, width_m=0.5,
            antenna_height_m=1.7, tx_power_dbm=0.0,
            max_speed_mps=2.5, max_accel_mps2=1.5, max_decel_mps2=2.0,
            target_speed_mps=speed,
        )
        a.is_vru = True
        a.has_radio = False
        return a

    def update(self, dt: float, road_bounds: Optional[Tuple[float, float, float, float]] = None):
        speed = float(np.linalg.norm(self.vel))
        if self.braking:
            if speed > 0:
                decel = min(self.max_decel_mps2, speed / max(dt, 1e-9))
                self.vel = self.vel - (self.vel / speed) * decel * dt
                new_speed = float(np.linalg.norm(self.vel))
                if new_speed < 0.1:
                    self.vel = np.zeros(2)
        elif speed < self.target_speed_mps:
            accel = min(self.max_accel_mps2,
                        (self.target_speed_mps - speed) / max(dt, 1e-9))
            if speed > 0:
                self.vel = self.vel + (self.vel / speed) * accel * dt
            else:
                self.vel = (
                    np.array([np.cos(self.heading), np.sin(self.heading)]) * accel * dt
                )

        speed = float(np.linalg.norm(self.vel))
        if speed > self.max_speed_mps:
            self.vel = self.vel * self.max_speed_mps / speed

        self.pos = self.pos + self.vel * dt

        # Update heading from velocity
        if speed > 0.1:
            self.heading = float(np.arctan2(self.vel[1], self.vel[0]))

        if road_bounds is not None:
            xmin, xmax, ymin, ymax = road_bounds
            self.pos[0] = xmin + (self.pos[0] - xmin) % (xmax - xmin)
            self.pos[1] = ymin + (self.pos[1] - ymin) % (ymax - ymin)

    @property
    def speed(self) -> float:
        return float(np.linalg.norm(self.vel))

    @property
    def speed_kmh(self) -> float:
        return self.speed * 3.6

    def distance_to(self, other: 'Agent') -> float:
        return float(np.linalg.norm(self.pos - other.pos))

    def time_to_collision(self, other: 'Agent') -> float:
        """TTC estimate for following vehicles."""
        rel_pos = self.pos - other.pos
        rel_vel = self.vel - other.vel
        d = float(np.linalg.norm(rel_pos))
        closing_speed = float(np.dot(-rel_pos / max(d, 0.1), rel_vel))
        if closing_speed <= 0:
            return float('inf')
        return d / closing_speed
