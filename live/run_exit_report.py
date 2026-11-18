"""Connect one terminal invocation to its own run summary after tmux exits."""
import json
import os
import sys
from pathlib import Path
from loop_timing import terminal_lines


def record_run_directory(directory):
    receipt = os.environ.get('SO101_RUN_RECEIPT')
    if receipt:
        (Path(receipt) / 'run_path').write_text(str(directory.resolve()), encoding='utf-8')


def describe(summary):
    status = str(summary.get('status') or 'EXIT')
    reason = str(summary.get('reason') or '실행 완료')
    prefix = '홈 이동 후 위치 확인 실패: '
    if reason.startswith(prefix):
        try:
            details = json.loads(reason[len(prefix):])
            pairs = [
                f"{joint} {row['present_ticks']}/{row['target_ticks']}"
                for joint, row in details['joints'].items() if not row['converged']
            ]
            mismatch = details.get('active_target_mismatch_ticks') or {}
            if mismatch:
                pairs.append('목표 읽기 불일치 '+','.join(mismatch))
            reason = 'home ' + (' · '.join(pairs) or '0.5초 안정 미확인')
        except (ValueError, KeyError, TypeError):
            reason = '홈 이동 후 위치 확인 실패'
    return ' '.join(f'{status} {reason}'.split())[:400]


def report(receipt):
    run = receipt
    try:
        run = Path((receipt / 'run_path').read_text(encoding='utf-8').strip())
        result = json.loads((run / 'summary.json').read_text(encoding='utf-8'))
        if not isinstance(result, dict):
            raise ValueError('invalid summary')
        summary = describe(result)
    except (OSError, ValueError):
        try:
            summary = (receipt / 'end_stage').read_text(encoding='utf-8').strip()
        except OSError:
            summary = 'tmux 시작 오류 또는 세션 분리'
    print(summary)
    print(f'실행 폴더 {run}')
    if 'result' in locals() and isinstance(result, dict):
        for line in terminal_lines(result.get('loop_timing')):
            print(line)
        recording = result.get('grasp_recording') or {}
        if recording:
            times = ', '.join(f"{e['time_s']:.3f}초" for e in recording.get('events', [])) or '없음'
            print(f"집기 이벤트 {recording.get('event_count',0)}회 · READY 이후 {times}")


if __name__ == '__main__':
    report(Path(sys.argv[1]))
