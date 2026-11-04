"""무인 프로파일은 상태 읽기·거부만 수행한다. 토크 변경 함수는 모의 시험 전용이다."""
import argparse
import json
import time
import nov_readonly as n
from artifacts import save_json
import operator_core

def disable_mock_only(bus,api,guard,home=None):
    # 검증된 별도 operator_core의 부분ON 해제 로직을 가짜 버스로만 시험한다.
    # 이 CLI에서는 실제 bus를 이 함수에 전달하지 않는다.
    return operator_core.release(bus,api,guard,home)

def print_diagnostics(snapshot,home):
    rows=[dict(joint=j,Torque_Enable=snapshot['torque'][j],position=snapshot['positions'][j],
        home=home['state'][i],home_delta=snapshot['positions'][j]-home['state'][i],
        mode=snapshot['modes'][j],temperature=snapshot['temperatures'][j],
        unit='percent_0_100' if i==5 else 'degree') for i,j in enumerate(n.JOINTS)]
    print('MOTOR_DIAGNOSTICS '+json.dumps(rows,ensure_ascii=False),flush=True)
    return rows

def precheck(snapshot,home):
    if any(v!=0 for v in snapshot['torque'].values()):raise n.SafetyStop('토크 일부ON; 활성화 거부')
    if any(v!=0 for v in snapshot['modes'].values()):raise n.SafetyStop('위치 모드0 아님')
    if any(v>=45 for v in snapshot['temperatures'].values()):raise n.SafetyStop('무인45도 한계')
    n.home_gate(snapshot,home)

def main():
    p=argparse.ArgumentParser(description=__doc__);mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--enable',action='store_true');mode.add_argument('--disable',action='store_true')
    p.add_argument('--execute',action='store_true');p.add_argument('--confirm-supported',action='store_true');args=p.parse_args()
    if args.disable:
        print('REFUSED_OVERNIGHT_NO_WRITES: --disable은 모의 시험만 허용, 실제 serial 연결 없음',flush=True)
        raise SystemExit(2 if args.execute else 0)
    home=n.read_home()
    with n.ReadSession() as session:
        snapshot=session.read(home);print_diagnostics(snapshot,home);precheck(snapshot,home)
    save_json(n.ROOT/'reports'/('torque_precheck_'+str(time.time_ns())+'.json'),snapshot)
    print('PRECHECK_PORT_CLOSED: ENABLE 프롬프트 전에 포트 해제',flush=True)
    if not args.execute:
        print('READONLY_PRECHECK_PASS: 활성화 실행 없음');return
    try:token=input('현장 운영자만 ENABLE 입력 가능: ')
    except EOFError:
        print('REFUSED_NO_CONFIRMATION: 입력EOF, 쓰기0',flush=True);raise SystemExit(2)
    # 무인 프로파일은 값에 관계없이 쓰기 불가. 내일의 명시적 실행은 별도 검토 도구를 사용한다.
    print('REFUSED_OVERNIGHT_NO_WRITES: 이 프로파일은 ENABLE 입력에도 레지스터를 쓰지 않음',flush=True)
    raise SystemExit(2)

if __name__=='__main__':main()
