"""Review the final second of wire/control logs without accessing any devices."""
import argparse
import json
from pathlib import Path
from recorder_bus_readonly import distribution


def analyze(rows, window=1.):
    active = {}
    reads, cycles, errors = [], [], []
    last_time = None
    for row in rows:
        event = row.get('event')
        now = row.get('monotonic_s')
        if now is not None: last_time = now
        if event == 'wire_tx':
            raw = bytes.fromhex(row.get('packet_hex', ''))
            if len(raw) >= 8 and raw[4] in (2, 0x82):
                ids = [raw[2]] if raw[4] == 2 else list(raw[7:-1])
                for mid in ids: active[mid] = dict(start=now, address=raw[5], width=raw[6])
        elif event == 'wire_rx':
            mid = row.get('expected_id')
            request = active.pop(mid, None)
            valid = (row.get('communication') == 0 and row.get('received_id') == mid
                     and row.get('checksum_valid') is True)
            if request and now is not None and request['start'] is not None:
                reads.append(dict(time=now, expected_id=mid, received_id=row.get('received_id'),
                                  communication=row.get('communication'), valid=valid,
                                  request_to_reply_s=now-request['start'], **request))
            if not valid: errors.append(row)
        elif event == 'goal_transmitted' and row.get('gap_s') is not None:
            # Historical goal records have no monotonic clock; the latest wire
            # event immediately preceding the goal is the available anchor.
            cycles.append(dict(time=last_time, gap_s=row['gap_s']))
    end = max((r['time'] for r in reads), default=last_time)
    tail = [r for r in reads if end is not None and r['time'] >= end-window]
    gaps = [r['gap_s'] for r in cycles if r['time'] is not None and end is not None
            and r['time'] >= end-window]
    return dict(window_s=window, end_monotonic_s=end,
                successful_request_to_reply_s=distribution([r['request_to_reply_s'] for r in tail if r['valid']]),
                all_request_to_reply_s=distribution([r['request_to_reply_s'] for r in tail]),
                goal_gap_s=distribution(gaps), tail_reads=tail,
                tail_errors=[r for r in errors if r.get('monotonic_s') is not None and end is not None
                             and r['monotonic_s'] >= end-window],
                cpu_usage='NOT_RECORDED_IN_HISTORICAL_WIRE_LOG',
                scope='SYNC_READ latencies include reply order; not per-motor processing time')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = {}
    for file in args.logs:
        with file.open(encoding='utf-8') as source:
            result[str(file)] = analyze(json.loads(line) for line in source if line.strip())
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__': main()
