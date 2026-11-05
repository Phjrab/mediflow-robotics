import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from mock_vla_stage_adapter import MockVLAStageAdapter
from test_v5 import args as packet


@contextlib.contextmanager
def existing_controller():
    root=Path(__file__).resolve().parents[1]/'fixtures/robot_controller_snapshot'
    hashes=json.loads((root/'PROVENANCE.json').read_text(encoding='utf-8'))['source_sha256']
    for name,value in hashes.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=value:raise ValueError('기존 controller 소스 스냅샷 불일치')
    package=types.ModuleType('_controller_snapshot');package.__path__=[str(root)]
    def forbidden(*args,**kwargs):raise AssertionError('실제 POSIX 장치 접근 금지')
    fcntl=types.ModuleType('fcntl');fcntl.ioctl=forbidden;fcntl.flock=forbidden;fcntl.LOCK_EX=1;fcntl.LOCK_NB=2;fcntl.LOCK_UN=3
    termios=types.ModuleType('termios');termios.TIOCEXCL=0
    with patch.dict(sys.modules,{'_controller_snapshot':package,'fcntl':fcntl,'termios':termios}):
        spec=importlib.util.spec_from_file_location('_controller_snapshot.robot',root/'robot.py')
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        yield module.RobotController


class Tests(unittest.TestCase):
    def test_existing_controller_orchestrates_mock_vla_stage_and_closes(self):
        adapter=MockVLAStageAdapter([0.]*5+[20.],[packet()])
        plan=types.SimpleNamespace(physical_motion_authorized=False,stages=('vla_chunk_mock',))
        with existing_controller() as Controller:result=Controller(adapter).run(plan)
        self.assertEqual(result,('vla_chunk_mock',));self.assertEqual(adapter.backend.register_writes,0)
        self.assertTrue(adapter.backend.closed);self.assertTrue(adapter.backend.stopped)
        self.assertEqual(adapter.events,['connect_mock_only','read_mock_state','prepare_mock_only','execute_vla_chunk_mock','stop_new_commands_no_write','close_mock_only'])

    def test_fault_uses_existing_finally_stop_close_without_goal_hold_write(self):
        p=packet();p['action']=[float('nan')]*6;adapter=MockVLAStageAdapter([0.]*5+[20.],[p])
        plan=types.SimpleNamespace(physical_motion_authorized=False,stages=('vla_chunk_mock',))
        with existing_controller() as Controller,self.assertRaises(RuntimeError):Controller(adapter).run(plan)
        self.assertEqual(adapter.backend.state_updates,0);self.assertEqual(adapter.backend.register_writes,0)
        self.assertEqual(adapter.events[-2:],['stop_new_commands_no_write','close_mock_only'])

    def test_physical_mode_refused_before_mock_connect(self):
        adapter=MockVLAStageAdapter([0.]*5+[20.],[packet()]);plan=types.SimpleNamespace(physical_motion_authorized=True,stages=('vla_chunk_mock',))
        with existing_controller() as Controller,self.assertRaises(RuntimeError):Controller(adapter).run(plan)
        self.assertEqual(adapter.events,[])


if __name__=='__main__':unittest.main()
