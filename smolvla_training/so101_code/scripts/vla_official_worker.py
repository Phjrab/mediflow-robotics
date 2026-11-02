"""Isolated official v0.6.0 data-only API; source dataset write and network guard."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tomllib


def protect_source(root):
    def within(path):
        if not isinstance(path, (str, bytes, os.PathLike)):
            return False
        p = Path(os.fsdecode(path)).resolve()
        return p == root or root in p.parents
    def guard(event, args):
        if event.startswith("socket.connect"):
            raise PermissionError("network disabled for dataset verification")
        if event == "open":
            path, mode, flags = args
            if isinstance(path, (str, bytes, os.PathLike)) and os.fsdecode(path).startswith(("/dev/tty", "/dev/video", "/dev/serial")):
                raise PermissionError("hardware access forbidden")
            writing = (isinstance(mode, str) and any(x in mode for x in "wax+")) or (flags is not None and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and within(path):
                raise PermissionError("source dataset is read-only")
        if event == "os.mkdir" and within(args[0]) and not Path(args[0]).is_dir():
            raise PermissionError("source mkdir forbidden")
        if event in ("os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink") and any(within(p) for p in args[:2]):
            raise PermissionError("source mutation forbidden")
    sys.addaudithook(guard)


def main():
    p = argparse.ArgumentParser()
    for key in ("root", "source", "result"):
        p.add_argument("--"+key, type=Path, required=True)
    p.add_argument("--mode", choices=("reload", "derive", "partitions"), default="reload")
    p.add_argument("--out", type=Path)
    p.add_argument("--excluded", default="[]")
    a = p.parse_args()
    root = a.root.resolve()
    protect_source(root)
    try:
        project = tomllib.loads((a.source/"pyproject.toml").read_text())["project"]
        if project["version"] != "0.6.0":
            raise ValueError("official source must be v0.6.0")
        import lerobot
        if (a.source/"src").resolve() not in Path(lerobot.__file__).resolve().parents:
            raise ValueError("wrong lerobot import source")
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        ds = LeRobotDataset("local/vla-audit", root=root, video_backend="pyav")
        sample = ds[0]
        report = {"status": "PASS", "official_source_version": project["version"],
                  "episodes": ds.num_episodes, "frames": len(ds), "sample_keys": sorted(sample)}
        if a.mode == "partitions":
            # Data-only integration, no training module / policy / optimizer imports.
            from types import SimpleNamespace
            from lerobot.configs.default import DatasetConfig
            from lerobot.datasets.factory import make_dataset
            from vla_data import Dataset, tree_hash
            from vla_train_data import train_statistics, partitioned_datasets
            before=tree_hash(root)
            cfg=SimpleNamespace(dataset=DatasetConfig(repo_id="local/vla-audit",root=str(root),video_backend="pyav"),
                trainable_config=SimpleNamespace(action_delta_indices=[0],observation_delta_indices=[0],reward_delta_indices=None),
                tolerance_s=1e-4,num_workers=0)
            train_ids=[0,2];eval_ids=[1]
            stats=train_statistics(Dataset(root),train_ids)
            train,ev=partitioned_datasets(cfg,make_dataset,train_ids,eval_ids,stats)
            import numpy as np
            assert train.episodes==train_ids and ev.episodes==eval_ids
            for data in (train,ev):
                data[0]
                for key in stats:
                    for stat in stats[key]: np.testing.assert_allclose(data.meta.stats[key][stat],stats[key][stat])
            assert tree_hash(root)==before
            report.update(train_episodes=train.episodes,eval_episodes=ev.episodes,
                train_stats_shared=True,source_unchanged=True,training="NOT_RUN")
        if a.mode == "derive":
            if a.out is None or a.out.exists() or root == a.out.resolve() or root in a.out.resolve().parents:
                raise ValueError("new output outside source required")
            excluded = set(json.loads(a.excluded))
            mapping = {old: new for new, old in enumerate(i for i in range(ds.num_episodes) if i not in excluded)}
            if not mapping or not excluded <= set(range(ds.num_episodes)):
                raise ValueError("invalid/all episode exclusion")
            from lerobot.datasets.dataset_metadata import LeRobotDatasetMetadata
            from lerobot.datasets.dataset_tools import _copy_and_reindex_data, _copy_and_reindex_episodes_metadata
            target = LeRobotDatasetMetadata.create("local/vla-derived", fps=ds.meta.fps,
                features=ds.meta.features, robot_type=ds.meta.robot_type, root=a.out, use_videos=True)
            data_meta = _copy_and_reindex_data(ds, target, mapping)
            video_meta = {}
            # Official public delete_episodes reencodes shared files. Reuse its
            # data+metadata routines but preserve original compressed video bytes
            # and PTS offsets to satisfy exact decoded sample equality.
            copied = set()
            for old, new in mapping.items():
                row = ds.meta.episodes[old]
                video_meta[new] = {k: v for k, v in row.items() if k.startswith("videos/")}
                for key in ds.meta.video_keys:
                    relative = ds.meta.get_video_file_path(old, key)
                    if str(relative) not in copied:
                        source, destination = root / relative, a.out / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                        copied.add(str(relative))
            _copy_and_reindex_episodes_metadata(ds, target, mapping, data_meta, video_meta)
            reloaded = LeRobotDataset("local/vla-derived", root=a.out, video_backend="pyav")
            reloaded[0]
            report.update(episodes=reloaded.num_episodes, frames=len(reloaded),
                          strategy="official v0.6.0 data/metadata helpers + whole video copy, original PTS retained")
        code = 0
    except Exception as exc:
        report = {"status": "FAIL", "error_type": type(exc).__name__, "reason": str(exc)[:600]}
        code = 1
    with a.result.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
