"""저장된 원문만 분석한다. 장치·네트워크에는 접근하지 않는다."""
import argparse
import csv
import json
import statistics
from pathlib import Path

JOINTS = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper']


def analyze(root):
    summary = {'validated_read_pairs': 0, 'profiles': {}, 'motor_register_writes': 0}
    records = []
    for path in sorted(root.rglob('*.jsonl')):
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        valid = []
        stops = []
        for row in rows:
            if row.get('event') == 'stop':
                stops.append(row)
                continue
            if row.get('event') != 'read':
                continue
            tx, rx = bytes.fromhex(row['tx_hex']), bytes.fromhex(row['rx_hex'])
            mid, address, width = row['id'], row['address'], row['width']
            assert 1 <= mid <= 6 and (address, width) in ((40, 1), (62, 1), (62, 2), (63, 1))
            assert len(tx) == 8 and tx[:7] == bytes((255, 255, mid, 4, 2, address, width))
            assert sum(tx[2:]) % 256 == 255
            assert len(rx) == width + 6 and rx[:5] == bytes((255, 255, mid, width + 2, 0))
            assert sum(rx[2:]) % 256 == 255 and row['status'] == 0 and row['vendor_error'] == 0
            assert row['value'] == rx[6 if width == 2 else 5]
            assert row['voltage_raw'] == (rx[5] if address == 62 else -1)
            summary['validated_read_pairs'] += 1
            valid.append(row)
            records.append({'file': str(path.relative_to(root)), 'joint': JOINTS[mid - 1], **row})
        if not valid and not stops:
            continue
        profile = {'read_pairs': len(valid), 'stops': stops, 'joints': {}}
        for mid in range(1, 7):
            paired = [r for r in valid if r['id'] == mid and r['phase'] == 'paired']
            if not paired:
                continue
            temps = [r['value'] for r in paired]
            volts = [r['voltage_raw'] for r in paired]
            tm, vm = statistics.median(temps), statistics.median(volts)
            peaks = [i for i, r in enumerate(paired) if r['value'] >= tm + 8]
            dips = [i for i, r in enumerate(paired) if r['voltage_raw'] <= vm - 2]
            profile['joints'][JOINTS[mid - 1]] = {
                'samples': len(paired), 'temperature_range_c': [min(temps), max(temps)],
                'temperature_median_c': tm, 'voltage_range_v': [min(volts) / 10, max(volts) / 10],
                'voltage_median_v': vm / 10,
                'temperature_spikes_ge_median_plus8': len(peaks),
                'temperature_spike_neighbors': [paired[max(0, i-1):i+2] for i in peaks],
                'voltage_dips_ge_0_2v': len(dips),
                'voltage_dip_neighbors': [paired[max(0, i-1):i+2] for i in dips],
                'same_reply_temperature_spike_and_voltage_dip': len(set(peaks) & set(dips)),
            }
        bracket = [r for r in valid if r['phase'] == 'temperature']
        if bracket:
            tm = statistics.median(r['value'] for r in bracket)
            peak_cycles = [r['cycle'] for r in bracket if r['value'] >= tm + 8]
            volts = [r['value'] / 10 for r in valid if r['phase'] in ('voltage_before', 'voltage_after')]
            profile['bracket'] = {
                'samples': len(bracket), 'temperature_range_c': [min(r['value'] for r in bracket), max(r['value'] for r in bracket)],
                'temperature_median_c': tm, 'voltage_range_v': [min(volts), max(volts)],
                'spike_cycles': [[r for r in valid if r['cycle'] == c and r['phase'] in ('temperature', 'voltage_before', 'voltage_after')] for c in peak_cycles],
            }
        profile['final_snapshot'] = [r for r in valid if r['phase'].startswith('final_')]
        summary['profiles'][str(path.relative_to(root))] = profile
    return summary, records


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    result, rows = analyze(args.root)
    with (args.root / 'CORRELATION_SUMMARY.json').open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    if rows:
        with (args.root / 'voltage_temperature.csv').open('x', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({'validated_read_pairs': result['validated_read_pairs'], 'profiles': list(result['profiles'])}))
