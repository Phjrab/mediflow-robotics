"""Recording-only SO101 transport boundary. Install before either bus is opened."""
import json
import logging
import os
from pathlib import Path

LOG = logging.getLogger("lelab.record_guard")
IDS = set(range(1, 7))


def site_config():
    return json.loads(Path(__file__).with_name("site.local.json").read_bytes())


class RecordingStopped(Exception):
    """A valid motion packet was cancelled before transmission."""


def existing_calibration_names(leader, follower):
    from lelab.utils.config import LEADER_CONFIG_PATH, FOLLOWER_CONFIG_PATH
    names = []
    for directory, name in [(LEADER_CONFIG_PATH,leader),(FOLLOWER_CONFIG_PATH,follower)]:
        if Path(name).name != name or not name.endswith(".json") or not (Path(directory)/name).is_file():
            raise ValueError("Existing calibration file required")
        names.append(Path(name).stem)
    return tuple(names)


def packet_kind(raw, role, stopping=False):
    p = bytes(raw)
    if len(p) < 6 or p[:2] != b"\xff\xff" or len(p) != p[3]+4 or sum(p[2:]) % 256 != 255:
        raise ValueError("invalid frame")
    target, opcode, data = p[2], p[4], p[5:-1]
    if opcode == 1 and not data and target in IDS | {254}:
        return "read"
    if opcode == 2 and target in IDS and len(data) == 2 and data[1] > 0:
        return "read"
    if opcode == 0x82 and target == 254 and len(data) >= 3 and data[1] and set(data[2:]) <= IDS:
        return "read"
    if opcode == 3 and len(data) >= 2:
        address, entries = data[0], [(target, data[1:])]
    elif opcode == 0x83 and target == 254 and len(data) >= 4:
        address, width = data[:2]
        if width not in (1, 2) or (len(data)-2) % (width+1):
            raise ValueError("invalid sync write width")
        entries = [(data[i], data[i+1:i+1+width]) for i in range(2,len(data),width+1)]
    else:
        raise ValueError(f"unapproved opcode {opcode}")
    if len({motor for motor,_ in entries}) != len(entries):
        raise ValueError("duplicate ID")
    for motor, value in entries:
        allowed = motor in IDS and address == 40 and value == b"\x00"
        if role == "follower" and not stopping and motor in IDS:
            raw_value = int.from_bytes(value, "little")
            allowed |= address == 40 and value == b"\x01"
            allowed |= address == 42 and len(value) == 2 and raw_value <= 4095
            allowed |= address in (16,48) and motor == 6 and len(value) == 2 and 0 < raw_value <= 500
        if not allowed:
            raise ValueError(f"forbidden address={address} width={len(value)} id={motor}")
    return "write"


class RecordingSession:
    def __init__(self, follower, leader, *, dataset_id, events, report_dir=None):
        self.devices = {"follower": follower, "leader": leader}
        self.stopping = False
        self.buses = {}
        self.counts = {role: {"allowed_tx":0,"blocked":0} for role in self.devices}
        self.cancelled = 0
        self.off = {role:{} for role in self.devices}
        self.report_dir = Path(report_dir or Path.home()/".local/state/lelab-record-guard")
        self.report_dir.mkdir(parents=True, exist_ok=True)
        import uuid
        self.report = self.report_dir/(uuid.uuid4().hex+".json")
        self.dataset_id = dataset_id
        self.events = events

    def attach(self, role, bus):
        handler = bus.port_handler
        downstream = handler.writePort

        def guarded(packet):
            try:
                kind = packet_kind(packet, role)
            except ValueError as e:
                self.counts[role]["blocked"] += 1
                self.stopping = True
                handler.is_using = False
                LOG.error("guard blocked role=%s detail=%s", role, e)
                raise PermissionError(str(e)) from e
            if kind == "write" and (self.stopping or self.events["stop_recording"]):
                try:
                    packet_kind(packet, role, stopping=True)
                except ValueError as e:
                    handler.is_using = False
                    self.cancelled += 1
                    LOG.info("cancelled pending motion during stop role=%s", role)
                    raise RecordingStopped() from e
            count = downstream(packet)
            if kind == "write" and count == len(packet):
                self.counts[role]["allowed_tx"] += 1
            return count

        handler.writePort = guarded
        self.buses[role] = bus

    def start(self):
        for role, device in self.devices.items():
            bus = device.bus
            expected = site_config()["ports"][role]
            if not os.path.samefile(bus.port, expected):
                raise RuntimeError(f"{role}: unexpected serial port")
            if {m.id for m in bus.motors.values()} != IDS or bus.motors["gripper"].id != 6:
                raise RuntimeError(f"{role}: unexpected motors")
            self.attach(role,bus)
            bus.connect()
            if not bus.calibration or not bus.is_calibrated:
                raise RuntimeError(f"{role}: calibration mismatch")
            for name in bus.motors:
                if bus.read("Operating_Mode",name,normalize=False) != 0:
                    raise RuntimeError(f"{role}/{name}: position mode required")
                if bus.read("Torque_Enable",name,normalize=False) != 0:
                    raise RuntimeError(f"{role}/{name}: expected initial torque OFF")
        follower = self.buses["follower"]
        for field, limit in (("Max_Torque_Limit",500),("Protection_Current",250),("Overload_Torque",25)):
            if not 0 < follower.read(field,"gripper",normalize=False) <= limit:
                raise RuntimeError(f"gripper: check existing {field}")
        for camera in self.devices["follower"].cameras.values():
            camera.connect()
        for name in follower.motors:
            actual = follower.read("Present_Position",name,normalize=False)
            follower.write("Goal_Position",name,actual,normalize=False)
            if follower.read("Goal_Position",name,normalize=False) != actual:
                raise RuntimeError(f"{name}: hold target mismatch")
        for name in follower.motors:
            follower.write("Torque_Enable",name,1,normalize=False)

    def close(self):
        self.stopping = True
        errors = []
        for role, bus in self.buses.items():
            if not bus.is_connected:
                errors.append(f"{role}: port unavailable for readback")
                continue
            # An individual failure must not prevent off attempts on the other axes.
            for name in bus.motors:
                try:
                    bus.port_handler.is_using = False
                    bus.write("Torque_Enable",name,0,normalize=False,num_retry=2)
                except Exception as e:
                    errors.append(f"{role}/{name}: {e}")
            for name in bus.motors:
                try:
                    bus.port_handler.is_using = False
                    self.off[role][name] = bus.read("Torque_Enable",name,normalize=False,num_retry=2)
                except Exception as e:
                    errors.append(f"{role}/{name}: readback {e}")
            try:
                bus.port_handler.closePort()
            except Exception as e:
                errors.append(f"{role}: close {e}")
        for camera in self.devices["follower"].cameras.values():
            try:
                if camera.is_connected:
                    camera.disconnect()
            except Exception as e:
                errors.append(f"camera: {e}")
        verified = all(len(values)==6 and all(v==0 for v in values.values()) for values in self.off.values())
        result = dict(dataset=self.dataset_id,counts=self.counts,torque_readback=self.off,
                      all_off_verified=verified,cancelled_motion=self.cancelled,errors=errors)
        self.report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf8")
        LOG.info("guard report %s",self.report)
        if errors or not verified:
            raise RuntimeError(f"torque shutdown not verified cleanly: {self.report}")
