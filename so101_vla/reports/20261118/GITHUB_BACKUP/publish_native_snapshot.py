#!/usr/bin/env python3
"""Create, but never push, a source snapshot commit using a temporary Git index.

Usage:
  python reports/20261118/GITHUB_BACKUP/publish_native_snapshot.py \
    --source <full-local-source-commit> --expected-remote-head <full-remote-head>

Requires the expected remote commit object to have been fetched locally. The
helper reads origin/vla/live-202611 through ls-remote, but does not fetch, push,
checkout, commit the real index, change refs, or modify the working tree. It adds
the native source as a second parent, preserving original commit identities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

PREFIX = "so101_vla/"
BRANCH = "vla/live-202611"
TARGET = "Phjrab/mediflow-robotics"
GENERATED = {PREFIX + "PUBLISH_MANIFEST.json", PREFIX + "PUBLISH_RECEIPT.json"}


def git(repo: Path, *args: str, input: bytes | None = None,
        env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], input=input,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, check=False)
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: " + result.stderr.decode("utf-8", "replace"))
    return result.stdout


def full_commit(repo: Path, value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise ValueError("Use a full 40-character commit SHA.")
    resolved = git(repo, "rev-parse", "--verify", value + "^{commit}").decode().strip()
    if resolved.lower() != value.lower():
        raise ValueError("Expected a commit object, not a tag object.")
    return resolved


def entries(repo: Path, ref: str) -> dict[str, tuple[str, str]]:
    found = {}
    for record in git(repo, "ls-tree", "-rz", "--full-tree", ref).split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, sha = metadata.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Unsupported nonregular file: {name!r} ({mode}, {kind})")
        found[name.decode("utf-8")] = (mode, sha)
    return found


def blobs(repo: Path, object_ids: list[str]) -> dict[str, bytes]:
    if not object_ids:
        return {}
    ordered = list(dict.fromkeys(object_ids))
    packed = git(repo, "cat-file", "--batch", input=("\n".join(ordered) + "\n").encode())
    cursor = 0
    result = {}
    for expected in ordered:
        end = packed.index(b"\n", cursor)
        sha, kind, count = packed[cursor:end].decode().split()
        size = int(count)
        if sha != expected or kind != "blob":
            raise ValueError(f"Unexpected Git object: {sha} {kind}")
        result[sha] = packed[end + 1:end + 1 + size]
        cursor = end + 2 + size
    return result


def remote_head(repo: Path, remote: str) -> str:
    url = git(repo, "remote", "get-url", remote).decode().strip()
    allowed = {"https://github.com/" + TARGET + ".git",
               "https://github.com/" + TARGET,
               "git@github.com:" + TARGET + ".git"}
    if url not in allowed:
        raise ValueError("Remote must identify the authorized Phjrab/mediflow-robotics repository.")
    output = git(repo, "ls-remote", "--exit-code", remote, "refs/heads/" + BRANCH).decode().splitlines()
    if len(output) != 1:
        raise ValueError("The authorized destination branch was not uniquely resolved.")
    sha, ref = output[0].split()
    if ref != "refs/heads/" + BRANCH:
        raise ValueError("Unexpected remote ref.")
    return sha


def verify_source_path(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe source path: {name}")
    if name.startswith(("runs/", "_archive/", ".git/", ".ssh/", ".aws/", "history/")):
        raise ValueError(f"Excluded or protected source path: {name}")
    if path.name == "so-101.json" or path.suffix.lower() in {
        ".safetensors", ".pt", ".pth", ".mp4", ".avi", ".mov", ".mkv", ".jpg", ".jpeg", ".bundle"
    }:
        raise ValueError(f"Excluded raw/model/calibration source: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-remote-head", required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--message", default="Publish validated native SO101 VLA source snapshot")
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    source = full_commit(repo, args.source)
    expected = full_commit(repo, args.expected_remote_head)
    if source == expected:
        raise ValueError("Source must be the native project commit, not the wrapper snapshot commit.")
    if remote_head(repo, args.remote) != expected:
        raise ValueError("Remote head changed. Inspect it before preparing another candidate.")

    source_entries = entries(repo, source)
    remote_entries = entries(repo, expected)
    for name in source_entries:
        verify_source_path(name)
    source_data = blobs(repo, [sha for mode, sha in source_entries.values()])
    secret_patterns = [
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{40,})"),
        re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
        re.compile(rb"\btskey-(?:auth|api)-[A-Za-z0-9_-]{20,}"),
        re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ]
    manifest_files = []
    for name, (mode, sha) in sorted(source_entries.items()):
        data = source_data[sha]
        if len(data) > 3_000_000:
            raise ValueError(f"Source larger than reviewed snapshot ceiling: {name}")
        if b"\0" not in data and any(pattern.search(data) for pattern in secret_patterns):
            raise ValueError(f"Credential pattern found in {name}; inspect before publication.")
        manifest_files.append({"path": PREFIX + name, "mode": mode, "git_blob_sha1": sha,
                               "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})

    manifest_path = PREFIX + "PUBLISH_MANIFEST.json"
    previous_source_paths: set[str] = set()
    if manifest_path in remote_entries:
        previous = json.loads(git(repo, "cat-file", "blob", remote_entries[manifest_path][1]))
        for record in previous.get("files", []):
            name = record.get("path", "")
            if isinstance(name, str) and name.startswith(PREFIX) and not name.startswith(PREFIX + "history/"):
                previous_source_paths.add(name)
    updates = {PREFIX + name: pair for name, pair in source_entries.items()}
    removals = sorted(previous_source_paths - set(updates) - GENERATED)
    manifest = {
        "schema": "so101.github.native-source-snapshot.v1", "status": "NATIVE_COMMIT_CANDIDATE",
        "target": TARGET, "branch": BRANCH, "source_commit": source,
        "expected_remote_parent": expected, "source_tag_commit":
            git(repo, "rev-parse", "pre-raw-20261118^{commit}").decode().strip(),
        "native_history": "Preserved as real Git parents and refs, not dependent on split-bundle upload",
        "secret_text_pattern_matches": 0, "removed_previous_source_paths": removals,
        "files": manifest_files,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    manifest_sha = git(repo, "hash-object", "-w", "--stdin", input=manifest_bytes).decode().strip()
    updates[manifest_path] = ("100644", manifest_sha)

    scratch = repo / "reports/20261118/GITHUB_BACKUP/native_snapshot_tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    if not scratch.resolve().is_relative_to(repo):
        raise ValueError("Temporary index must stay inside this workspace.")
    with tempfile.TemporaryDirectory(prefix="index-", dir=scratch) as tempdir:
        resolved_temp = Path(tempdir).resolve()
        if not resolved_temp.is_relative_to(scratch.resolve()):
            raise ValueError("Unexpected temporary directory location.")
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(resolved_temp / "index")
        git(repo, "read-tree", expected + "^{tree}", env=env)
        records = [f"0 {'0'*40}\t{name}".encode("utf-8") + b"\0" for name in removals]
        records += [f"{mode} {sha}\t{name}".encode("utf-8") + b"\0"
                    for name, (mode, sha) in sorted(updates.items())]
        git(repo, "update-index", "-z", "--index-info", input=b"".join(records), env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()

    created = entries(repo, tree)
    for path, pair in remote_entries.items():
        if not path.startswith(PREFIX) or path.startswith(PREFIX + "history/"):
            if created.get(path) != pair:
                raise AssertionError(f"Protected existing content changed: {path}")
    for path, pair in updates.items():
        if created.get(path) != pair:
            raise AssertionError(f"Source snapshot mismatch: {path}")
    if remote_head(repo, args.remote) != expected:
        raise ValueError("Remote changed while preparing the tree; no commit/ref was created.")
    message = (args.message + "\n\nNative source: " + source
               + "\nExpected remote parent: " + expected
               + "\nTemporary index only; existing root and history preserved. No push performed.\n")
    commit = git(repo, "commit-tree", tree, "-p", expected, "-p", source,
                 input=message.encode("utf-8")).decode().strip()
    print(json.dumps({"commit_sha": commit, "tree_sha": tree, "parents": [expected, source],
                      "source_files": len(source_entries), "removed_stale_source_files": len(removals),
                      "remote_ref_modified": False, "main_modified": False,
                      "real_index_or_worktree_modified": False, "push_performed": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
