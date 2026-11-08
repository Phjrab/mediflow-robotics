import ast,hashlib,json,sys
from pathlib import Path
from types import SimpleNamespace
root=Path.home()/"v6_process_1793887709179/operator_candidate"
sys.path.insert(0,str(root))
from individual_reply_guard import StrictIndividualReplies,ReplyRejected,read_target_feedback
p=Path.home()/"venvs/lelab-jetson-py310/lib/python3.10/site-packages/scservo_sdk/protocol_packet_handler.py"
text=p.read_text(encoding="utf-8-sig")
node=next(n for n in ast.walk(ast.parse(text)) if isinstance(n,ast.FunctionDef) and n.name=="readTxRx")
scope=dict(COMM_SUCCESS=0,COMM_NOT_AVAILABLE=-1,BROADCAST_ID=254,PKT_ID=2,PKT_LENGTH=3,PKT_INSTRUCTION=4,PKT_PARAMETER0=5,PKT_ERROR=4,INST_READ=2)
exec(compile(ast.Module(body=[node],type_ignores=[]),"installed_sdk_fixture","exec"),scope)
class Handler:
    readTxRx=scope["readTxRx"]
    def __init__(self,short=False):self.short=short;self.calls=0
    def txRxPacket(self,port,request):
        self.calls+=1
        payload=b"" if self.short else bytes((242,7))
        raw=bytes((255,255,request[2],len(payload)+2,0))+payload
        return raw+bytes(((~sum(raw[2:]))&255,)),0,0
joints=tuple("abcdef")
h=Handler()
bus=SimpleNamespace(packet_handler=h,port_handler=None,motors={j:SimpleNamespace(id=i) for i,j in enumerate(joints,1)})
with StrictIndividualReplies(h):assert read_target_feedback(bus,joints)==dict.fromkeys(joints,2034)
h=Handler(True)
with StrictIndividualReplies(h):
    try:h.readTxRx(None,1,67,2)
    except ReplyRejected:pass
    else:raise AssertionError("malformed ACK accepted")
assert h.calls==1
print(json.dumps(dict(status="INSTALLED_SDK_SYNTHETIC_PASS",tests=2,hardware_access=False,motor_register_writes=0,source_file=p.name,source_sha256=hashlib.sha256(p.read_bytes()).hexdigest())))
