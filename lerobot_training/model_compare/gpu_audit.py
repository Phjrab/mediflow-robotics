"""Record an NVIDIA process snapshot on the training host without changing processes."""
import argparse
import csv
import datetime as dt
import os
from pathlib import Path
import select
import signal
import subprocess
import time


def smi(*args):
    result = subprocess.run(["nvidia-smi", *args], text=True, capture_output=True, check=True)
    return result.stdout


def snapshot(samples=3):
    devices = list(csv.reader(smi("--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
                                  "--format=csv,noheader,nounits").splitlines(), skipinitialspace=True))
    compute = list(csv.reader(smi("--query-compute-apps=pid,process_name,used_memory",
                                  "--format=csv,noheader,nounits").splitlines(), skipinitialspace=True))
    per_pid = {}
    names = {}
    memory = {pid: used for pid, _, used in compute}
    types = {}
    header = []
    for line in smi("pmon", "-s", "um", "-c", str(samples)).splitlines():
        fields = line.lstrip("# ").split()
        if "pid" in fields and "sm" in fields:
            header = fields
        elif header and fields and fields[0].isdigit() and len(fields) >= len(header):
            row = dict(zip(header, fields))
            if row["pid"].isdigit():
                process = row["pid"]
                per_pid.setdefault(process, []).append(row["sm"])
                names[process] = row.get("command", "?")
                types[process] = row.get("type", "?")
                if process not in memory and row.get("fb", "-").isdigit():
                    memory[process] = row["fb"]
    lines = ["", "### NVIDIA 점유 실측 · " + dt.datetime.now().astimezone().isoformat(timespec="seconds"), "",
             "| 장치 | 모델 | 총 VRAM (MiB) | 여유 VRAM (MiB) | GPU 사용률 (%) |",
             "|---|---|---:|---:|---:|"]
    lines.extend("| " + " | ".join(row) + " |" for row in devices)
    lines += ["", "| PID | 프로세스 | 유형 | VRAM (MiB) | PID별 SM 사용률 표본 (%) |",
              "|---:|---|---|---:|---|"]
    for pid, name, _ in compute:
        names[pid] = Path(name).name
    for pid in sorted(names, key=int):
        values = ", ".join(per_pid.get(pid, [])) or "미측정"
        lines.append(f"| {pid} | {names[pid]} | {types.get(pid, 'C')} | {memory.get(pid, '미측정')} | {values} |")
    if not names:
        lines.append("| — | 조회된 프로세스 없음 | — | — | — |")
    return "\n".join(lines) + "\n", {int(row[0]) for row in compute}, per_pid, types


def stale_reason(pid, run, samples):
    """Require ownership, an exact training output and observed inactivity before SIGTERM."""
    proc = Path("/proc") / str(pid)
    if proc.stat().st_uid != os.getuid() or samples != ["0"] * 3:
        return "소유자 또는 GPU 유휴 표본 조건 미충족"
    argv = [os.fsdecode(x) for x in (proc / "cmdline").read_bytes().split(b"\0") if x]
    if not any(Path(arg).name in {"lerobot-train", "lerobot_train.py", "training_worker.py"}
               or arg == "lerobot.scripts.lerobot_train" for arg in argv):
        return "LeRobot 학습 프로세스 확인 불가"
    dirs = [arg.partition("=")[2] for arg in argv if arg.startswith("--output_dir=")]
    dirs += [v for k, v in zip(argv, argv[1:]) if k == "--output_dir"]
    if not any(((proc / "cwd") / d).resolve() == run for d in dirs):
        return "지정한 실험의 출력 경로와 불일치"
    logs = {p for p in run.rglob("*") if p.is_file() and
            (p.suffix in {".log", ".csv", ".jsonl"} or "tfevents" in p.name)}
    for fd in ("1", "2"):
        target = (proc / "fd" / fd).resolve()
        if target.is_file() and (target.suffix in {".log", ".out"} or target.name == "nohup.out"):
            logs.add(target)
    checkpoints = [p for p in (run / "checkpoints").rglob("*") if p.is_file()]
    if not logs or not checkpoints:
        return "로그·체크포인트 양쪽의 갱신 시각 확인 불가"
    latest = max(p.stat().st_mtime for p in (*logs, *checkpoints))
    if time.time() - latest < 600:
        return "최근 10분 내 로그 또는 체크포인트 갱신"
    return None


def terminate_stale(pid, run, report):
    # The open descriptor binds signals to the original process even if a PID is recycled.
    with os.fdopen(os.pidfd_open(pid), "rb", buffering=0) as handle:
        report("시그널 전 재측정")
        table, compute, uses, types = snapshot()
        report(table)
        if pid not in compute or types.get(str(pid)) != "C":
            report(f"PID {pid} 유지 · 단독 계산 프로세스 확인 불가")
            return
        reason = stale_reason(pid, run, uses.get(str(pid)))
        if reason:
            report(f"PID {pid} 유지 · {reason}")
            return
        signal.pidfd_send_signal(handle.fileno(), signal.SIGTERM)
        done, _, _ = select.select([handle.fileno()], [], [], 10)
        report(f"PID {pid} 종료 확인" if done else f"PID {pid} SIGTERM 전달 · 종료 대기")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, help="Append the measured tables to STATUS.md")
    parser.add_argument("--require-free", action="store_true", help="Exit 3 when a compute PID remains")
    parser.add_argument("--stop-stale-pid", type=int, help="Check and stop this specific owned LeRobot job")
    parser.add_argument("--run-dir", type=Path, help="Exact output directory of the identified experiment")
    args = parser.parse_args()
    if (args.stop_stale_pid is None) != (args.run_dir is None):
        parser.error("--stop-stale-pid and --run-dir must be used together")

    def report(message):
        print(message)
        if args.status:
            with args.status.open("a", encoding="utf-8") as out:
                out.write(message + "\n")

    table, compute, _, _ = snapshot()
    report(table)
    if args.stop_stale_pid is not None:
        try:
            terminate_stale(args.stop_stale_pid, args.run_dir.resolve(strict=True), report)
        except FileNotFoundError:
            report("지정한 프로세스 또는 산출물을 재확인할 수 없음")
        table, compute, _, _ = snapshot()
        report(table)
    if args.require_free and compute:
        report("다른 계산 프로세스가 있어 학습 큐 대기")
        raise SystemExit(3)
