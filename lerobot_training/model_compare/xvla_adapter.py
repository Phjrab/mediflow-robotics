"""Offline single-arm contract around an injected X-VLA policy and processors."""

from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Protocol

JOINTS = (
    "shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos",
    "wrist_flex.pos", "wrist_roll.pos", "gripper.pos",
)
VIEWS = ("wrist", "overhead", "oblique")
Vector = tuple[float, ...]
Chunk = tuple[Vector, ...]


@dataclass(frozen=True)
class RGBFrame:
    width: int
    height: int
    packed_rgb: bytes

    def __post_init__(self):
        if (
            self.width <= 0
            or self.height <= 0
            or not isinstance(self.packed_rgb, bytes)
            or len(self.packed_rgb) != self.width * self.height * 3
        ):
            raise ValueError("expected a full packed RGB8 frame")


@dataclass(frozen=True)
class Contract:
    camera_keys: tuple[str, str, str]
    chunk_size: int
    n_action_steps: int
    action_fps: float
    domain_id: int
    joint_bounds: tuple[tuple[float, float], ...]
    artifact_id: str

    def __post_init__(self):
        if len(self.camera_keys) != 3 or len(set(self.camera_keys)) != 3:
            raise ValueError("expected three distinct checkpoint camera keys")
        if not all(key.startswith("observation.images.") for key in self.camera_keys):
            raise ValueError("invalid checkpoint image key")
        if not 1 <= self.n_action_steps <= self.chunk_size:
            raise ValueError("invalid execution horizon")
        if not isfinite(self.action_fps) or self.action_fps <= 0 or not 0 <= self.domain_id < 30:
            raise ValueError("invalid action rate or domain id")
        if len(self.joint_bounds) != 6 or self.joint_bounds[-1] != (0, 100):
            raise ValueError("expected five degree bounds and gripper bounds (0, 100)")
        if any(not (isfinite(lo) and isfinite(hi) and lo < hi) for lo, hi in self.joint_bounds):
            raise ValueError("invalid joint bounds")
        if not self.artifact_id.strip():
            raise ValueError("artifact identifier required")


class Backend(Protocol):
    """Bridge converting RGB8 to official processor input and tensor output to rows."""

    def reset(self) -> None: ...
    def preprocess(self, observation: dict) -> object: ...
    def predict_action_chunk(self, batch: object) -> object: ...
    def postprocess(self, policy_chunk: object) -> object: ...


def finite_vector(values) -> Vector:
    if not isinstance(values, (tuple, list)) or len(values) != len(JOINTS):
        raise ValueError("expected six values in canonical joint order")
    if any(isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value)
           for value in values):
        raise ValueError("expected six finite numeric values")
    return tuple(float(value) for value in values)


def batch_one_chunk(raw, horizon: int) -> Chunk:
    rows = raw.tolist() if hasattr(raw, "tolist") else raw
    if not isinstance(rows, (tuple, list)) or len(rows) != 1:
        raise ValueError("expected batch dimension of one")
    if not isinstance(rows[0], (tuple, list)) or len(rows[0]) != horizon:
        raise ValueError("unexpected chunk horizon")
    return tuple(finite_vector(row) for row in rows[0])


class XVLAAdapter:
    """Accepts a backend for a checkpoint trained with auto/6 and absolute targets."""

    @classmethod
    def load(cls, contract: Contract, backend: Backend):
        adapter = cls(contract, backend)
        adapter.reset()
        return adapter

    def __init__(self, contract: Contract, backend: Backend):
        self.contract = contract
        self.backend = backend

    def reset(self) -> None:
        self.backend.reset()

    def prepare_observation(self, *, views, state, task):
        if set(views) != set(VIEWS) or not all(isinstance(view, RGBFrame) for view in views.values()):
            raise ValueError("expected real wrist, overhead and oblique RGB frames")
        if set(state) != set(JOINTS):
            raise ValueError("expected six named state values")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("expected task text")
        observation = {
            key: views[view]
            for view, key in zip(VIEWS, self.contract.camera_keys, strict=True)
        }
        positions = finite_vector([state[joint] for joint in JOINTS])
        if not 0 <= positions[-1] <= 100:
            raise ValueError("observed gripper is outside [0, 100]")
        observation.update({"observation.state": positions, "task": task})
        return self.backend.preprocess(observation)

    def predict(self, prepared_observation):
        return self.backend.predict_action_chunk(prepared_observation)

    def decode_actions(self, policy_chunk) -> Chunk:
        # The upstream auto action space has already removed its internal padding.
        batch_one_chunk(policy_chunk, self.contract.chunk_size)
        decoded = batch_one_chunk(self.backend.postprocess(policy_chunk), self.contract.chunk_size)
        for index, action in enumerate(decoded):
            for joint, value, (lower, upper) in zip(JOINTS, action, self.contract.joint_bounds, strict=True):
                if not lower <= value <= upper:
                    raise ValueError(f"row {index} {joint} outside [{lower}, {upper}]")
        return decoded

    def metadata(self) -> dict:
        return {
            "artifact_id": self.contract.artifact_id,
            "camera_keys": dict(zip(VIEWS, self.contract.camera_keys, strict=True)),
            "joint_order": JOINTS,
            "action_mode": "auto",
            "output": "absolute degrees x5, gripper [0, 100]",
            "chunk_size": self.contract.chunk_size,
            "n_action_steps": self.contract.n_action_steps,
            "action_fps": self.contract.action_fps,
            "domain_id": self.contract.domain_id,
        }

    def chunk_boundary_delta(self, previous: Chunk, current: Chunk) -> Vector:
        """Per-axis absolute difference after the preceding execution prefix."""
        last_executed = previous[self.contract.n_action_steps - 1]
        return tuple(abs(b - a) for a, b in zip(last_executed, current[0], strict=True))
