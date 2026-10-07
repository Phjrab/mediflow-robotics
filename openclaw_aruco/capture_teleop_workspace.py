"""Read-only capture of LeLab's existing teleoperation joint WebSocket.

This never opens a motor port or sends robot actions. The resulting envelope
describes observed poses only; it is not a collision-free workspace.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import socket
import struct
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


JOINT_UNITS = {
    "Rotation": "deg",
    "Pitch": "deg",
    "Elbow": "deg",
    "Wrist_Pitch": "deg",
    "Wrist_Roll": "deg",
    "Jaw": "normalized_0_100",
}


def read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    while size:
        part = sock.recv(size)
        if not part:
            raise ConnectionError("WebSocket closed")
        chunks.append(part)
        size -= len(part)
    return b"".join(chunks)


def connect_websocket(url: str) -> socket.socket:
    target = urlsplit(url)
    if target.scheme != "ws" or not target.hostname:
        raise ValueError("Only ws:// URLs are supported")
    port = target.port or 80
    path = target.path or "/"
    if target.query:
        path += "?" + target.query
    sock = socket.create_connection((target.hostname, port), timeout=5)
    sock.settimeout(2)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {target.hostname}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(4096)
        if len(response) > 16384:
            raise ConnectionError("Oversized WebSocket handshake")
    headers, rest = response.split(b"\r\n\r\n", 1)
    expected = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    )
    if not headers.startswith(b"HTTP/1.1 101") or expected.lower() not in headers.lower():
        raise ConnectionError(f"WebSocket upgrade failed: {headers[:200]!r}")
    if rest:
        # FastAPI sends no message during handshake; fail rather than discard bytes.
        raise ConnectionError("Unexpected data immediately after WebSocket handshake")
    return sock


def read_frame(sock: socket.socket) -> tuple[int, bytes]:
    first, second = read_exact(sock, 2)
    if not first & 0x80:
        raise ConnectionError("Fragmented WebSocket frame is unsupported")
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(sock, 8))[0]
    if length > 1_000_000:
        raise ConnectionError("Unexpectedly large WebSocket frame")
    mask = read_exact(sock, 4) if second & 0x80 else None
    payload = read_exact(sock, length)
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return first & 0x0F, payload


def send_pong(sock: socket.socket, payload: bytes) -> None:
    """A client control frame must be masked (RFC 6455)."""
    if len(payload) > 125:
        raise ConnectionError("Oversized WebSocket ping")
    mask = os.urandom(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    sock.sendall(bytes((0x8A, 0x80 | len(payload))) + mask + masked)


def read_teleop_active(base_url: str) -> bool:
    with urllib.request.urlopen(base_url.rstrip("/") + "/teleoperation-status", timeout=3) as res:
        return bool(json.load(res)["teleoperation_active"])


def parse_joint_update(raw: bytes) -> dict | None:
    message = json.loads(raw)
    if message.get("type") != "joint_update":
        return None
    joints = message.get("joints")
    if not isinstance(joints, dict) or set(joints) != set(JOINT_UNITS):
        raise ValueError("Incomplete joint update")
    values = {name: round(math.degrees(float(value)), 5) for name, value in joints.items()}
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("Non-finite joint position")
    return {"type": "joint_sample", "server_timestamp": message.get("timestamp"), "values": values}


def capture(base_url: str, output: Path, max_seconds: float, stop_file: Path | None = None) -> dict:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("Only http:// LeLab bases are supported")
    ws_url = f"ws://{parsed.netloc}/ws/joint-data"
    output.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    seen_active = False
    last_status = 0.0
    inactive_since = None
    samples = 0
    gaps_over_250ms = 0
    last_sample_time = None
    envelope: dict[str, dict[str, float]] = {}
    reason = "max_duration"
    with connect_websocket(ws_url) as sock, output.open("x", encoding="utf-8") as dest:
        dest.write(json.dumps({"type": "metadata", "started_utc": datetime.now(timezone.utc).isoformat(),
                               "source": ws_url, "joint_units": JOINT_UNITS,
                               "interpretation": "observed_joint_poses_not_safe_workspace"}) + "\n")
        dest.flush()
        print(f"READY {output}", flush=True)
        try:
            while time.monotonic() - start < max_seconds:
                if stop_file is not None and stop_file.exists():
                    reason = "operator_stopped"
                    break
                now = time.monotonic()
                if now - last_status >= 1.0:
                    active = read_teleop_active(base_url)
                    last_status = now
                    if active:
                        if not seen_active:
                            print("TELEOP_ACTIVE", flush=True)
                        seen_active = True
                        inactive_since = None
                    elif seen_active:
                        inactive_since = inactive_since or now
                        if now - inactive_since >= 2.0:
                            reason = "teleop_ended"
                            break
                try:
                    opcode, payload = read_frame(sock)
                except socket.timeout:
                    continue
                except ConnectionError:
                    reason = "websocket_closed"
                    break
                if opcode == 8:
                    reason = "websocket_closed"
                    break
                if opcode == 9:
                    send_pong(sock, payload)
                    continue
                if opcode != 1:
                    continue
                sample = parse_joint_update(payload)
                if sample is None or not seen_active:
                    continue
                sample["received_utc"] = datetime.now(timezone.utc).isoformat()
                dest.write(json.dumps(sample, ensure_ascii=False) + "\n")
                dest.flush()
                samples += 1
                if last_sample_time is not None and now - last_sample_time > 0.25:
                    gaps_over_250ms += 1
                last_sample_time = now
                for name, value in sample["values"].items():
                    bounds = envelope.setdefault(name, {"min": value, "max": value})
                    bounds["min"] = min(bounds["min"], value)
                    bounds["max"] = max(bounds["max"], value)
        except KeyboardInterrupt:
            reason = "operator_stopped"
        summary = {"type": "summary", "reason": reason, "samples": samples,
                   "teleop_seen": seen_active, "gaps_over_250ms": gaps_over_250ms,
                   "joint_envelope_observed_only": envelope,
                   "ended_utc": datetime.now(timezone.utc).isoformat()}
        dest.write(json.dumps(summary, ensure_ascii=False) + "\n")
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8022")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=220)
    parser.add_argument("--stop-file", type=Path,
                        help="Optional marker file checked once per second to end capture cleanly")
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 300:
        parser.error("max-seconds must be 1..300")
    capture(args.base_url, args.output, args.max_seconds, args.stop_file)


if __name__ == "__main__":
    main()
