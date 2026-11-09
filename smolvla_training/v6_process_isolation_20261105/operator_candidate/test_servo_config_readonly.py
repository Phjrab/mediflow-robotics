import unittest
import contextlib
import io
from unittest.mock import patch
from types import SimpleNamespace
from first_trial_core import JOINTS,Hold
from servo_config_readonly import MANDATORY,register_contract,inspect_bus

class Bus:
    def __init__(self):self.is_connected=False;self.calls=[];self.port_handler=SimpleNamespace(ser=None);self.fail=None;self.torque=0;self.temp=30;self.mode=0
    def connect(self,handshake):self.calls.append(('connect',handshake));self.is_connected=True
    def disconnect(self,disable_torque):self.calls.append(('disconnect',disable_torque));self.is_connected=False
    def read(self,register,joint,normalize,num_retry):
        self.calls.append(('read',register,joint,normalize,num_retry))
        if register==self.fail:raise OSError('fixture read failure')
        return {'Torque_Enable':self.torque,'Present_Temperature':self.temp,'Operating_Mode':self.mode,'Present_Position':2048}.get(register,2)
    def write(self,*args,**kwargs):raise AssertionError('쓰기 금지')

class Tests(unittest.TestCase):
    def test_only_table_verified_names_and_widths_are_selected(self):
        selected,unsupported=register_contract({**MANDATORY,'P_Coefficient':(21,1),'Acceleration':(41,1),'Goal_Velocity':(46,4)})
        self.assertEqual(selected['P_Coefficient'],(21,1));self.assertNotIn('CW_Dead_Zone',selected)
        self.assertEqual(unsupported['CW_Dead_Zone'],'NOT_FOUND');self.assertEqual(unsupported['Goal_Velocity'],'UNVERIFIED_FORMAT')
    def test_wrong_mandatory_address_refused(self):
        with self.assertRaises(Hold):register_contract({**MANDATORY,'Torque_Enable':(41,1)})
    def test_success_reads_only_and_disconnect_does_not_disable_torque(self):
        bus=Bus();result=inspect_bus(bus,{**MANDATORY,'P_Coefficient':(21,1)})
        self.assertEqual(result['status'],'SERVO_CONFIG_READONLY_COMPLETE');self.assertTrue(result['port_closed'])
        self.assertEqual(bus.calls[0],('connect',False));self.assertEqual(bus.calls[-1],('disconnect',False))
        reads=[r for r in bus.calls if r[0]=='read'];self.assertEqual(len(reads),42);self.assertTrue(all(r[-2:]==(False,0) for r in reads))
    def test_first_on_torque_stops_without_more_reads(self):
        bus=Bus();bus.torque=1;result=inspect_bus(bus,MANDATORY)
        self.assertEqual(result['error']['observed']['code'],'TORQUE_NOT_OFF');self.assertEqual(len([r for r in bus.calls if r[0]=='read']),1);self.assertTrue(result['port_closed'])
    def test_explicit_torque_on_review_reads_all_six_without_writing(self):
        bus=Bus();bus.torque=1
        result=inspect_bus(bus,{**MANDATORY,'P_Coefficient':(21,1)},allow_torque_on=True)
        self.assertEqual(result['status'],'SERVO_CONFIG_READONLY_COMPLETE')
        self.assertEqual(result['values']['Torque_Enable'],dict.fromkeys(JOINTS,1))
        self.assertEqual(result['values']['P_Coefficient'],dict.fromkeys(JOINTS,2))
        self.assertEqual(bus.calls[-1],('disconnect',False))
    def test_temperature_and_mode_abort_and_close(self):
        for key,value,code in [('temp',45,'TEMPERATURE'),('mode',1,'MODE')]:
            bus=Bus();setattr(bus,key,value);result=inspect_bus(bus,MANDATORY)
            self.assertEqual(result['error']['observed']['code'],code);self.assertTrue(result['port_closed'])
            self.assertFalse(any(r[0]=='read' and r[1]=='Present_Position' for r in bus.calls))
    def test_read_error_no_retry_closes(self):
        bus=Bus();bus.fail='Torque_Enable';result=inspect_bus(bus,MANDATORY)
        self.assertEqual(result['status'],'HOLD');self.assertEqual(len([r for r in bus.calls if r[0]=='read']),1);self.assertTrue(result['port_closed'])
    def test_exclusive_claim_failure_reads_nothing_and_closes(self):
        bus=Bus()
        def fail():raise OSError('occupied')
        result=inspect_bus(bus,MANDATORY,exclusive=fail)
        self.assertFalse(any(r[0]=='read' for r in bus.calls));self.assertTrue(result['port_closed'])
    def test_seal_precedes_disconnect(self):
        bus=Bus();result=inspect_bus(bus,MANDATORY,seal=lambda:bus.calls.append(('seal',)))
        self.assertEqual(bus.calls[-2:],[('seal',),('disconnect',False)]);self.assertTrue(result['port_closed'])
    def test_interrupt_stops_reads_and_closes(self):
        bus=Bus()
        def interrupt(*args,**kwargs):raise KeyboardInterrupt()
        bus.read=interrupt;result=inspect_bus(bus,MANDATORY)
        self.assertEqual(result['error']['kind'],'KeyboardInterrupt');self.assertTrue(result['port_closed'])
    def test_disconnect_failure_still_forces_port_closed(self):
        bus=Bus()
        def closed():bus.is_connected=False;bus.port_handler.ser.is_open=False
        bus.port_handler.ser=SimpleNamespace(is_open=True,close=closed)
        def fail(disable_torque):raise OSError('close failed')
        bus.disconnect=fail;result=inspect_bus(bus,MANDATORY)
        self.assertEqual(result['status'],'HOLD');self.assertTrue(result['port_closed']);self.assertTrue(result['cleanup_errors'])
    def test_default_plan_has_no_hardware_import(self):
        import builtins
        import servo_config_readonly as module
        original=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name.split('.')[0] in {'trial_runtime','lerobot','serial','cv2','torch'}:raise AssertionError('device dependency imported')
            return original(name,*args,**kwargs)
        with patch('sys.argv',['servo_config_readonly.py']),patch('builtins.__import__',side_effect=guarded),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(module.main(),0)
        self.assertIn('PLAN_ONLY',out.getvalue())
    def test_execute_without_present_refused_before_dependencies(self):
        import servo_config_readonly as module
        with patch('sys.argv',['servo_config_readonly.py','--execute']),self.assertRaises(Hold):module.main()
    def test_conflicting_sdk_fields_are_skipped_and_raw_bytes_kept_separate(self):
        selected,unsupported=register_contract({**MANDATORY,'Minimum_Startup_Force':(24,2),'Goal_Position_2':(71,2)})
        self.assertNotIn('Minimum_Startup_Force',selected);self.assertNotIn('Goal_Position_2',selected)
        bus=Bus();reads=[]
        def raw(address,size,joint):reads.append((address,size,joint));return address
        result=inspect_bus(bus,selected,raw_read=raw)
        self.assertEqual(result['status'],'SERVO_CONFIG_READONLY_COMPLETE');self.assertEqual(len(reads),48)
        self.assertEqual(result['raw_profile_values']['startup_byte24'][JOINTS[0]],24)
        self.assertEqual(result['raw_profile_values']['integral_limit_byte25'][JOINTS[0]],25)
        self.assertFalse(any(address==71 for address,size,joint in reads))
    def test_raw_profile_failure_stops_without_retry_and_closes(self):
        bus=Bus();calls=[]
        def fail(address,size,joint):calls.append(address);raise OSError('fixture')
        result=inspect_bus(bus,MANDATORY,raw_read=fail)
        self.assertEqual(result['status'],'HOLD');self.assertEqual(calls,[0]);self.assertTrue(result['port_closed'])

if __name__=='__main__':unittest.main()
