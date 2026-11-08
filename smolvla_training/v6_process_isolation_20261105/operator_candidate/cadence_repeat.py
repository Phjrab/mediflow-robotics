"""직전 성공 목표의 연속성을 검증한다. 모터 API 없음."""
from first_trial_core import vector,bounds_gate

def continuity(measured,active,expected,quantum,bounds):
    measured,active,expected=vector(measured),vector(active),vector(expected)
    for row in (measured,active,expected):bounds_gate(row,bounds)
    import math
    if len(quantum)!=6 or any(type(v) not in (float,int) or not math.isfinite(v) or v<=0 for v in quantum):raise ValueError("관절 분해능 미확인")
    rows=[]
    for i in range(6):
        rows.append(dict(joint_index=i,active_expected_delta=active[i]-expected[i],active_current_delta=active[i]-measured[i],
                         quantum=quantum[i],passed=abs(active[i]-expected[i])<=quantum[i]+1e-9 and abs(active[i]-measured[i])<=(.5 if i<5 else 2.)))
    return dict(scope="PREVIOUS_SUCCESS_GOAL_CONTINUITY",passed=all(r["passed"] for r in rows),rows=rows)
