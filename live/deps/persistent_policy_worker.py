"""예열과 실제 정책 계산을 같은 스레드에서 수행한다. SDK/장치 API 없음."""
import atexit
import queue
import threading
import time

class PersistentPolicyWorker:
    def __init__(self):
        self.tasks=queue.Queue();self.lock=threading.Lock();self.flight=threading.Lock()
        self.closed=False;self.history=[]
        self.thread=threading.Thread(target=self._loop,name="persistent-policy-no-devices",daemon=True)
        self.thread.start();atexit.register(self.close)

    def _loop(self):
        while True:
            task=self.tasks.get()
            if task is None:return
            fn,result,label=task;start=time.perf_counter()
            try:value=(True,fn())
            except BaseException as error:value=(False,error)
            self.history.append(dict(label=label,seconds=time.perf_counter()-start,thread_id=threading.get_ident()))
            result.put(value)

    def call(self,fn,label="prediction"):
        if threading.get_ident()==self.thread.ident:raise RuntimeError("정책 worker 재귀 호출 거부")
        if not self.flight.acquire(blocking=False):raise RuntimeError("동시 정책 요청 거부")
        try:
            result=queue.Queue(maxsize=1)
            with self.lock:
                if self.closed:raise RuntimeError("이미 종료된 정책 worker")
                self.tasks.put((fn,result,label))
            ok,value=result.get()
            if not ok:raise value
            return value
        finally:self.flight.release()

    def close(self,timeout=10):
        with self.lock:
            if not self.closed:
                self.closed=True;self.tasks.put(None)
        if threading.get_ident()==self.thread.ident:return False
        self.thread.join(timeout)
        return not self.thread.is_alive()
