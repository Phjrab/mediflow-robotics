"""Bounded -6/-7 recovery; no device imports and no register-write authority."""
import contextlib
import time


class RetryableWireError(RuntimeError):
    def __init__(self, reason, observed):
        super().__init__(reason)
        self.observed=dict(observed)


class CommunicationHold(RuntimeError):
    def __init__(self, reason, observed=None):
        super().__init__(reason)
        self.observed=observed or {}


class CommunicationPolicy:
    def __init__(self, joints, settings, log, *, clock=time.monotonic, pause=time.sleep):
        self.joints=tuple(joints);self.settings=settings;self.log=log
        self.clock,self.pause=clock,pause;self.started=clock()
        self.last_good={i:self.started for i in range(1,len(joints)+1)}
        self.streak={};self.counts={j:{'-6':0,'-7':0} for j in joints}
        self.events=[];self.active=False;self.halted=None;self.hold_deadline=None
        self.port=None;self.original_write=None;self.previous_write=None;self.had_write=False
        self.tx_count=0;self.rx_count=0;self.tx_bytes=0;self.rx_bytes=0
        self.tx_times=[];self.last_pose=None;self.pose_at=None;self.last_temp_request={}

    def update_pose(self,sample):
        self.last_pose={'state':list(sample['state']),'present_raw':dict(sample['present_raw'])}
        self.pose_at=self.clock()

    def context(self):
        pose=self.last_pose or {};state=pose.get('state')
        return dict(**pose,pose_age_s=None if self.pose_at is None else self.clock()-self.pose_at,
                    wrist_roll_deg=None if state is None else state[4],
                    wrist_flex_deg=None if state is None else state[3])

    def activate(self):
        self.active=True

    def attach(self,port):
        self.port=port;self.had_write='writePort' in port.__dict__
        self.previous_write=port.__dict__.get('writePort');self.original_write=port.writePort
        def write(packet):
            raw=bytes(packet);now=self.clock();self.tx_count+=1;self.tx_bytes+=len(raw)
            self.tx_times.append(now)
            self.log(dict(event='wire_tx',monotonic_s=now,unix=time.time(),packet_hex=raw.hex(),
                          instruction=raw[4] if len(raw)>4 else None,
                          address=raw[5] if len(raw)>5 else None,phase='hold' if self.hold_deadline else 'normal'))
            return self.original_write(packet)
        port.writePort=write
        serial=getattr(port,'ser',None)
        if serial is not None:serial.write_timeout=.02

    def detach(self):
        if self.port is not None and self.original_write is not None:
            if self.had_write:self.port.writePort=self.previous_write
            else:self.port.__dict__.pop('writePort',None)
        self.original_write=None

    def receive(self,mid,operation,packet,communication):
        raw=bytes(packet or ());self.rx_count+=1;self.rx_bytes+=len(raw)
        self.log(dict(event='wire_rx',monotonic_s=self.clock(),unix=time.time(),expected_id=mid,
            operation=operation,raw_hex=raw.hex(),communication=communication,
            received_id=raw[2] if len(raw)>2 else None,
            checksum_valid=len(raw)>=6 and sum(raw[2:])%256==255))

    def good(self,mid,operation,*,response=True):
        self.streak[(mid,operation)]=0
        if response:self.last_good[mid]=self.clock()

    def bad(self,mids,operation,error):
        observed=getattr(error,'observed',{})
        code=observed.get('communication')
        if code not in (-6,-7):raise error
        for mid in mids:
            self.counts[self.joints[mid-1]][str(code)]+=1
            key=(mid,operation);self.streak[key]=self.streak.get(key,0)+1
        row=dict(event='communication_error',monotonic_s=self.clock(),unix=time.time(),
                 axes=[self.joints[i-1] for i in mids],operation=operation,
                 communication=code,observed=observed,pose=self.context(),
                 axis_attribution='reply_expected_id' if len(mids)==1 else 'broadcast_target_axes_not_individual_reply',
                 streak={self.joints[i-1]:self.streak[i,operation] for i in mids},
                 phase='hold' if self.hold_deadline is not None else 'normal')
        self.events.append(row);self.log(row)
        if self.hold_deadline is None and any(self.streak[i,operation]>=self.settings['consecutive_failures'] for i in mids):
            self.halted=CommunicationHold('동일 축·요청 연속5회 통신 실패',row)

    def deadline(self,mids,begin):
        deadline=begin+self.settings['no_response_s']
        if self.active and self.hold_deadline is None:
            deadline=min(deadline,min(self.last_good[i]+self.settings['no_response_s'] for i in mids))
        if self.hold_deadline is not None:deadline=min(deadline,self.hold_deadline)
        return deadline

    def check(self,mids,begin):
        if self.halted is not None and self.hold_deadline is None:raise self.halted
        if self.clock()>=self.deadline(mids,begin):
            error=CommunicationHold('정상 응답 없이200ms 경과',{'axes':[self.joints[i-1] for i in mids],'pose':self.context()})
            if self.hold_deadline is None:self.halted=error
            raise error

    @contextlib.contextmanager
    def packet_deadline(self,port,deadline):
        original=port.isPacketTimeout
        had='isPacketTimeout' in port.__dict__;previous=port.__dict__.get('isPacketTimeout')
        port.isPacketTimeout=lambda: self.clock()>=deadline or original()
        try:yield
        finally:
            if had:port.isPacketTimeout=previous
            else:port.__dict__.pop('isPacketTimeout',None)

    def resynchronize(self,port,deadline):
        port.is_using=False
        quiet=self.clock();end=min(deadline,quiet+.006)
        while self.clock()<end:
            count=port.getBytesAvailable()
            if count:
                raw=bytes(port.readPort(min(count,1024)))
                self.log(dict(event='rx_resynchronization',monotonic_s=self.clock(),raw_hex=raw.hex()))
                quiet=self.clock()
            elif self.clock()-quiet>=.002:return
            self.pause(.001)

    def perform(self,mids,operation,call,*,port=None,initial_failures=0,response=True,packet_timeout_s=.02):
        begin=self.clock();attempt=initial_failures;cycle=0
        while True:
            self.check(mids,begin)
            if attempt>=1+self.settings['max_retries']:
                if self.hold_deadline is not None:raise CommunicationHold('HOLD 읽기 재시도 예산 소진')
                self.log(dict(event='communication_cycle_deferred',operation=operation,cycle=cycle,pose=self.context()))
                self.pause(.02);attempt=0;cycle+=1
            deadline=self.deadline(mids,begin)
            if port is not None and (attempt or cycle):self.resynchronize(port,deadline)
            if operation=='read:63:1':
                last=self.last_temp_request.get(mids[0])
                if last is not None:self.pause(max(0.,last+.02-self.clock()))
                self.check(mids,begin);self.last_temp_request[mids[0]]=self.clock()
            attempt+=1
            try:
                if port is None:value=call()
                else:
                    packet_deadline=deadline if packet_timeout_s is None else min(deadline,self.clock()+packet_timeout_s)
                    with self.packet_deadline(port,packet_deadline):value=call()
            except RetryableWireError as error:
                self.bad(mids,operation,error);continue
            if self.clock()>deadline:
                raise CommunicationHold('정상 응답 없이200ms 경과',{'axes':[self.joints[i-1] for i in mids],'pose':self.context()})
            for mid in mids:self.good(mid,operation,response=response)
            return value

    @contextlib.contextmanager
    def hold_attempt(self):
        previous=self.hold_deadline;self.hold_deadline=self.clock()+.2
        try:yield
        finally:self.hold_deadline=previous

    def summary(self):
        elapsed=max(self.clock()-self.started,1e-9)
        gaps=[b-a for a,b in zip(self.tx_times,self.tx_times[1:])]
        return dict(by_joint=self.counts,errors=self.events,tx_packets=self.tx_count,rx_packets=self.rx_count,
            tx_bytes=self.tx_bytes,rx_bytes=self.rx_bytes,elapsed_s=elapsed,
            tx_packets_per_s=self.tx_count/elapsed,total_packets_per_s=(self.tx_count+self.rx_count)/elapsed,
            tx_gap_s=dict(min=min(gaps,default=None),max=max(gaps,default=None),
                          mean=sum(gaps)/len(gaps) if gaps else None),
            settings=self.settings,halt_reason=None if self.halted is None else str(self.halted),
            halt_observed=None if self.halted is None else self.halted.observed)


def retryable_write_error(error,observed):
    info=getattr(error,'observed',{})
    code=info.get('communication')
    if code is None:
        message=str(error)
        if 'Incorrect status packet' in message or '통신 코드=-7' in message:code=-7
        elif 'There is no status packet' in message or '통신 코드=-6' in message:code=-6
    if code not in (-6,-7):return None
    return RetryableWireError(str(error),{**observed,**info,'communication':code})


def confirmed_hold(io,policy,joints):
    attempts=[]
    for attempt in range(1,policy.settings['hold_attempts']+1):
        try:
            with policy.hold_attempt():
                sample=io.feedback()
                target={j:min(io.arm_bounds[j][1],max(io.arm_bounds[j][0],sample['present_raw'][j])) for j in joints}
                io._send_once(target)
                raw=io.read_block(67,2)
                goals={j:int.from_bytes(raw[j],'little') for j in joints}
                first=io.feedback();io.pause(.02);last=io.feedback()
                if goals!=target:raise RuntimeError('유지 목표 피드백 불일치')
                if any(abs(last['present_raw'][j]-first['present_raw'][j])>2 or abs(last['present_raw'][j]-target[j])>3 for j in joints):
                    raise RuntimeError('유지 목표 후 위치 안정 미확인')
                return dict(status='HOLD_CONFIRMED',attempts=attempt,target_raw=target,goal_feedback_raw=goals,
                            final_present_raw=last['present_raw'],confirmation='goal_readback_and_position_stability')
        except (KeyboardInterrupt,Exception) as error:
            attempts.append(dict(attempt=attempt,reason=str(error)))
            policy.log(dict(event='hold_attempt_failed',**attempts[-1]))
            if isinstance(error,KeyboardInterrupt) or getattr(io,'wire_fault',False):break
    return dict(status='HOLD_NOT_CONFIRMED',attempts=attempts,reason='최대5회 유지 복구·읽기 확인 실패')
