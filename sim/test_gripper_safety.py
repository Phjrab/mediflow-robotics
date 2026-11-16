"""Opening stop, scoped status recovery and unchanged peer axes; no devices."""
import copy
import types
import unittest
from config_runtime import load,command_limits
from gripper_safety import OpeningGuard,recover_overload
from startup_simulation import SimulatedArm
from stream_live import start_policy_session
import direct_live as base
from test_communication_retry import fake_imports,Clock,Port

JOINTS=load()['joint_names']


def status_reply(mid,status,width=15):
    body=bytes((255,255,mid,width+2,status))+bytes(width)
    return body+bytes(((~sum(body[2:]))&255,))


class RecoveryIO:
    def __init__(self,cycle=False,stubborn=False):
        self.ids=dict(zip(JOINTS,range(1,7)));self.arm_bounds=command_limits()
        self.present={j:2000 for j in JOINTS};self.present['gripper']=1000
        self.target=dict(self.present);self.status=dict.fromkeys(JOINTS,0);self.status['gripper']=32
        self.torque=dict.fromkeys(JOINTS,1);self.writes=[];self.cycle=cycle;self.stubborn=stubborn
        self.recovering_gripper=False;self.t=0
    def pause(self,dt):self.t+=dt
    def recovery_snapshot(self):
        return dict(present_raw=self.present.copy(),active_targets=self.target.copy(),statuses=self.status.copy(),
                    torque=self.torque.copy(),modes=dict.fromkeys(JOINTS,0))
    def _send_once(self,values):
        self.writes.append(('Goal_Position',values.copy()));self.target.update(values);self.present.update(values)
        if not self.cycle and not self.stubborn:self.status['gripper']=0
    def write_recovery_gripper(self,register,value):
        self.writes.append((register,{'gripper':value}))
        if register=='Torque_Enable':
            self.torque['gripper']=value
            if value==1 and not self.stubborn:self.status['gripper']=0
        else:self.target['gripper']=value


class GripperTests(unittest.TestCase):
    def test_nominal_48_5_and_data_adjusted_cap(self):
        guard=OpeningGuard({**load()['gripper'],'max_open_points':48.5})
        self.assertEqual(guard.apply(61,30,0,0),48.5)
        guard=OpeningGuard();self.assertEqual(guard.apply(61,30,0,0),load()['gripper']['max_open_points'])
        self.assertEqual(guard.summary()['policy_open_cap_clips'],1)

    def test_close_direction_and_minimum_unchanged(self):
        guard=OpeningGuard()
        self.assertEqual(guard.apply(0,30,0,0),0)
        self.assertEqual(command_limits()['gripper'][0],load()['limits']['gripper'][0])

    def test_opening_load_freeze_at_0_3_wall_seconds_and_closing_release(self):
        guard=OpeningGuard()
        self.assertEqual(guard.apply(46,40,-400,0),46)
        self.assertEqual(guard.apply(46,40,-400,.299),46)
        self.assertEqual(guard.apply(46,40,-400,.3),40)
        self.assertEqual(guard.apply(48,40,-450,.5),40)
        self.assertEqual(guard.apply(30,40,-450,.6),30)
        self.assertIsNone(guard.fixed);self.assertEqual(guard.freezes,1)

    def test_opposite_load_or_short_pulse_does_not_latch(self):
        guard=OpeningGuard()
        for t in (0,.4,.8):self.assertEqual(guard.apply(46,40,500,t),46)
        guard.apply(46,40,-400,1);guard.apply(46,40,0,1.2)
        self.assertEqual(guard.apply(46,40,-400,1.4),46)

    def test_same_stream_with_61_point_policy_never_reaches_50_point_stop(self):
        c=load();events=[];io=SimulatedArm(c['home']['state'],{},events.append,'camera_dry')
        def policy(*args):return [c['home']['state'][:-1]+[61.] for _ in range(50)],.02
        result=start_policy_session(io,policy,io.home,events.append,4,.33)
        self.assertEqual(result['status'],'RUN_COMPLETE');self.assertGreater(result['steps'],0)
        self.assertGreater(result['gripper_opening_protection']['policy_open_cap_clips'],0)
        for r in events:
            if r['event']=='goal_transmitted':
                self.assertLessEqual(r['wire_raw']['gripper'],command_limits()['gripper'][1])
                self.assertLess(r['feedback']['state'][-1],50)

    def test_current_position_hold_without_torque_cycle(self):
        io=RecoveryIO();baseline=io.present.copy();result=recover_overload(io)
        self.assertEqual(result['status'],'HOLD_CONFIRMED');self.assertFalse(result['policy_resumed'])
        self.assertFalse(any(reg=='Torque_Enable' for reg,_ in io.writes))
        self.assertEqual(io.present,baseline)

    def test_cycle_only_id6_and_keep_five_other_axes(self):
        io=RecoveryIO(cycle=True);baseline=io.present.copy();result=recover_overload(io)
        self.assertEqual(result['status'],'HOLD_CONFIRMED')
        torque=[values for reg,values in io.writes if reg=='Torque_Enable']
        self.assertEqual(torque,[{'gripper':0},{'gripper':1}])
        self.assertEqual(io.present,baseline)

    def test_persistent_overload_maximum_three_cycles(self):
        io=RecoveryIO(cycle=True,stubborn=True);result=recover_overload(io)
        self.assertEqual(result['status'],'HOLD_NOT_CONFIRMED')
        self.assertEqual(sum(reg=='Torque_Enable' and values=={'gripper':0} for reg,values in io.writes),3)
        self.assertFalse(io.recovering_gripper)

    def test_typed_overload_keeps_validated_payload_but_never_returns_normal_feedback(self):
        packets=[(status_reply(i,32 if i==6 else 0),0) for i in range(1,7)]
        handler=types.SimpleNamespace(rxPacket=lambda port:packets.pop(0))
        with self.assertRaises(base.ServoStatusStop) as error:
            base.read_sync_replies(handler,Port(),dict(zip(JOINTS,range(1,7))),56,15)
        self.assertEqual(error.exception.mid,6);self.assertEqual(error.exception.status,32)
        self.assertEqual(set(error.exception.replies),set(JOINTS))

    def test_other_axis_or_corruption_cannot_enter_overload_recovery(self):
        for mid in (1,6):
            raw=bytearray(status_reply(mid,32));raw[-1]^=1
            with self.assertRaises(base.Stop) as error:base.parse_reply(raw,0,mid,15)
            self.assertNotIsInstance(error.exception,base.ServoStatusStop)
        packets=[(status_reply(i,32 if i==1 else 0),0) for i in range(1,7)]
        with self.assertRaises(base.Stop) as error:
            base.read_sync_replies(types.SimpleNamespace(rxPacket=lambda port:packets.pop(0)),Port(),dict(zip(JOINTS,range(1,7))),56,15)
        self.assertNotIsInstance(error.exception,base.ServoStatusStop)

    def test_real_streaming_reader_only_exposes_overload_payload_inside_recovery(self):
        import stream_live
        data={j:bytes(15) for j in JOINTS}
        class FakeBase:
            def read_block(self,a,w):
                error=base.ServoStatusStop(6,32,bytes(15),status_reply(6,32));error.replies=data;raise error
        mods={name:types.ModuleType(name) for name in ('lerobot','lerobot.cameras','lerobot.cameras.opencv','lerobot.cameras.opencv.camera_opencv')}
        mods['lerobot.cameras.opencv'].OpenCVCameraConfig=object;mods['lerobot.cameras.opencv.camera_opencv'].OpenCVCamera=object
        with fake_imports(mods,FakeBase):
            cls=stream_live.device_class();io=cls.__new__(cls);io.wire_fault=False;io.log=lambda r:None
            io.communication=types.SimpleNamespace(good=lambda *a:None)
            with self.assertRaises(base.ServoStatusStop):io.read_block(56,15)
            self.assertFalse(io.wire_fault)
            io.recovering_gripper=True
            self.assertEqual(io.read_block(56,15),data)
            with self.assertRaises(base.ServoStatusStop):io.read_block(42,2)
            self.assertTrue(io.wire_fault)

    def test_actual_recovery_packet_allowlist_only_id6_off_and_goals_within_cap(self):
        import stream_live,live_start
        mods={name:types.ModuleType(name) for name in ('lerobot','lerobot.cameras','lerobot.cameras.opencv','lerobot.cameras.opencv.camera_opencv')}
        mods['lerobot.cameras.opencv'].OpenCVCameraConfig=object;mods['lerobot.cameras.opencv.camera_opencv'].OpenCVCamera=object
        with fake_imports(mods,object):
            cls=stream_live.device_class();io=cls.__new__(cls)
            io.ids=dict(zip(JOINTS,range(1,7)));io.arm_bounds=command_limits();io.wire_fault=False;io.gripper_only=False
            io.torque_off_writes=io.torque_on_writes=0;io.log=lambda r:None;port=object();packets=[]
            def denied(*args):raise AssertionError('packet not exactly authorized')
            io.permission=types.SimpleNamespace(inspect=denied,closed=False,phase='read',port=port,allow_goal=True,owner=__import__('threading').get_ident())
            def sync_write(register,values,**kwargs):
                packet=live_start.exact_sync_packet(40 if register=='Torque_Enable' else 42,1 if register=='Torque_Enable' else 2,{io.ids[j]:v for j,v in values.items()})
                packets.append((register,values.copy(),io.permission.inspect(port,packet)))
            io.bus=types.SimpleNamespace(port_handler=port,sync_write=sync_write)
            with self.assertRaises(base.Stop):io._write_start_once('Torque_Enable',{'gripper':0})
            io.recovering_gripper=True
            io._write_start_once('Torque_Enable',{'gripper':0})
            self.assertEqual(packets[-1],('Torque_Enable',{'gripper':0},'torque_off'))
            self.assertEqual(io.torque_off_writes,1)
            for register,values in (('Torque_Enable',{'shoulder_pan':0}),('P_Coefficient',{'gripper':16}),('Goal_Position',{'gripper':command_limits()['gripper'][1]+1})):
                with self.assertRaises(base.Stop):io._write_start_once(register,values)
            self.assertIs(io.permission.inspect,denied)

    def test_feedback_status32_routes_recovery_even_with_normal_packet_header(self):
        import stream_live
        mods={name:types.ModuleType(name) for name in ('lerobot','lerobot.cameras','lerobot.cameras.opencv','lerobot.cameras.opencv.camera_opencv')}
        mods['lerobot.cameras.opencv'].OpenCVCameraConfig=object;mods['lerobot.cameras.opencv.camera_opencv'].OpenCVCamera=object
        with fake_imports(mods,object):
            cls=stream_live.device_class();io=cls.__new__(cls);io.gripper_overload_fault=None;io.log=lambda r:None
            status=dict.fromkeys(JOINTS,0);status['gripper']=32
            io.latch_gripper_status(dict(statuses=status))
            self.assertEqual(io.gripper_overload_fault['status'],32)
            io.gripper_overload_fault=None;status['shoulder_pan']=4
            io.latch_gripper_status(dict(statuses=status))
            self.assertIsNone(io.gripper_overload_fault)


if __name__=='__main__':unittest.main()
