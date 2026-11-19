"""Read-only, 640 x 480 image-plane measurements for the alignment preview."""
import math

import cv2
import numpy as np


def wrist_landmarks(rgb):
    # These two bottle silhouettes enter from the top of the reference wrist view.
    hsv = cv2.cvtColor(rgb[:160], cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, (5, 58, 38), (34, 255, 255))
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    points = []
    for index, (x, y, width, height, area) in enumerate(stats[1:], 1):
        if y != 0 or height >= mask.shape[0] or area < 900 or width < 32 or height < 18:
            continue
        rows, columns = np.nonzero(labels == index)
        bottom = rows.max()
        tip = float(np.median(columns[rows >= bottom - 3]))
        if 4 < tip < rgb.shape[1] - 5:
            points.append([tip, float(bottom)])
    if len(points) == 2:
        points.sort()
        if points[1][0] - points[0][0] > 80:
            return points
    return []


def displacement(point, reference):
    dx = float(point[0] - reference[0])
    dy = float(point[1] - reference[1])
    return dict(current_xy=list(point), reference_xy=list(reference),
                dx=dx, dy=dy, distance=math.hypot(dx, dy))


def wrist_difference(reference_points, rgb):
    current = wrist_landmarks(rgb)
    if len(reference_points) != 2 or len(current) != 2:
        return []
    return [displacement(point, baseline) for point, baseline in zip(current, reference_points)]


RIM_CORNERS = {
    'ceiling_vertical': [(280, 205), (333, 207), (331, 254), (275, 257)],
    'ceiling_oblique': [(268, 270), (326, 272), (340, 327), (279, 331)],
}


def object_markers(gray):
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), params)
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return {}
    indices = ids.flatten()
    result = {}
    for marker in (6, 9):
        matches = np.flatnonzero(indices == marker)
        if len(matches) == 1:
            result[marker] = corners[matches[0]].reshape(4, 2).mean(axis=0)
    return result


def rim_reference(rgb, role):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    points = np.array(RIM_CORNERS[role], dtype=np.float32)
    patches = [gray[y-8:y+9, x-8:x+9].copy() for x, y in RIM_CORNERS[role]]
    return object_markers(gray)[6], points, patches


def opening_center(gray, marker, reference):
    ref_marker, ref_points, patches = reference
    predicted = np.rint(ref_points + marker - ref_marker).astype(int)
    matches = []
    for (x, y), patch in zip(predicted, patches):
        if x < 19 or y < 19 or x+19 >= gray.shape[1] or y+19 >= gray.shape[0]:
            return None
        scores = cv2.matchTemplate(gray[y-19:y+20, x-19:x+20], patch, cv2.TM_CCOEFF_NORMED)
        _, best, _, (u, v) = cv2.minMaxLoc(scores)
        if best < 0.80 or not (0 < u < 22 and 0 < v < 22):
            return None
        matches.append((x-11+u, y-11+v))
    corners = np.array(matches, dtype=np.float32)
    ratio = cv2.contourArea(corners) / cv2.contourArea(ref_points)
    if not cv2.isContourConvex(corners) or not 0.76 <= ratio <= 1.25:
        return None
    if np.max(np.ptp(corners - ref_points, axis=0)) > 9:
        return None
    a, b, c, d = corners.astype(float)
    u, v = np.linalg.solve(np.column_stack((c-a, b-d)), b-a)
    if 0 < u < 1 and 0 < v < 1:
        return a + u*(c-a)
    return None


def object_positions(rgb, baselines, rim):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    markers = object_markers(gray)
    points = {'bottle_cap': markers.get(9), 'basket_tag': markers.get(6)}
    if 6 in markers:
        points['basket_opening'] = opening_center(gray, markers[6], rim)
    positions = {}
    for name, point in points.items():
        if point is not None:
            positions[name] = displacement(point.tolist(), baselines[name]['mean_xy'])
    return positions
