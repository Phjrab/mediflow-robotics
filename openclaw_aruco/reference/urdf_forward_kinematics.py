#!/usr/bin/env python3
"""Compute dry-run URDF forward kinematics without opening a robot bus."""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Joint:
    name: str
    kind: str
    parent: str
    child: str
    origin: np.ndarray
    axis: np.ndarray


def vector(text: str | None, default: tuple[float, float, float]) -> np.ndarray:
    values = default if text is None else tuple(float(item) for item in text.split())
    if len(values) != 3 or not np.isfinite(values).all():
        raise ValueError("URDF vector must contain three finite values")
    return np.asarray(values, dtype=np.float64)


def rpy_rotation(rpy: np.ndarray) -> np.ndarray:
    roll, pitch, yaw = rpy
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]], dtype=np.float64)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]], dtype=np.float64)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]], dtype=np.float64)
    return rz @ ry @ rx


def homogeneous(rotation: np.ndarray | None = None, translation: np.ndarray | None = None) -> np.ndarray:
    result = np.eye(4, dtype=np.float64)
    if rotation is not None:
        result[:3, :3] = rotation
    if translation is not None:
        result[:3, 3] = translation
    return result


def axis_rotation(axis: np.ndarray, angle: float) -> np.ndarray:
    norm = float(np.linalg.norm(axis))
    if norm <= 0:
        raise ValueError("revolute joint axis must be non-zero")
    x, y, z = axis / norm
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]], dtype=np.float64)
    return np.eye(3) + math.sin(angle) * skew + (1 - math.cos(angle)) * (skew @ skew)


class UrdfKinematics:
    def __init__(self, joints: list[Joint]) -> None:
        self.by_child = {joint.child: joint for joint in joints}
        if len(self.by_child) != len(joints):
            raise ValueError("URDF child links must have a single parent joint")

    @classmethod
    def from_file(cls, path: Path) -> "UrdfKinematics":
        root = ET.parse(path).getroot()
        joints = []
        for element in root.findall("joint"):
            name = element.attrib["name"]
            kind = element.attrib["type"]
            parent = element.find("parent")
            child = element.find("child")
            if parent is None or child is None:
                raise ValueError(f"joint {name} is missing parent or child")
            origin_element = element.find("origin")
            xyz = vector(None if origin_element is None else origin_element.get("xyz"), (0, 0, 0))
            rpy = vector(None if origin_element is None else origin_element.get("rpy"), (0, 0, 0))
            axis_element = element.find("axis")
            axis = vector(None if axis_element is None else axis_element.get("xyz"), (1, 0, 0))
            joints.append(Joint(
                name=name,
                kind=kind,
                parent=parent.attrib["link"],
                child=child.attrib["link"],
                origin=homogeneous(rpy_rotation(rpy), xyz),
                axis=axis,
            ))
        return cls(joints)

    def chain(self, root_link: str, target_link: str) -> list[Joint]:
        chain = []
        current = target_link
        visited = set()
        while current != root_link:
            if current in visited:
                raise ValueError("URDF joint graph contains a cycle")
            visited.add(current)
            joint = self.by_child.get(current)
            if joint is None:
                raise ValueError(f"no chain from {root_link} to {target_link}")
            chain.append(joint)
            current = joint.parent
        return list(reversed(chain))

    def forward(self, joint_positions_rad: dict[str, float], root_link: str, target_link: str) -> np.ndarray:
        result = np.eye(4, dtype=np.float64)
        for joint in self.chain(root_link, target_link):
            result = result @ joint.origin
            if joint.kind in {"revolute", "continuous"}:
                if joint.name not in joint_positions_rad:
                    raise ValueError(f"missing joint position: {joint.name}")
                angle = float(joint_positions_rad[joint.name])
                if not math.isfinite(angle):
                    raise ValueError(f"non-finite joint position: {joint.name}")
                result = result @ homogeneous(axis_rotation(joint.axis, angle))
            elif joint.kind == "prismatic":
                if joint.name not in joint_positions_rad:
                    raise ValueError(f"missing joint position: {joint.name}")
                distance = float(joint_positions_rad[joint.name])
                result = result @ homogeneous(translation=joint.axis * distance)
            elif joint.kind != "fixed":
                raise ValueError(f"unsupported joint type: {joint.kind}")
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urdf", required=True, type=Path)
    parser.add_argument("--joints", required=True, type=Path, help="JSON with unit=rad and joints object")
    parser.add_argument("--root-link", default="base")
    parser.add_argument("--target-link", default="gripper")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    document = json.loads(args.joints.read_text(encoding="utf-8"))
    if document.get("unit") != "rad" or not isinstance(document.get("joints"), dict):
        raise ValueError("joint JSON must contain unit=rad and a joints object")
    transform = UrdfKinematics.from_file(args.urdf).forward(
        document["joints"], args.root_link, args.target_link
    )
    output = {
        "status": "FK_ONLY_REQUIRES_PHYSICAL_VALIDATION",
        "root_link": args.root_link,
        "target_link": args.target_link,
        "T_root_target_m": transform.tolist(),
        "position_mm": (transform[:3, 3] * 1000.0).tolist(),
        "robot_enabled": False,
        "motion_authorized": False,
        "note": "URDF link origin is not a verified TCP. Define and validate gripper-tip offset before T_B_W teach.",
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
