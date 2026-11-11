"""첫 시험의 정리·결과 보존. 모터 상태 조회나 레지스터 쓰기를 하지 않는다."""
import hashlib
import json
import math
import os
from pathlib import Path
import time


def json_value(value):
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return "미직렬화 형식: " + type(value).__name__


def finish_resources(*resources):
    errors, ports = [], {}
    for label, resource in resources:
        if resource is None:
            continue
        permission = getattr(resource, "permission", None)
        if permission is not None:
            try: permission.seal()
            except BaseException as error: errors.append(dict(component=label + "_seal", kind=type(error).__name__))
        try:
            resource.close()
        except BaseException as error:
            errors.append(dict(component=label + "_close", kind=type(error).__name__))
        for message in getattr(resource, "cleanup_errors", []) or []:
            errors.append(dict(component=label, reason=str(message)))
        bus = getattr(getattr(resource, "robot", None), "bus", None)
        serial = getattr(getattr(bus, "port_handler", None), "ser", None)
        if serial is not None:
            # 예외가 난 close 뒤에도 OS 포트는 해제한다. 모터 쓰기는 하지 않는다.
            if getattr(serial, "is_open", None) is not False:
                try:
                    serial.close()
                    if getattr(serial, "is_open", None) is False: bus.port_handler.is_open = False
                except BaseException as error:
                    errors.append(dict(component=label + "_serial_close", kind=type(error).__name__))
            ports[label] = getattr(serial, "is_open", None) is False
            if ports[label] and permission is not None:
                try: permission.restore()
                except BaseException as error: errors.append(dict(component=label + "_restore", kind=type(error).__name__))
            if not ports[label]: errors.append(dict(component=label, reason="포트 해제 미확인"))
    return dict(errors=errors, ports_closed=ports)


def verify_target_receipt(io, targets, expected_steps):
    permission = getattr(io, "permission", None)
    if permission is None or permission.goal_packets != len(targets) or len(targets) != expected_steps:
        raise RuntimeError("목표 송신 시도와 저장 step 수 불일치; 완료로 판정하지 않음")


def save_run(root, output, result, *, latch=False):
    result = json_value(result)
    if result.get("cleanup_errors"):
        result["status"] = "HOLD"
    stop = Path(root) / "FIELD_STOP.json"
    if latch and result.get("status") == "HOLD":
        record = json.dumps(dict(result, unix=time.time(), record_kind="FIRST_TRIAL_HOLD"),
                            ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        try:
            with stop.open("xb") as stream:
                stream.write(record); stream.flush(); os.fsync(stream.fileno())
            result["stop_receipt"] = dict(created=True, name=stop.name)
        except FileExistsError:
            try:
                result["stop_receipt"] = dict(created=False, existing_preserved=True,
                                              sha256=hashlib.sha256(stop.read_bytes()).hexdigest())
            except OSError as error:
                result["stop_receipt"] = dict(created=False, existing_preserved=True,
                                              error_kind=type(error).__name__)
        except OSError as error:
            result["stop_receipt"] = dict(created=False, error_kind=type(error).__name__)
            result["status"] = "HOLD"
    text = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
    with (Path(output) / "RESULT.json").open("x", encoding="utf-8") as stream:
        stream.write(text); stream.flush(); os.fsync(stream.fileno())
    return result
