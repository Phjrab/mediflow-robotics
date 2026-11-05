"""이 실행 프로세스의 CPU 병렬도만 제한. 장치/시스템 설정 변경 없음."""
import json
import threading
local=threading.local()

def configure_main(path):
    import torch
    import cv2
    result={"scope":"PROCESS_ONLY","torch_before":torch.get_num_threads(),"opencv_before":cv2.getNumThreads()}
    torch.set_num_threads(1);cv2.setNumThreads(1)
    result.update(torch_after=torch.get_num_threads(),opencv_after=cv2.getNumThreads())
    if result["torch_after"]!=1 or result["opencv_after"]!=1:raise RuntimeError("CPU 스레드 설정 검증 실패")
    with path.open("x",encoding="utf-8") as f:json.dump(result,f,indent=2)
    return result

def configure_worker(torch):
    if not getattr(local,"configured",False):torch.set_num_threads(1);local.configured=True
    if torch.get_num_threads()!=1:raise RuntimeError("정책 worker CPU 병렬도 변경 감지")
