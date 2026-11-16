"""Opening stop protection and a bounded ID-6 overload HOLD recovery."""
from config_runtime import load


class OpeningGuard:
    def __init__(self, settings=None):
        self.settings=dict(settings or load()['gripper'])
        self.cap=self.settings['max_open_points']
        self.since=self.fixed=None
        self.clips=self.freezes=0

    def apply(self, target, position, load_raw, now):
        requested=target
        if requested>self.cap:self.clips+=1
        target=min(target,self.cap)
        if target<position-.1:
            self.since=self.fixed=None
            return target
        if self.fixed is not None:
            return min(target,self.fixed)
        opening=target>position+.1
        loaded=load_raw*self.settings['opening_load_sign']>=self.settings['opening_load_threshold_raw']
        if not (opening and loaded):self.since=None
        elif self.since is None:self.since=now
        elif now-self.since+1e-9>=self.settings['opening_load_hold_s']:
            self.fixed=min(position,self.cap);self.freezes+=1
            return min(target,self.fixed)
        return target

    def summary(self):
        return dict(open_cap_points=self.cap,policy_open_cap_clips=self.clips,
                    opening_load_freezes=self.freezes,opening_hold_target_points=self.fixed)


def recover_overload(io):
    """Stop the run; preserve other axes; never resume the policy here."""
    records=[];baseline=None
    io.recovering_gripper=True
    try:
        for attempt in range(1,load()['gripper']['overload_recovery_attempts']+1):
            sample=io.recovery_snapshot()
            peers=[j for j in io.ids if j!='gripper']
            if any(sample['modes'][j]!=0 or sample['torque'][j]!=1 for j in peers):
                raise RuntimeError('그리퍼 복구 중 다른5축 모드/토크 불일치')
            if baseline is None:baseline={j:sample['present_raw'][j] for j in peers}
            if any(abs(sample['present_raw'][j]-baseline[j])>3 for j in peers):
                raise RuntimeError('그리퍼 복구 중 다른5축 위치 이탈')
            target={j:min(io.arm_bounds[j][1],max(io.arm_bounds[j][0],sample['present_raw'][j])) for j in io.ids}
            target.update({j:min(io.arm_bounds[j][1],max(io.arm_bounds[j][0],baseline[j])) for j in peers})
            io._send_once(target)
            row=dict(attempt=attempt,target_raw=target,torque_cycle=False);records.append(row)
            # A clipped opening target can clear the load without a torque cycle.
            for _ in range(4):
                io.pause(.04);after=io.recovery_snapshot()
                if after['statuses']['gripper']==0:break
            if after['statuses']['gripper']==32:
                io.write_recovery_gripper('Torque_Enable',0)
                seed=io.recovery_snapshot();goal=min(io.arm_bounds['gripper'][1],max(io.arm_bounds['gripper'][0],seed['present_raw']['gripper']))
                io.write_recovery_gripper('Goal_Position',goal)
                before=io.recovery_snapshot()
                if abs(before['active_targets']['gripper']-before['present_raw']['gripper'])>3:
                    raise RuntimeError('그리퍼 재활성화 전 목표/현재3틱 초과; 토크ON 금지')
                if before['torque']['gripper']==0:io.write_recovery_gripper('Torque_Enable',1)
                row['torque_cycle']=True
                io.pause(.04);after=io.recovery_snapshot()
                target['gripper']=goal
            io.pause(.04);last=io.recovery_snapshot()
            stable=all(abs(last['present_raw'][j]-after['present_raw'][j])<=2 and abs(last['present_raw'][j]-target[j])<=3 for j in io.ids)
            confirmed=all(last['statuses'][j]==0 and last['torque'][j]==1 and last['modes'][j]==0 for j in io.ids)
            if stable and confirmed and last['active_targets']==target:
                return dict(status='HOLD_CONFIRMED',attempts=records,target_raw=target,final_present_raw=last['present_raw'],
                            confirmation='gripper_overload_cleared_goal_readback_and_stability',policy_resumed=False)
            row.update(statuses=last['statuses'],present_raw=last['present_raw'],stable=stable)
    except (KeyboardInterrupt,Exception) as error:
        return dict(status='HOLD_NOT_CONFIRMED',attempts=records,reason=str(error),policy_resumed=False)
    finally:
        io.recovering_gripper=False
    return dict(status='HOLD_NOT_CONFIRMED',attempts=records,reason='그리퍼 과부하 복구3회 실패',policy_resumed=False)
