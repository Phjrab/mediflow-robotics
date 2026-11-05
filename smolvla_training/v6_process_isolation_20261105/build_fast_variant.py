"""v6 모델 계산만 다른 프로세스에 배치. 원래 gate·감시·닫기·상한은 유지."""
import ast
from pathlib import Path
from latency_dependencies import prepare

def render(source):
    tree=ast.parse(source);lines=source.splitlines(keepends=True)
    replacements={"infer_chunk":'''def infer_chunk(policy,preprocess,postprocess,observation,torch):
    return infer_process(policy,preprocess,postprocess,observation,torch)
''',"read_motor_state":'''def read_motor_state(bus):
    return torque_modes(bus)
''',"read_health":'''def read_health(bus,starting=False,load_limit=200.0):
    return batch_health(bus,load_limit)
'''}
    for node in sorted([n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in replacements],key=lambda n:n.lineno,reverse=True):
        lines[node.lineno-1:node.end_lineno]=[replacements[node.name]]
    text="".join(lines)
    pairs={"from live_safety_runtime import":"from latency_dependencies import prepare\nprepare()\nfrom process_policy import infer_process\nfrom batch_motor_read import torque_modes,health as batch_health\nfrom live_safety_runtime import",
           "from persistent_policy_worker import PersistentPolicyWorker":"from process_policy import ClosingWorker as PersistentPolicyWorker",
           "    from lerobot.policies.factory import make_pre_post_processors":"    from process_policy import process_processors as make_pre_post_processors",
           "    from lerobot.policies.smolvla import SmolVLAPolicy":"    from process_policy import ProcessPolicy as SmolVLAPolicy"}
    for old,new in pairs.items():
        if text.count(old)!=1:raise ValueError("교체 기준 미확인")
        text=text.replace(old,new)
    marker="    session.begin(args)"
    addition='\n    from runtime_cpu import configure_main\n    configure_main(Path(__file__).resolve().parent/"CPU_RUNTIME.json")'
    if text.count(marker)!=1:raise ValueError("CPU 설정 위치 불일치")
    text=text.replace(marker,marker+addition)
    restored=text.replace(addition,"")
    for old,new in reversed(list(pairs.items())):restored=restored.replace(new,old)
    def unchanged(value):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(value).body if isinstance(n,ast.FunctionDef) and n.name not in replacements}
    if unchanged(source)!=unchanged(restored):raise ValueError("gate/감시/main 조건 변경 감지")
    ast.parse(text,feature_version=(3,10));return text

if __name__=="__main__":
    base=prepare()
    with (Path(__file__).parent/"so101_v6_fast_dry.py").open("x",encoding="utf-8",newline="\n") as f:f.write(render((base/"so101_first_action_test_safe_v6_dry.py").read_text(encoding="utf-8")))
    print("PROCESS_VARIANT_GATES_UNCHANGED")
