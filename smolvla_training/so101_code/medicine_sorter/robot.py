from __future__ import annotations

import fcntl
import inspect
import os
import termios
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import SorterError
from .models import TaskPlan


@dataclass(frozen=True)
class RobotState:
    connected: bool
    moving: bool
    torque_enabled: bool | None
    temperatures_c: tuple[float, ...]


@dataclass(frozen=True)
class RobotStatusSnapshot:
    port: str
    serial: str
    goal_raw: dict[str, int]
    present_raw: dict[str, int]
    torque_enable: dict[str, int]
    moving: dict[str, int]
    temperature_c: dict[str, int]
    read_requests: int
    state_change_writes: int
    disconnect_disable_torque: bool

    def as_dict(self) -> dict:
        return {
            "status": "ROBOT_STATUS_READ_ONLY",
            "port": self.port,
            "serial": self.serial,
            "goal_raw": self.goal_raw,
            "present_raw": self.present_raw,
            "torque_enable": self.torque_enable,
            "moving": self.moving,
            "temperature_c": self.temperature_c,
            "read_requests": self.read_requests,
            "state_change_writes": self.state_change_writes,
            "disconnect_disable_torque": self.disconnect_disable_torque,
        }


class RobotAdapter(Protocol):
    physical: bool

    def connect(self) -> None: ...
    def read_state(self) -> RobotState: ...
    def prepare(self) -> None: ...
    def execute_stage(self, stage: str) -> None: ...
    def stop_new_commands(self) -> None: ...
    def close(self) -> None: ...


class RobotStatusAdapter(Protocol):
    def connect(self) -> None: ...
    def read_status(self) -> RobotStatusSnapshot: ...
    def close(self) -> None: ...


class MockRobotAdapter:
    physical = False

    def __init__(self, *, fail_stage: str | None = None) -> None:
        self.connected = False
        self.prepared = False
        self.stopped = False
        self.closed = False
        self.fail_stage = fail_stage
        self.executed: list[str] = []

    def connect(self) -> None:
        if self.connected:
            raise SorterError("duplicate_robot_connection", True, "연결 한 번",
                              "기존 작업을 종료해 주세요")
        self.connected = True

    def read_state(self) -> RobotState:
        return RobotState(self.connected, False, True, (25.0,) * 6)

    def prepare(self) -> None:
        if not self.connected:
            raise SorterError("robot_not_connected", False, "connected=true", "먼저 연결해 주세요")
        self.prepared = True

    def execute_stage(self, stage: str) -> None:
        if not self.prepared or self.stopped:
            raise SorterError("robot_not_prepared", {"prepared": self.prepared, "stopped": self.stopped},
                              "prepared=true and stopped=false", "작업을 새로 시작해 주세요")
        if stage == self.fail_stage:
            raise SorterError("mock_stage_failed", stage, "정상 단계 실행", "실패 원인을 확인해 주세요")
        self.executed.append(stage)

    def stop_new_commands(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True
        self.connected = False


class RobotController:
    """The only application layer allowed to request robot state changes."""

    def __init__(self, adapter: RobotAdapter) -> None:
        self.adapter = adapter

    def run(self, plan: TaskPlan) -> tuple[str, ...]:
        if plan.physical_motion_authorized != self.adapter.physical:
            raise SorterError("plan_adapter_mode_mismatch", {
                "plan_physical": plan.physical_motion_authorized,
                "adapter_physical": self.adapter.physical,
            }, "동일한 mock/live 모드", "실행 모드를 확인해 주세요")
        completed: list[str] = []
        connected = False
        try:
            self.adapter.connect()
            connected = True
            state = self.adapter.read_state()
            if state.moving:
                raise SorterError("robot_already_moving", True, "moving=false",
                                  "다른 제어기를 중단하고 상태를 다시 확인해 주세요")
            self.adapter.prepare()
            for stage in plan.stages:
                self.adapter.execute_stage(stage)
                completed.append(stage)
            return tuple(completed)
        finally:
            if connected:
                self.adapter.stop_new_commands()
            self.adapter.close()

    def read_status(self) -> RobotStatusSnapshot:
        """Read one hardware snapshot without preparing or commanding motion."""
        try:
            self.adapter.connect()
            return self.adapter.read_status()
        finally:
            self.adapter.close()


class SO101ReadOnlyStatusAdapter:
    """Minimal installed-SDK adapter for a state-only follower session.

    It deliberately bypasses ``SO101Follower.connect()`` because that path
    configures motors and enables torque in the installed stack observed on
    the Jetson. The bus is opened with ``handshake=False``; reads use raw
    register requests with zero retries; shutdown explicitly passes
    ``disable_torque=False``.
    """

    MOTOR_NAMES = (
        "shoulder_pan", "shoulder_lift", "elbow_flex",
        "wrist_flex", "wrist_roll", "gripper",
    )
    REGISTERS = {
        "goal_raw": "Goal_Position",
        "present_raw": "Present_Position",
        "torque_enable": "Torque_Enable",
        "moving": "Moving",
        "temperature_c": "Present_Temperature",
    }

    def __init__(self, *, port: str, expected_serial: str, probe=None,
                 bus_factory=None, exclusive_seal=None, lock_path: Path | None = None,
                 auto_resolve_port: bool = False):
        self.port = Path(port)
        self.expected_serial = expected_serial
        self.probe = probe
        self.bus_factory = bus_factory
        self.exclusive_seal = exclusive_seal or self._seal_serial_exclusive
        self.lock_path = lock_path or Path(
            f"/run/user/{os.getuid()}/medicine-sorter-follower.lock")
        self.bus = None
        self._lock_file = None
        self._connected = False
        self._read_requests = 0
        self._write_attempts = 0
        self._disconnect_disable_torque = None
        self.auto_resolve_port = auto_resolve_port

    @classmethod
    def from_config(cls, config):
        robot = config.raw["robot"]
        return cls(port=robot["follower_port"], expected_serial=robot["follower_serial"],
                   auto_resolve_port=True)

    def _resolve_expected_port(self, probe) -> Path:
        allowed = (Path("/dev/ttyACM0"), Path("/dev/ttyACM1"))
        if not self.auto_resolve_port:
            observed = probe.serial_short(self.port)
            if observed != self.expected_serial:
                raise SorterError("wrong_follower_usb_identity", observed,
                                  self.expected_serial,
                                  "follower 장비 식별자를 확인해 주세요")
            return self.port
        observed_by_port: dict[str, str] = {}
        matches: list[Path] = []
        for candidate in allowed:
            try:
                observed = probe.serial_short(candidate)
            except Exception as error:
                observed_by_port[str(candidate)] = f"UNAVAILABLE:{type(error).__name__}"
                continue
            observed_by_port[str(candidate)] = observed
            if observed == self.expected_serial:
                matches.append(candidate)
        if len(matches) != 1:
            raise SorterError("follower_serial_port_resolution_failed", observed_by_port,
                              {"serial": self.expected_serial, "unique_match_count": 1},
                              "두 USB serial과 현재 ACM 장치를 확인해 주세요")
        return matches[0]

    @staticmethod
    def _build_bus(port: str):
        from lerobot.motors import Motor, MotorNormMode
        from lerobot.motors.feetech import FeetechMotorsBus

        motors = {
            name: Motor(index, "sts3215",
                        MotorNormMode.DEGREES if name != "gripper"
                        else MotorNormMode.RANGE_0_100)
            for index, name in enumerate(SO101ReadOnlyStatusAdapter.MOTOR_NAMES, start=1)
        }
        return FeetechMotorsBus(port=port, motors=motors)

    @staticmethod
    def _seal_serial_exclusive(bus) -> None:
        serial = getattr(getattr(bus, "port_handler", None), "ser", None)
        if serial is None or not hasattr(serial, "fileno"):
            raise RuntimeError("serial_exclusive_seal_unavailable")
        fcntl.ioctl(serial.fileno(), termios.TIOCEXCL)

    def _resolve_probe(self):
        if self.probe is None:
            from scripts.so101_exclusive_bus import SystemBusProbe
            self.probe = SystemBusProbe()
        return self.probe

    def _forbid_state_change(self, *_args, **_kwargs):
        self._write_attempts += 1
        raise RuntimeError("state_change_write_blocked")

    def _install_write_firewall(self, bus) -> None:
        for method_name in ("write", "sync_write", "enable_torque", "disable_torque"):
            if hasattr(bus, method_name):
                setattr(bus, method_name, self._forbid_state_change)

    @staticmethod
    def _require_runtime_contract(bus) -> None:
        connect_parameters = inspect.signature(bus.connect).parameters
        disconnect_parameters = inspect.signature(bus.disconnect).parameters
        if "handshake" not in connect_parameters:
            raise RuntimeError("bus_connect_handshake_contract_missing")
        if "disable_torque" not in disconnect_parameters:
            raise RuntimeError("bus_disconnect_torque_contract_missing")

    def _acquire_process_lock(self) -> None:
        self._lock_file = self.lock_path.open("a+")
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release_process_lock(self) -> None:
        if self._lock_file is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    @staticmethod
    def _force_serial_close(bus) -> None:
        serial = getattr(getattr(bus, "port_handler", None), "ser", None)
        if serial is not None and hasattr(serial, "close"):
            serial.close()

    def _disconnect_bus(self) -> None:
        if self.bus is None:
            return
        if not self._connected:
            self._force_serial_close(self.bus)
            return
        self._disconnect_disable_torque = False
        try:
            self.bus.disconnect(disable_torque=False)
        finally:
            self._force_serial_close(self.bus)
            self._connected = False

    def connect(self) -> None:
        if self.port not in (Path("/dev/ttyACM0"), Path("/dev/ttyACM1")):
            raise SorterError("robot_status_port_not_allowed", str(self.port),
                              "/dev/ttyACM0 or /dev/ttyACM1",
                              "Jetson의 canonical follower node를 확인해 주세요")
        probe = self._resolve_probe()
        try:
            self._acquire_process_lock()
            self.port = self._resolve_expected_port(probe)
            owners = probe.owner_pids(self.port)
            if owners:
                raise SorterError("follower_port_busy", sorted(owners), "owner PID 없음",
                                  "다른 controller를 정상 종료해 주세요")
            factory = self.bus_factory or self._build_bus
            self.bus = factory(str(self.port))
            self._require_runtime_contract(self.bus)
            self._install_write_firewall(self.bus)
            self.bus.connect(handshake=False)
            self._connected = True
            self.exclusive_seal(self.bus)
            owners_after = probe.owner_pids(self.port)
            if owners_after != {os.getpid()}:
                raise RuntimeError(f"follower_bus_not_exclusive:{sorted(owners_after)}")
        except SorterError:
            self._disconnect_bus()
            self._release_process_lock()
            raise
        except Exception as error:
            self._disconnect_bus()
            self._release_process_lock()
            raise SorterError("robot_status_connect_failed", f"{type(error).__name__}: {error}",
                              "쓰기 없는 exclusive bus connect",
                              "SDK contract·포트 점유·통신을 확인해 주세요") from error

    def _ensure_exclusive(self) -> None:
        if not self._connected or self.bus is None:
            raise SorterError("robot_status_not_connected", False, "connected=true",
                              "상태 조회를 다시 시작해 주세요")
        owners = self._resolve_probe().owner_pids(self.port)
        if owners != {os.getpid()}:
            raise SorterError("follower_bus_ownership_changed", sorted(owners),
                              f"owner={os.getpid()}", "추가 read를 중단하고 다른 controller를 확인해 주세요")

    def read_status(self) -> RobotStatusSnapshot:
        self._ensure_exclusive()
        values = {}
        try:
            for label, register in self.REGISTERS.items():
                values[label] = {}
                for name in self.MOTOR_NAMES:
                    values[label][name] = int(self.bus.read(
                        register, name, normalize=False, num_retry=0))
                    self._read_requests += 1
        except Exception as error:
            raise SorterError("robot_status_read_failed", f"{type(error).__name__}: {error}",
                              "30개 raw register read 요청의 응답",
                              "자동 재시도 없이 통신과 전원을 확인해 주세요") from error
        return RobotStatusSnapshot(
            str(self.port), self.expected_serial,
            values["goal_raw"], values["present_raw"], values["torque_enable"],
            values["moving"], values["temperature_c"], self._read_requests,
            self._write_attempts, False,
        )

    def close(self) -> None:
        try:
            self._disconnect_bus()
        finally:
            self._release_process_lock()


class LiveRobotAdapter:
    """Fail-closed placeholder until the new-environment live contract is verified.

    Importing and constructing this class never imports LeRobot or opens a port.
    The old recovery executors are intentionally not called from this path.
    """

    physical = True

    def __init__(self, readiness: dict) -> None:
        self.readiness = readiness

    def connect(self) -> None:
        raise SorterError("live_control_not_validated", self.readiness,
                          "새 환경 calibration·path·Torque lifecycle verified",
                          "아래 미검증 항목을 완료한 뒤 단일 감독 시험을 승인해 주세요")

    def read_state(self) -> RobotState:
        raise AssertionError("connect always fails closed before device access")

    def prepare(self) -> None:
        raise AssertionError("connect always fails closed before device access")

    def execute_stage(self, stage: str) -> None:
        raise AssertionError("connect always fails closed before device access")

    def stop_new_commands(self) -> None:
        pass

    def close(self) -> None:
        pass
