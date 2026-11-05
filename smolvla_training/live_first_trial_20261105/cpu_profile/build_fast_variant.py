"""v6 추론·상태 읽기 구현만 교체. main/판정 한계/종료 경로 불변."""
import ast
from pathlib import Path
from latency_dependencies import prepare

def render(source):
    tree=ast.parse(source)
    lines=source.splitlines(keepends=True)
    replacements={"infer_chunk":'''def infer_chunk(policy,preprocess,postprocess,observation,torch):
    return infer_chunk_fast(policy,preprocess,postprocess,observation,torch)
''',"read_motor_state":'''def read_motor_state(bus):
    return torque_modes(bus)
''',"read_health":'''def read_health(bus,starting=False,load_limit=200.0):
    return batch_health(bus,load_limit)
'''}
    for node in sorted([n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in replacements],key=lambda n:n.lineno,reverse=True):
        lines[node.lineno-1:node.end_lineno]=[replacements[node.name]]
    text="".join(lines)
    anchor="from live_safety_runtime import"
    if text.count(anchor)!=1:raise ValueError("v6 import 근거 불일치")
    text=text.replace(anchor,"from latency_dependencies import prepare\nprepare()\nfrom fast_postprocess import infer_chunk_fast\nfrom batch_motor_read import torque_modes,health as batch_health\n"+anchor)
    after=ast.parse(text,feature_version=(3,10))
    original={n.name:ast.dump(n,include_attributes=False) for n in tree.body if isinstance(n,ast.FunctionDef) and n.name not in replacements}
    changed={n.name:ast.dump(n,include_attributes=False) for n in after.body if isinstance(n,ast.FunctionDef) and n.name not in replacements}
    if original!=changed:raise ValueError("허용한 추론·읽기 함수 외 변경 감지")
    marker="    session.begin(args)"
    if text.count(marker)!=1:raise ValueError("CPU 설정 삽입 위치 미확인")
    text=text.replace(marker,marker+'\n    from cpu_runtime import configure_main\n    configure_main(Path(__file__).resolve().parent/"CPU_RUNTIME.json")')
    return text

if __name__=="__main__":
    root=prepare();source=(root/"so101_first_action_test_safe_v6_dry.py").read_text(encoding="utf-8")
    with (Path(__file__).parent/"so101_v6_fast_dry.py").open("x",encoding="utf-8",newline="\n") as f:f.write(render(source))
    print("FAST_VARIANT_CREATED_MAIN_AND_GATES_UNCHANGED")
