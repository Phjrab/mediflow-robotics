"""원래10스텝 프로필과 분리한 단일50행동 작업 구간."""
from dataclasses import dataclass
from first_trial_core import Limits,Hold

@dataclass(frozen=True)
class WorkSegmentLimits(Limits):
    cap:float=2.
    max_seconds:float=10.
    max_steps:int=50
    work_segment=True
    def validate(self):
        values=dict(self.__dict__)
        values.update(cap=min(self.cap,.5),max_steps=min(self.max_steps,10),max_seconds=min(self.max_seconds,5))
        Limits(**values).validate()
        if not 0<self.cap<=2 or type(self.max_steps) is not int or not 1<=self.max_steps<=50 or not 0<self.max_seconds<=10:raise Hold("작업 구간 상한 초과")
