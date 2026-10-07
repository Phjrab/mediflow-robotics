"""Read-only check of uploaded asset sizes and GitHub-reported SHA-256."""

import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--tag", default="mediflow-archive-20261007")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    repo = manifest["repo"]
    releases = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repo}/releases?per_page=100"], text=True))
    matches = [row for row in releases if row["tag_name"] == args.tag]
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one authorized release with this tag")
    release = matches[0]
    actual = {row["name"]: row for row in release["assets"]}
    failures = []
    for expected in manifest["assets"]:
        row = actual.get(expected["file"])
        if row is None or row.get("state") != "uploaded":
            failures.append({"file": expected["file"], "error": "missing/not uploaded"})
        elif row["size"] != expected["bytes"]:
            failures.append({"file": expected["file"], "error": "size mismatch"})
        elif row.get("digest") != "sha256:" + expected["sha256"]:
            failures.append({"file": expected["file"], "error": "digest missing/mismatch"})
    print(json.dumps({"repo": repo, "tag": args.tag, "draft": release["draft"],
                      "expected_assets": len(manifest["assets"]),
                      "remote_assets": len(actual), "failures": failures},
                     ensure_ascii=False, indent=2))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
