"""ID3 dead zones and limits; every serial packet is checked to be READ only."""
import argparse
import hashlib
import json
from pathlib import Path

from config_runtime import load
from servo_thermal_readonly import require_unowned_port
from individual_reply_guard import StrictIndividualReplies


def inspect_read(packet, allowed):
    data = bytes(packet)
    if (len(data) != 8 or data[:4] != bytes((255, 255, 3, 4)) or data[4] != 2
            or (data[5], data[6]) not in allowed or sum(data[2:]) % 256 != 255):
        raise RuntimeError('ID3의 승인 READ 이외 패킷 차단')
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    config = load()
    calibration_path = Path(config['calibration']).expanduser()
    original_hash = hashlib.sha256(calibration_path.read_bytes()).hexdigest()
    if original_hash != config['calibration_sha256']:
        raise RuntimeError('보정 파일 해시 불일치; 장치 미연결')
    require_unowned_port(Path(config['port']).resolve(strict=True))
    from lerobot.motors import Motor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus
    cal = json.loads(calibration_path.read_text())['elbow_flex']
    bus = FeetechMotorsBus(port=config['port'], motors={'elbow_flex': Motor(3, 'sts3215', MotorNormMode.DEGREES)},
                          calibration={'elbow_flex': MotorCalibration(**cal)})
    registers = ('CW_Dead_Zone', 'CCW_Dead_Zone', 'Min_Position_Limit', 'Max_Position_Limit',
                 'Present_Position', 'Goal_Position', 'Present_Load', 'Present_Current',
                 'Present_Temperature', 'Present_Voltage', 'Torque_Enable', 'Status')
    table = bus.model_ctrl_table['sts3215']
    allowed = {tuple(table[name]) for name in registers}
    write_port = bus.port_handler.writePort
    result = dict(status='HOLD', live='NOT_RUN', motor_register_writes=0, reads={}, packets=[])

    def guarded(packet):
        checked = inspect_read(packet, allowed)
        result['packets'].append(checked.hex())
        return write_port(packet)

    bus.port_handler.writePort = guarded
    try:
        bus.connect(handshake=False)
        with StrictIndividualReplies(bus.packet_handler) as guard:
            for name in registers:
                result['reads'][name] = bus.read(name, 'elbow_flex', normalize=False, num_retry=0)
            result['replies'] = list(guard.history)
        result['status'] = 'ELBOW_SETTINGS_READONLY_COMPLETE'
    except Exception as error:
        result['error'] = dict(kind=type(error).__name__, reason=str(error))
    finally:
        if bus.port_handler.is_open:
            bus.disconnect(disable_torque=False)
        result['port_closed'] = not bus.port_handler.is_open
        result['calibration_sha256'] = hashlib.sha256(calibration_path.read_bytes()).hexdigest()
        require_unowned_port(Path(config['port']).resolve(strict=True))
    args.out.expanduser().write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'ELBOW_SETTINGS_READONLY_COMPLETE' and result['port_closed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
