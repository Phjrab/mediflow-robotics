"""보존한 SDK 함수에 가짜 응답만 넣는다. 장치·SDK import·추론 없이 검사한다."""
import argparse
import ast
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace


def run():
    root = Path(__file__).resolve().parent
    evidence = root / "evidence/FIELD_INCIDENT_SDK_20261106.json"
    records = json.loads(evidence.read_text(encoding="utf-8"))

    def method(suffix, name, line=None):
        matches = [m for f in records if f["file"].endswith(suffix)
                   for m in f["methods"] if m["name"] == name
                   and (line is None or m["line"] == line)]
        assert len(matches) == 1
        source = matches[0]["source"]
        tree = ast.parse(source)
        assert len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef)
        scope = dict(COMM_SUCCESS=0, COMM_RX_FAIL=-1, COMM_NOT_AVAILABLE=-2,
                     logger=logging.getLogger("합성응답시험"))
        exec(compile(tree, "보존한 SDK 함수/" + name, "exec"), scope)
        return scope[name]

    Reader = type("SyntheticReader", (), {
        n: method("group_sync_read.py", n)
        for n in ("rxPacket", "isAvailable", "getData")
    })
    upper_read = method("motors_bus.py", "_sync_read", 1171)

    def simulate(value, bad_id=None, comm=0):
        requests, extractions = [], []

        def read_rx(port, motor_id, length):
            requests.append(motor_id)
            assert port is None and length == 1
            return ([] if motor_id == bad_id else [value]), comm, 0

        reader = Reader()
        reader.data_dict = {i: [] for i in range(1, 7)}
        reader.start_address, reader.data_length = 40, 1
        reader.last_result, reader.port = False, None
        reader.ph = SimpleNamespace(readRx=read_rx)
        reader.txRxPacket = reader.rxPacket  # 전송 대신 가짜 응답 함수만 호출
        original_get = reader.getData

        def get_data(*args):
            extractions.append(args[0])
            return original_get(*args)

        reader.getData = get_data
        bus = SimpleNamespace(sync_reader=reader,
                              _setup_sync_reader=lambda *args: None,
                              _is_comm_success=lambda result: result == 0,
                              packet_handler=SimpleNamespace(getTxRxResult=str))
        try:
            values, result = upper_read(bus, 40, 1, list(range(1, 7)), num_retry=0)
            return dict(values=values, communication=result,
                        requests=requests, extractions=extractions)
        except ConnectionError:
            assert not extractions
            return dict(rejected=True, requests=requests, extractions=extractions)

    zero = simulate(0)
    assert list(zero["values"].values()) == [0] * 6
    one = simulate(1)
    assert list(one["values"].values()) == [1] * 6
    malformed = simulate(1, bad_id=3)
    assert malformed["values"][3] == 0
    failed = simulate(1, comm=-1)
    assert failed["rejected"] is True and len(failed["requests"]) == 1

    candidates = [root.parent / "operator_candidate/initialize_current_goal.py",
                  root.parent / "v6_process_isolation_20261105/operator_candidate/initialize_current_goal.py"]
    init = next(p for p in candidates if p.is_file())
    tree = ast.parse(init.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "exact_packet")
    joints = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
    scope = dict(JOINTS=joints)
    exec(compile(ast.Module(body=[function], type_ignores=[]), "목표 패킷 순수 함수", "exec"), scope)
    values = dict(zip(joints, [2035, 831, 3082, 2575, 1812, 531]))
    packet = scope["exact_packet"](values)
    assert packet[:3] == bytes([255, 255, 254]) and packet[4:7] == bytes([131, 42, 2])
    assert packet[3] + 4 == len(packet) and sum(packet[2:]) % 256 == 255
    decoded = {j: int.from_bytes(packet[8 + 3*i:10 + 3*i], "little")
               for i, j in enumerate(joints)}
    assert decoded == values
    assert [packet[7 + 3*i] for i in range(6)] == list(range(1, 7))

    return dict(
        status="PASS", synthetic_scenarios=4,
        evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
        zero=zero, one=one, malformed_success_response=malformed,
        communication_error=failed,
        attempted_goal_packet=dict(address=42, byte_width=2, motor_ids=list(range(1, 7)),
                                   values=decoded, hex=packet.hex(),
                                   torque_address_40_in_write_range=False),
        finding="합성 데이터만 사용. 누락 응답을 통신 성공으로 전달하면 0으로 처리되지만, 실기 발생 증거 없음.",
        incident_root_cause="UNVERIFIED", hardware_access=False,
        inference="NOT_RUN", live="NOT_RUN")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = run()
    if args.out:
        with args.out.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print("합성 응답 4종·목표 패킷 형식 검사 PASS / 실제 원인 UNVERIFIED / 장치 접근 0")
