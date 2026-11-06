import tempfile
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
from goal_crosscheck import JOINTS,crosscheck

class IO:
    def __init__(self):
        self.calls=[];self.health_calls=0;self.fail=False
        self.permission=SimpleNamespace(goal_packets=0,read_packets=0,blocked_packets=0)
        owner=self
        class Bus:
            calibration={j:SimpleNamespace(range_min=100,range_max=3000) for j in JOINTS}
            def read(self,name,joint,**kwargs):
                owner.calls.append((name,joint,kwargs))
                return 1000 if name=="Present_Position" else 0
            def sync_write(self,*args,**kwargs):raise AssertionError("목표 쓰기 금지")
        self.robot=SimpleNamespace(bus=Bus())
    def health_snapshot(self):
        self.health_calls+=1
        if self.fail:raise RuntimeError("토크ON")
        return {name:dict.fromkeys(JOINTS,30 if name=="temperatures" else 0) for name in ("torque","modes","temperatures","loads","statuses")}
    def snapshot(self):return dict(state=[0.]*6,moving=False)
    def raw(self,name):return dict.fromkeys(JOINTS,0)
    def check_records(self):pass

class CrosscheckTests(unittest.TestCase):
    def context(self,path):return dict(home={},calibration=path)
    def test_individual_goal_read_and_no_writes(self):
        io=IO()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"cal.json";p.write_text('{}')
            with patch.dict(sys.modules,{"first_trial_core":SimpleNamespace(home_gate=lambda *args:None)}):report=crosscheck(io,self.context(p))
        self.assertEqual(report["register_writes"],0)
        self.assertEqual([r["goal_individual_raw"] for r in report["joints"]],[0]*6)
        self.assertEqual(len(io.calls),18)
        self.assertTrue(all(kw==dict(normalize=False,num_retry=0) for _,_,kw in io.calls))
    def test_torque_fault_no_more_register_reads(self):
        io=IO();io.fail=True
        with patch.dict(sys.modules,{"first_trial_core":SimpleNamespace(home_gate=lambda *args:None)}),self.assertRaises(RuntimeError):crosscheck(io,{"home":{}})
        self.assertEqual(io.calls,[])
    def test_field_stop_blocks_main_before_files_or_device_access(self):
        import goal_crosscheck
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/"operator_candidate").mkdir();(root/"operator_candidate/FIELD_STOP.json").write_text('{}')
            output=root/"never_created.json"
            with patch.object(goal_crosscheck,"__file__",str(root/"goal_crosscheck.py")),patch.object(sys,"argv",["goal_crosscheck.py","--out",str(output)]),self.assertRaisesRegex(RuntimeError,"추가 serial 진단 금지"):
                goal_crosscheck.main()
            self.assertFalse(output.exists())

if __name__=="__main__":unittest.main()
