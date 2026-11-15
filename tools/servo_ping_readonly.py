"""팔로워 ID1~6 PING 존재 확인. PING 외 전송과 모든 레지스터 쓰기를 금지한다."""
import argparse
import json
import os
from pathlib import Path
import time

from servo_thermal_readonly import FOLLOWER, LinuxReadTransport, ReadFault


def ping_packet(mid):
    if type(mid) is not int or not 1 <= mid <= 6:
        raise ValueError('PING은 ID1~6만 허용')
    head = bytes((255, 255, mid, 2, 1))
    return head + bytes(((~sum(head[2:])) & 255,))


def require_ping_packet(packet):
    if len(packet) != 6 or packet != ping_packet(packet[2]):
        raise ReadFault('PING 외 패킷 송신 금지')


def parse_ping_reply(raw, mid):
    if len(raw) != 6 or raw[:4] != bytes((255, 255, mid, 2)) or sum(raw[2:]) % 256 != 255:
        raise ReadFault('PING 응답 ID·길이·체크섬 오류', expected_id=mid, raw_hex=raw.hex())
    return raw[4]


class PingOnlyTransport(LinuxReadTransport):
    def request(self, *_):
        raise ReadFault('PING 전용 모드에서 READ/쓰기 경로 사용 금지')

    def ping(self, mid):
        import array
        import fcntl
        import select
        import termios
        tx = ping_packet(mid)
        require_ping_packet(tx)
        if self.fd is None:
            raise ReadFault('포트 미연결')
        pending = array.array('i', [0])
        fcntl.ioctl(self.fd, termios.FIONREAD, pending, True)
        if pending[0]:
            raise ReadFault('수신 잔여; 지우지 않고 해당 ID 조회 거부', queued_bytes=pending[0])
        if os.write(self.fd, tx) != len(tx):
            raise ReadFault('PING 전송 불완전')
        raw, deadline = bytearray(), time.monotonic() + .15
        while len(raw) < 6:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                if not raw:
                    return {'id': mid, 'status': 'NO_RESPONSE', 'responded': False,
                            'normal': False, 'tx_hex': tx.hex(), 'rx_hex': ''}
                raise ReadFault('PING 부분 응답 timeout', raw_hex=raw.hex(), expected_id=mid)
            data = os.read(self.fd, 6-len(raw))
            if not data:
                raise ReadFault('PING USB EOF', raw_hex=raw.hex())
            raw.extend(data)
        status = parse_ping_reply(bytes(raw), mid)
        return {'id': mid, 'status': 'RESPONDED' if status == 0 else 'RESPONDED_WITH_ERROR',
                'responded': True, 'normal': status == 0, 'motor_status': status,
                'tx_hex': tx.hex(), 'rx_hex': raw.hex()}


def scan(factory=PingOnlyTransport):
    rows = []
    for mid in range(1, 7):
        transport = factory(FOLLOWER)
        try:
            transport.open()
            row = transport.ping(mid)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            row = {'id': mid, 'status': 'QUERY_ERROR', 'responded': False, 'normal': False,
                   'error': str(error), 'observed': getattr(error, 'observed', {})}
        finally:
            transport.close()
        row.update(port_closed=transport.fd is None, cleanup_errors=list(transport.cleanup_errors))
        rows.append(row)
        if not row['port_closed'] or row['cleanup_errors']:
            rows.extend({'id': next_id, 'status': 'SKIPPED_CLOSE_UNVERIFIED', 'responded': False,
                         'normal': False} for next_id in range(mid+1, 7))
            break
    return {'status': 'PING_SCAN_COMPLETE', 'ids': rows,
            'responding_ids': [r['id'] for r in rows if r['responded']],
            'normal_ids': [r['id'] for r in rows if r['normal']],
            'all_normal': len(rows) == 6 and all(r['normal'] and r.get('port_closed')
                                               and not r.get('cleanup_errors') for r in rows),
            'motor_register_writes': 0, 'live': 'NOT_RUN', 'port': FOLLOWER}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if not a.execute:
        p.error('--execute 없음; 장치 미연결')
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open('x', encoding='utf-8') as f:
        result = scan()
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['all_normal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
