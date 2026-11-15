"""기존 READ 원문을 진단 필터로 재생한다. 장치·네트워크에는 접근하지 않는다."""
import argparse
import json
from pathlib import Path
import tarfile

from thermal_diagnostics import ThermalDiagnostics

JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')


def analyze(archive):
    results = []
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle.getmembers():
            if not member.isfile() or Path(member.name).name not in ('paired-selected-30.jsonl', 'paired-target-180.jsonl'):
                continue
            observer, spikes = ThermalDiagnostics(JOINTS), []
            with bundle.extractfile(member) as source:
                for line in source:
                    row = json.loads(line)
                    if row.get('event') != 'read' or row.get('phase') != 'paired':
                        continue
                    mid = row['id']
                    tx, rx = bytes.fromhex(row['tx_hex']), bytes.fromhex(row['rx_hex'])
                    if not (1 <= mid <= 6 and tx[:7] == bytes((255, 255, mid, 4, 2, 62, 2))
                            and len(tx) == 8 and sum(tx[2:]) % 256 == 255
                            and len(rx) == 8 and rx[:5] == bytes((255, 255, mid, 4, 0))
                            and sum(rx[2:]) % 256 == 255):
                        raise ValueError('기존 원문 검산 실패: ' + member.name)
                    joint, stamp = JOINTS[mid-1], row['monotonic_ns'] / 1e9
                    if rx[6] != row['value'] or rx[5] != row['voltage_raw']:
                        raise ValueError('기존 원문·디코드 불일치')
                    evidence = observer.observe_axis(joint, stamp, rx[6], rx[5]/10)
                    if evidence['suspected_spike']:
                        spikes.append(dict(joint=joint, sequence=row['sequence'], raw_c=rx[6],
                                           voltage_v=rx[5]/10, rx_hex=rx.hex(),
                                           filtered_c=evidence['filtered_c']))
            results.append(dict(source=member.name, summary=observer.summary(), spikes=spikes))
    return {'kind': 'HISTORICAL_REPLAY_NOT_NEW_HARDWARE_TEST', 'motor_register_writes': 0,
            'live': 'NOT_RUN', 'profiles': results}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    data = analyze(args.archive)
    with args.out.open('x', encoding='utf-8') as output:
        json.dump(data, output, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'profiles': len(data['profiles']),
                      'spikes': sum(len(p['spikes']) for p in data['profiles']),
                      'hardware_access': False}))
