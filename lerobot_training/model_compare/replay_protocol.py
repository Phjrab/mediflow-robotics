"""Shared invariants for recorded policy replay."""
import hashlib

HORIZON = 16
HISTORICAL = "smolvla_b84b15c7"
HISTORICAL_SHA = "b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654"
RAW_VIEWS = ("end_effector", "ceiling_vertical", "ceiling_oblique")


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            sha.update(block)
    return sha.hexdigest()
