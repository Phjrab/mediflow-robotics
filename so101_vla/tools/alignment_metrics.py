"""Image-plane alignment of fixed board markers; no robot/model dependencies."""
import math
import statistics

FIXED_BOARD_IDS = (0, 1, 2, 3)


def compare_markers(reference, current, target_px=20):
    common = sorted(set(reference) & set(current) & set(FIXED_BOARD_IDS))
    result = dict(common_ids=common, missing_ids=sorted(set(FIXED_BOARD_IDS)-set(common)),
                  target_px=target_px, position_difference_px=None, rotation_deg=None,
                  shift_x_px=None, shift_y_px=None, scale=None, within_target=None,
                  method='fixed_board_ids_0_3_raw_corners_and_similarity_fit')
    if len(common) < 2:
        return {**result, 'status': 'insufficient_markers'}
    a = [tuple(map(float, point)) for marker in common for point in reference[marker]]
    b = [tuple(map(float, point)) for marker in common for point in current[marker]]
    if len(a) != len(b) or len(a) != 4*len(common):
        raise ValueError('고정 마커 모서리 수 불일치')
    if not all(math.isfinite(value) for point in a+b for value in point):
        raise ValueError('비정상 마커 좌표')
    ca = [statistics.mean(point[k] for point in a) for k in (0, 1)]
    cb = [statistics.mean(point[k] for point in b) for k in (0, 1)]
    dot = cross = energy = 0.0
    for p, q in zip(a, b):
        x, y = p[0]-ca[0], p[1]-ca[1]
        u, v = q[0]-cb[0], q[1]-cb[1]
        dot += x*u+y*v
        cross += x*v-y*u
        energy += x*x+y*y
    if energy <= 0:
        return {**result, 'status': 'degenerate_markers'}
    difference = statistics.median(math.hypot(q[0]-p[0], q[1]-p[1]) for p, q in zip(a, b))
    return {**result, 'status': 'measured', 'position_difference_px': difference,
            'rotation_deg': math.degrees(math.atan2(cross, dot)),
            'shift_x_px': statistics.median(q[0]-p[0] for p, q in zip(a, b)),
            'shift_y_px': statistics.median(q[1]-p[1] for p, q in zip(a, b)),
            'scale': math.hypot(dot, cross)/energy, 'within_target': difference <= target_px}


def detect_markers(rgb):
    import cv2
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    return {} if ids is None else {
        int(marker): points.reshape(4, 2).tolist() for marker, points in zip(ids.flatten(), corners)
        if int(marker) in FIXED_BOARD_IDS}


def deny_serial(event, args):
    if event != 'open' or not args:
        return
    import os
    if isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0])
        if path.startswith(('/dev/tty', '/dev/serial/')):
            raise RuntimeError('카메라 정렬 도구: serial 열기 금지')
