"""Reconstruct the trained action rows from the recorded episode map."""

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path


JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
TRIMMED_LENGTHS = {
    ("a_to_red_20261102_210433", 39): 455,
    ("a_to_blue_20261102_212958", 0): 516,
}
EXPECTED = {"red": (48, 22752), "green": (45, 21367), "blue": (44, 19432)}


def calculate(base, mapping):
    import numpy as np
    import pyarrow.parquet as parquet

    with mapping.open(encoding="utf-8", newline="") as stream:
        episodes = list(csv.DictReader(stream))
    if [int(row["merged_episode_index"]) for row in episodes] != list(range(137)):
        raise ValueError("학습 회차 매핑 불일치")
    by_source = defaultdict(dict)
    for row in episodes:
        source = row["source_repo_id"].split("/")[-1]
        number = int(row["source_episode_index"])
        if number in by_source[source]:
            raise ValueError("원본 회차 중복")
        by_source[source][number] = row["color"]
    changes, speeds = [], []
    counts, frames = Counter(), Counter()
    evidence = {}
    for source, selection in by_source.items():
        root = base / source
        info = json.loads((root / "meta/info.json").read_text(encoding="utf-8"))
        feature = info["features"]["action"]
        if info["fps"] != 30 or feature["shape"] != [6] or feature["names"] != [name + ".pos" for name in JOINTS]:
            raise ValueError(f"{source} 주기 또는 action 관절 순서 불일치")
        found = defaultdict(list)
        for file in sorted((root / "data").rglob("*.parquet")):
            evidence[f"{source}/{file.relative_to(root).as_posix()}"] = hashlib.sha256(file.read_bytes()).hexdigest()
            for batch in parquet.ParquetFile(file).iter_batches(columns=["episode_index", "frame_index", "timestamp", "action"]):
                for row in batch.to_pylist():
                    if row["episode_index"] in selection:
                        found[row["episode_index"]].append(row)
        if set(found) != set(selection):
            raise ValueError(f"{source} 선택된 회차 누락")
        for number, color in selection.items():
            rows = sorted(found[number], key=lambda row: row["frame_index"])
            if [row["frame_index"] for row in rows] != list(range(len(rows))):
                raise ValueError(f"{source} {number} 프레임 순서 불일치")
            length = TRIMMED_LENGTHS.get((source, number))
            if length is not None:
                if len(rows) not in (length, length + 1):
                    raise ValueError(f"{source} {number} 보정 기록과 길이 불일치")
                rows = rows[:length]
            actions = np.array([row["action"] for row in rows], dtype=float)
            times = np.array([row["timestamp"] for row in rows], dtype=float)
            dt = np.diff(times)
            if (len(rows) < 2 or actions.shape != (len(rows), 6)
                or not np.isfinite(actions).all() or not np.isfinite(times).all()
                or not np.allclose(dt, 1 / 30, atol=.001, rtol=0)):
                raise ValueError(f"{source} {number} 비정상 시계열")
            delta = np.abs(np.diff(actions, axis=0))
            changes.append(delta)
            speeds.append(delta / dt[:, None])
            counts[color] += 1
            frames[color] += len(rows)
    if {color: (counts[color], frames[color]) for color in counts} != EXPECTED:
        raise ValueError(f"학습본 집계 불일치 {counts} {frames}")
    change, speed = np.concatenate(changes), np.concatenate(speeds)

    def describe(values):
        quantiles = np.percentile(values, [50, 95, 99])
        return dict(zip(("p50", "p95", "p99", "max"), [*map(float, quantiles), float(values.max())]))

    joints = {}
    for index, name in enumerate(JOINTS):
        magnitude = describe(change[:, index])
        velocity = describe(speed[:, index])
        joints[name] = dict(
            unit="degree" if index < 5 else "gripper_point_0_100",
            per_frame=magnitude, per_second=velocity,
            above_2_per_frame=int(np.count_nonzero(change[:, index] > 2)),
            above_20_per_second=int(np.count_nonzero(speed[:, index] > 20)),
            requested_limit=min(60., 1.2 * velocity["p99"]) if index < 5 else None,
        )
    return dict(episodes=sum(counts.values()), frames=sum(frames.values()), fps=30,
                pairs=len(change), counts=counts, frames_by_color=frames,
                mapping_sha256=hashlib.sha256(mapping.read_bytes()).hexdigest(),
                parquet_sha256=evidence, joints=joints)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=Path.home()/".cache/huggingface/lerobot/Supermassive111")
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = calculate(args.datasets, args.mapping)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(report, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
