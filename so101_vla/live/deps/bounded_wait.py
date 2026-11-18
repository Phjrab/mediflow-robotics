"""추론/카메라 대기 중 상태 감시. 마지막 상태 조회 시간도 예산에 포함."""
import time
from first_trial_dependencies import prepare

def wait(fn,monitor,*,deadline,timeout):
    prepare()
    from live_safety_runtime import monitored_inference
    from first_trial_core import Hold
    end=min(deadline,time.monotonic()+timeout)
    result=monitored_inference(fn,monitor,deadline=end,timeout=timeout)
    if time.monotonic()>end:raise Hold("마지막 감시 포함 시간 초과")
    return result
