from __future__ import annotations

from pathlib import Path


def resolve_local_snapshot(model_id: str, cache_dir: Path) -> str:
    """Prefer a complete project-local snapshot and avoid writes to a user cache."""
    try:
        from huggingface_hub import snapshot_download

        return snapshot_download(
            repo_id=model_id,
            cache_dir=cache_dir,
            local_files_only=True,
        )
    except Exception:
        return model_id

