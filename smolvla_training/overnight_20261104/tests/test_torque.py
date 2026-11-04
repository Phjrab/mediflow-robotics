import contextlib
import io
import types
import unittest
from unittest.mock import patch
import nov_readonly as n
import torque_so101 as t

HOME=dict(joint_names=list(n.JOINTS),state=[0.]*5+[20.],tolerance_deg=5.)
def state():return dict(torque=dict.fromkeys(n.JOINTS,0),temperatures=dict.fromkeys(n.JOINTS,30.),
    modes=dict.fromkeys(n.JOINTS,0),positions=dict(zip(n.JOINTS,HOME['state'])))
class Session:
    closed=False
    def __enter__(self):return self
    def read(self,home):return state()
    def __exit__(self,*args):Session.closed=True

class Tests(unittest.TestCase):
    def setUp(self):
        ctx=contextlib.redirect_stdout(io.StringIO());ctx.__enter__();self.addCleanup(ctx.__exit__,None,None,None)
    def test_disable_actual_cli_refused_before_open(self):
        with patch('sys.argv',['torque','--disable','--execute']),patch.object(n,'ReadSession') as session:
            with self.assertRaises(SystemExit) as error:t.main()
        self.assertEqual(error.exception.code,2);session.assert_not_called()
    def test_precheck_rejects_partial_on_home_hot_mode(self):
        for kind in ('on','home','hot','mode'):
            s=state()
            if kind=='on':s['torque']['gripper']=1
            if kind=='home':s['positions']['elbow_flex']=6
            if kind=='hot':s['temperatures']['wrist_roll']=45
            if kind=='mode':s['modes']['elbow_flex']=1
            with self.subTest(kind=kind),self.assertRaises(n.SafetyStop):t.precheck(s,HOME)
    def test_eof_before_enable_no_writes_and_closed_port(self):
        with patch('sys.argv',['torque','--enable','--execute']),patch.object(n,'ReadSession',Session),patch.object(n,'read_home',return_value=HOME),patch.object(t,'save_json'),patch('builtins.input',side_effect=EOFError),patch.object(t.operator_core,'guarded_write') as write:
            with self.assertRaises(SystemExit) as error:t.main()
        self.assertEqual(error.exception.code,2);self.assertTrue(Session.closed);write.assert_not_called()
    def test_disable_partial_on_mock_only_zero_write(self):
        class Bus:
            def __init__(self):self.torque=dict.fromkeys(n.JOINTS,0);self.torque['gripper']=1;self.writes=[]
            def read(self,reg,j,**kw):return self.torque[j] if reg=='Torque_Enable' else 0 if reg=='Operating_Mode' else HOME['state'][n.JOINTS.index(j)]
            def sync_write(self,reg,values,**kw):self.writes.append((reg,values.copy()));self.torque=values.copy()
        class Guard:
            def emit(self,row):pass
            @contextlib.contextmanager
            def permission(self,reg,values):
                assert reg=='Torque_Enable' and all(v==0 for v in values.values());yield
        api=types.SimpleNamespace(JOINTS=n.JOINTS,vector=lambda v:v,read_temperatures=lambda b:dict.fromkeys(n.JOINTS,30.))
        bus=Bus()
        with patch.object(t.operator_core.time,'sleep'):result=t.disable_mock_only(bus,api,Guard(),HOME)
        self.assertFalse(any(result.values()));self.assertEqual(len(bus.writes),1)
        self.assertEqual(bus.writes[0][0],'Torque_Enable');self.assertTrue(all(v==0 for v in bus.writes[0][1].values()))

if __name__=='__main__':unittest.main()
