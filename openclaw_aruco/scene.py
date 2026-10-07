"""Read-only ArUco scene inspection.

This module intentionally has no robot, serial, torque, or motion imports.  It
converts marker and fixed alignment-guide image points to an ArUco-defined
table frame and reports why physical motion remains blocked.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


TASK_NAMES = ("A", "B", "C")


def load_config(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or document.get("unit") != "mm":
        raise ValueError("workspace config must use schema_version=1 and unit=mm")
    mode = document.get("mode", {})
    if mode.get("dry_run") is not True:
        raise ValueError("dry_run must remain enabled")
    if mode.get("robot_enabled") is not False or mode.get("motion_authorized") is not False:
        raise ValueError("scene inspection refuses robot-enabled configurations")

    reference = document.get("markers", {}).get("reference_centers_mm")
    if not isinstance(reference, dict) or len(reference) < 4:
        raise ValueError("at least four reference marker centers are required")
    for marker_id, point in reference.items():
        int(marker_id)
        if not _finite_pair(point):
            raise ValueError(f"invalid reference marker center: {marker_id}")

    tasks = document.get("tasks")
    if not isinstance(tasks, dict) or tuple(sorted(tasks)) != TASK_NAMES:
        raise ValueError("tasks must contain exactly A, B, and C")
    basket_ids: set[int] = set()
    for name in TASK_NAMES:
        task = tasks[name]
        if task.get("pickup_source") != "live_cap_detection_fixed_order_cba":
            raise ValueError(f"{name}.pickup_source must use live bottle detection")
        marker_id = task.get("basket_marker_id")
        if isinstance(marker_id, bool) or not isinstance(marker_id, int):
            raise ValueError(f"{name}.basket_marker_id must be an integer")
        basket_ids.add(marker_id)
    if len(basket_ids) != len(TASK_NAMES):
        raise ValueError("A/B/C basket marker IDs must be unique")
    detector = document.get("bottle_detector", {})
    if detector.get("type") != "hough_cap_circles":
        raise ValueError("bottle_detector.type must be hough_cap_circles")
    roi = detector.get("roi_xyxy")
    if (
        not isinstance(roi, list)
        or len(roi) != 4
        or any(isinstance(item, bool) or not isinstance(item, int) for item in roi)
        or not (roi[0] < roi[2] and roi[1] < roi[3])
    ):
        raise ValueError("bottle_detector.roi_xyxy must be a valid integer rectangle")
    if detector.get("expected_count") != 3:
        raise ValueError("bottle detector must require exactly three bottles")
    if detector.get("fixed_left_to_right_identity") != ["C", "B", "A"]:
        raise ValueError("bottle identity order must be exactly C, B, A")
    ranges = detector.get("fixed_identity_x_ranges_px")
    if not isinstance(ranges, dict) or set(ranges) != {"A", "B", "C"}:
        raise ValueError("bottle detector must define fixed A/B/C x ranges")
    for identity in ("C", "B", "A"):
        bounds = ranges[identity]
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in bounds)
            or bounds[0] >= bounds[1]
            or bounds[0] < roi[0]
            or bounds[1] > roi[2]
        ):
            raise ValueError(f"invalid fixed x range for bottle {identity}")
    return document


def _finite_pair(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(not isinstance(item, bool) and isinstance(item, (int, float)) for item in value)
        and bool(np.isfinite(np.asarray(value, dtype=np.float64)).all())
    )


def _aruco_detector(dictionary_name: str):
    dictionary_id = getattr(cv2.aruco, dictionary_name)
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    if hasattr(cv2.aruco, "ArucoDetector"):
        parameters = cv2.aruco.DetectorParameters()
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        detector = cv2.aruco.ArucoDetector(dictionary, parameters)
        return detector.detectMarkers
    parameters = cv2.aruco.DetectorParameters_create()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return lambda image: cv2.aruco.detectMarkers(image, dictionary, parameters=parameters)


def _project(homography: np.ndarray, point: list[float] | np.ndarray) -> list[float]:
    source = np.asarray(point, dtype=np.float64).reshape(1, 1, 2)
    projected = cv2.perspectiveTransform(source, homography).reshape(2)
    return [round(float(projected[0]), 3), round(float(projected[1]), 3)]


def _detect_bottle_caps(image: np.ndarray, config: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[list[float]]]:
    detector = config["bottle_detector"]
    x1, y1, x2, y2 = detector["roi_xyxy"]
    if x1 < 0 or y1 < 0 or x2 > image.shape[1] or y2 > image.shape[0]:
        raise ValueError("bottle detector ROI is outside the image")
    gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    params = detector["hough"]
    def hough(param2: float) -> list[list[float]]:
        circles = cv2.HoughCircles(
            gray,
            cv2.HOUGH_GRADIENT,
            dp=float(params["dp"]),
            minDist=float(params["min_dist_px"]),
            param1=float(params["param1"]),
            param2=param2,
            minRadius=int(params["min_radius_px"]),
            maxRadius=int(params["max_radius_px"]),
        )
        return [] if circles is None else [
            [round(float(cx + x1), 3), round(float(cy + y1), 3), round(float(radius), 3)]
            for cx, cy, radius in circles[0]
        ]

    candidates = hough(float(params["param2"]))
    if len(candidates) != detector["expected_count"] and "fallback_param2" in params:
        fallback_candidates = hough(float(params["fallback_param2"]))
        selected = []
        for identity in detector["fixed_left_to_right_identity"]:
            low, high = detector["fixed_identity_x_ranges_px"][identity]
            in_column = [candidate for candidate in fallback_candidates if low <= candidate[0] < high]
            if not in_column:
                selected = []
                break
            selected.append(max(in_column, key=lambda candidate: candidate[2]))
        if len(selected) == detector["expected_count"]:
            candidates = selected
    candidates.sort(key=lambda item: item[0])
    detections: dict[str, dict[str, Any]] = {}
    if len(candidates) == detector["expected_count"]:
        for identity, (cx, cy, radius) in zip(detector["fixed_left_to_right_identity"], candidates):
            detections[identity] = {
                "cap_center_pixel": [cx, cy],
                "cap_radius_px": radius,
                "identity_basis": "fixed_left_to_right_order_C_B_A",
            }
    return detections, candidates


def inspect_scene(image_path: Path, config_path: Path) -> tuple[dict[str, Any], np.ndarray]:
    config = load_config(config_path)
    payload = image_path.read_bytes()
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to decode image: {image_path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detect = _aruco_detector(config["markers"].get("dictionary", "DICT_4X4_50"))
    corners, ids, rejected = detect(gray)
    marker_centers = (
        {}
        if ids is None
        else {
            int(marker_id): marker_corners.reshape(4, 2).mean(axis=0)
            for marker_corners, marker_id in zip(corners, ids.flatten())
        }
    )
    bottle_detections, bottle_candidates = _detect_bottle_caps(image, config)
    detected_ids = sorted(marker_centers)
    reference_centers = {
        int(marker_id): np.asarray(point, dtype=np.float64)
        for marker_id, point in config["markers"]["reference_centers_mm"].items()
    }
    reference_ids = sorted(reference_centers)
    missing_reference_ids = sorted(set(reference_ids) - set(detected_ids))

    homography = None
    reference_rms_mm = None
    if not missing_reference_ids:
        image_points = np.asarray([marker_centers[item] for item in reference_ids], dtype=np.float64)
        table_points = np.asarray([reference_centers[item] for item in reference_ids], dtype=np.float64)
        homography, _ = cv2.findHomography(image_points, table_points, method=0)
        if homography is not None:
            predicted = cv2.perspectiveTransform(image_points.reshape(-1, 1, 2), homography).reshape(-1, 2)
            reference_rms_mm = float(np.sqrt(np.mean(np.sum((predicted - table_points) ** 2, axis=1))))

    overlay = image.copy()
    if ids is not None:
        cv2.aruco.drawDetectedMarkers(overlay, corners, ids)
    tasks: dict[str, Any] = {}
    missing_basket_ids: list[int] = []
    for name in TASK_NAMES:
        task_config = config["tasks"][name]
        basket_id = int(task_config["basket_marker_id"])
        bottle = bottle_detections.get(name)
        if basket_id not in marker_centers:
            missing_basket_ids.append(basket_id)
        pickup_pixel = None if bottle is None else bottle["cap_center_pixel"]
        pickup_xy = None if homography is None or pickup_pixel is None else _project(homography, pickup_pixel)
        basket_xy = (
            None
            if homography is None or basket_id not in marker_centers
            else _project(homography, marker_centers[basket_id])
        )
        tasks[name] = {
            "identity": task_config["identity"],
            "pickup_source": task_config["pickup_source"],
            "pickup_pixel": pickup_pixel,
            "pickup_table_xy_mm": pickup_xy,
            "pickup_coordinate_status": "UNVALIDATED_CAP_PROJECTION" if pickup_xy is not None else "UNAVAILABLE",
            "basket_marker_id": basket_id,
            "basket_table_xy_mm": basket_xy,
            "robot_base_pickup_xyz_mm": None,
            "robot_base_basket_xyz_mm": None,
        }
        if pickup_pixel is not None:
            pickup_px = tuple(round(value) for value in pickup_pixel)
            cv2.circle(overlay, pickup_px, round(float(bottle["cap_radius_px"])), (0, 255, 255), 2)
            cv2.putText(
                overlay,
                f"{name} LIVE",
                (pickup_px[0] + 7, pickup_px[1] - 7),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )

    blocked_reasons = list(config.get("motion_blocked_reasons", []))
    if missing_reference_ids:
        blocked_reasons.insert(0, f"missing reference marker IDs: {missing_reference_ids}")
    if missing_basket_ids:
        blocked_reasons.insert(0, f"missing basket marker IDs: {sorted(set(missing_basket_ids))}")
    if homography is None and not missing_reference_ids:
        blocked_reasons.insert(0, "failed to compute table homography")
    if len(bottle_detections) != len(TASK_NAMES):
        blocked_reasons.insert(
            0,
            f"expected exactly 3 bottle cap circles but found {len(bottle_candidates)}",
        )

    dry_run_ready = homography is not None and not missing_basket_ids and len(bottle_detections) == len(TASK_NAMES)
    report = {
        "schema_version": 1,
        "status": "READY_FOR_DRY_RUN" if dry_run_ready else "BLOCKED_SCENE_INPUT",
        "image": str(image_path),
        "image_sha256": hashlib.sha256(payload).hexdigest(),
        "frame_size": {"width": image.shape[1], "height": image.shape[0]},
        "detected_marker_ids": detected_ids,
        "rejected_marker_candidates": len(rejected),
        "reference": {
            "required_ids": reference_ids,
            "missing_ids": missing_reference_ids,
            "homography_ready": homography is not None,
            "rms_mm": None if reference_rms_mm is None else round(reference_rms_mm, 6),
        },
        "bottle_detection": {
            "method": "hough_cap_circles",
            "roi_xyxy": config["bottle_detector"]["roi_xyxy"],
            "expected_count": 3,
            "candidate_count": len(bottle_candidates),
            "fixed_left_to_right_identity": ["C", "B", "A"],
            "detections": bottle_detections,
            "depth_validated": False,
        },
        "tasks": tasks,
        "robot_transform_ready": config["transforms"].get("T_B_W") is not None,
        "robot_limits_ready": config.get("robot_limits_mm") is not None,
        "blocked_reasons": blocked_reasons,
        "robot_enabled": False,
        "motion_authorized": False,
    }
    return report, overlay
