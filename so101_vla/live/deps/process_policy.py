"""GPU 정책을 spawn 프로세스로 격리. 자식에 robot/bus/카메라 객체를 전달하지 않는다."""
import atexit
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from types import SimpleNamespace

def deny_hardware(event,args):
    if event!="open" or not args or not isinstance(args[0],(str,bytes,os.PathLike)):return
    path=os.fsdecode(args[0])
    if path.startswith(("/dev/tty","/dev/serial/","/dev/video","/dev/v4l/")):
        raise RuntimeError("정책 프로세스의 로봇/카메라 장치 접근 금지")

def _serve(connection,factory,options):
    sys.addaudithook(deny_hardware)
    try:
        engine=factory(options)
        connection.send(dict(ok=True,config=engine.config,pid=os.getpid()))
        while True:
            command,payload=connection.recv()
            if command=="close":connection.send(dict(ok=True,value=None));return
            start=time.perf_counter()
            try:
                value=engine.call(command,payload)
                connection.send(dict(ok=True,value=value,seconds=time.perf_counter()-start))
            except BaseException:
                connection.send(dict(ok=False,error=traceback.format_exc().replace(str(Path.home()),"~")));return
    except BaseException:
        try:connection.send(dict(ok=False,error=traceback.format_exc().replace(str(Path.home()),"~")))
        except (BrokenPipeError,EOFError,OSError):pass
    finally:connection.close()

class Transport:
    def __init__(self,factory,options,timeout=120):
        context=mp.get_context("spawn")
        self.connection,child=context.Pipe();self.lock=threading.Lock();self.closed=False;self.history=[]
        self.process=context.Process(target=_serve,args=(child,factory,options),daemon=True,name="isolated-policy-no-hardware")
        self.process.start();child.close()
        try:
            if not self.connection.poll(timeout):raise TimeoutError("정책 프로세스 로딩 시간 초과")
            ready=self.connection.recv()
            if ready.get("ok") is not True:raise RuntimeError(ready.get("error","정책 로딩 실패"))
            self.config=ready["config"];self.pid=ready["pid"]
        except BaseException:self.close();raise
    def call(self,command,payload=None):
        if self.closed or not self.lock.acquire(blocking=False):raise RuntimeError("정책 종료/동시 호출 거부")
        try:
            start=time.perf_counter();self.connection.send((command,payload))
            if not self.connection.poll(10):raise TimeoutError("정책 응답10초 초과; 상위 감시 결과는 별도 폐기")
            response=self.connection.recv()
            self.history.append(dict(command=command,parent_seconds=time.perf_counter()-start,child_seconds=response.get("seconds")))
            if response.get("ok") is not True:raise RuntimeError(response.get("error","정책 응답 오류"))
            return response["value"]
        finally:self.lock.release()
    def close(self):
        if self.closed:return
        self.closed=True
        if self.process.is_alive() and self.lock.acquire(blocking=False):
            try:
                self.connection.send(("close",None))
                if self.connection.poll(1):self.connection.recv()
            except (OSError,EOFError,BrokenPipeError):pass
            finally:self.lock.release()
        self.process.join(1)
        if self.process.is_alive():self.process.terminate();self.process.join(3)
        self.connection.close()
        if self.process.is_alive():raise RuntimeError("정책 프로세스 종료 미확인")

class RealEngine:
    def __init__(self,options):
        from latency_dependencies import prepare
        prepare()
        import torch
        from lerobot.policies.smolvla import SmolVLAPolicy
        from lerobot.policies.factory import make_pre_post_processors
        from fast_postprocess import validate_post_config
        self.torch=torch;torch.set_num_threads(1);torch.set_num_interop_threads(1)
        path=Path(options["checkpoint"]).expanduser().resolve()
        self.config=json.loads((path/"config.json").read_text())
        if self.config.get("chunk_size")!=50 or self.config.get("n_action_steps")!=50:raise RuntimeError("50/50 계약 불일치")
        validate_post_config(json.loads((path/"policy_postprocessor.json").read_text()))
        self.policy=SmolVLAPolicy.from_pretrained(str(path),local_files_only=True).to("cuda").eval()
        self.pre,self.post=make_pre_post_processors(self.policy.config,str(path),preprocessor_overrides={"device_processor":{"device":"cuda"}})
    def call(self,command,payload):
        torch=self.torch
        if command=="reset":self.policy.reset();return None
        if command=="seed":
            torch.manual_seed(payload);torch.cuda.manual_seed_all(payload);return None
        if command!="predict":raise ValueError("허용하지 않은 정책 명령")
        import numpy as np
        from checkpoint_amp import infer_with_saved_amp
        from fast_postprocess import infer_chunk_fast
        observation={"task":payload["task"]}
        for key,row in payload["tensors"].items():
            shape=tuple(row["shape"])
            expected=(1,6) if key=="observation.state" else (1,3,480,640)
            if shape!=expected or len(row["bytes"])!=4*int(np.prod(shape)):raise ValueError("IPC 관측 형식 불일치")
            observation[key]=torch.from_numpy(np.frombuffer(row["bytes"],dtype=np.float32).copy().reshape(shape))
        torch.cuda.synchronize()
        result=infer_with_saved_amp(lambda:infer_chunk_fast(self.policy,self.pre,self.post,observation,torch),torch,self.config)
        torch.cuda.synchronize();return result

def real_factory(options):return RealEngine(options)

_policies=[]
class ProcessPolicy:
    def __init__(self,path):
        self.transport=Transport(real_factory,{"checkpoint":str(path)})
        self.config=SimpleNamespace(**self.transport.config);_policies.append(self);atexit.register(self.close)
    @classmethod
    def from_pretrained(cls,path,local_files_only=False):
        if local_files_only is not True:raise RuntimeError("로컬 체크포인트만 허용")
        return cls(path)
    def to(self,device):
        if str(device)!="cuda":raise RuntimeError("확인된 CUDA 실행만 허용")
        return self
    def eval(self):return self
    def reset(self):return self.transport.call("reset")
    def seed(self,value):return self.transport.call("seed",value)
    def predict_raw(self,observation):
        cameras={"observation.images."+name for name in ("end_effector","ceiling_vertical","ceiling_oblique")}
        if set(observation)!={"observation.state","task",*cameras}:raise ValueError("정책 IPC 입력 키 불일치")
        if not isinstance(observation["task"],list) or len(observation["task"])!=1 or not observation["task"][0]:raise ValueError("task 형식 불일치")
        payload=dict(task=observation["task"],tensors={})
        for key in cameras|{"observation.state"}:
            tensor=observation[key]
            if tensor.device.type!="cpu" or str(tensor.dtype)!="torch.float32":raise ValueError("IPC는 CPU float32만 허용")
            shape=(1,6) if key=="observation.state" else (1,3,480,640)
            if tuple(tensor.shape)!=shape:raise ValueError("IPC 입력 shape 불일치")
            payload["tensors"][key]=dict(shape=list(shape),bytes=tensor.detach().contiguous().numpy().tobytes())
        return self.transport.call("predict",payload)
    def close(self):
        self.transport.close()
        out=Path(__file__).resolve().parent/"CHILD_HISTORY.json"
        if not out.exists():
            with out.open("x") as f:json.dump(dict(pid=self.transport.pid,history=self.transport.history,alive=self.transport.process.is_alive()),f,indent=2)

def process_processors(config,path,**kwargs):
    # 실제 저장 전/후처리는 자식에서 적용한다. 부모에서는 두 번째로 적용하지 않는다.
    return None,None

def infer_process(policy,pre,post,observation,torch):return policy.predict_raw(observation)

from persistent_policy_worker import PersistentPolicyWorker
class ClosingWorker(PersistentPolicyWorker):
    def close(self,timeout=10):
        stopped=super().close(timeout)
        for policy in _policies:policy.close()
        return stopped
