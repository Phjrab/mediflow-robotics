"""공유 중단 상태에서 진입점의 거부만 확인한다. 입력은 항상 EOF다."""
import json
from pathlib import Path
import subprocess
import sys
from artifacts import save_json

ROOT=Path(__file__).resolve().parent
def main():
    if not (ROOT/'SERIAL_STOP.json').exists():raise SystemExit('거부 시험 전제인 중단 기록 없음; 도구를 실행하지 않음')
    commands=[('home_monitor_refusal',[sys.executable,'-B',str(ROOT/'home_align_web.py'),'--max-seconds','2']),
        ('torque_enable_pre_prompt_refusal',[sys.executable,'-B',str(ROOT/'torque_so101.py'),'--enable','--execute']),
        ('dry_wrapper_refusal',['bash',str(ROOT/'nov_policy_run.sh'),'dry'])]
    result=[]
    for name,argv in commands:
        p=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=30,cwd=str(ROOT))
        text=(p.stdout+p.stderr).replace(str(Path.home()),'~')
        expected=p.returncode!=0 and 'serial 전역 중단' in text
        result.append(dict(name=name,exit_code=p.returncode,expected_refusal=expected,
            confirmation_input='EOF_ONLY',output=text[-2500:],device_open='NOT_REACHED_BY_STATIC_GATE'))
        print(json.dumps(dict(name=name,expected_refusal=expected,exit_code=p.returncode)),flush=True)
    save_json(ROOT/'reports/REFUSAL_CHECKS.json',result)
    if not all(r['expected_refusal'] for r in result):raise SystemExit('거부 시험 불일치; 추가 장치 작업 없음')

if __name__=='__main__':main()
