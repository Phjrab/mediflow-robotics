import ast
import copy
from pathlib import Path
import unittest
from latency_dependencies import prepare
from fast_postprocess import bulk_actions,validate_post_config
from build_fast_variant import render
from benchmark_saved_observation import deny_robot_device_open
from batch_motor_read import torque_modes,health,JOINTS

class Tensor:
    def __init__(self,rows,shape=(1,50,6)):self.rows=rows;self.shape=shape
    def detach(self):return self
    def float(self):return self
    def cpu(self):return self
    def reshape(self,*shape):return self
    def tolist(self):return self.rows

class FastPostTests(unittest.TestCase):
    def test_single_saved_pipeline_call_and_order(self):
        rows=[[float(100*i+j) for j in range(6)] for i in range(50)]
        normalized=Tensor(rows);calls=[]
        def pipeline(value):calls.append(value);return Tensor(rows)
        actual=bulk_actions(pipeline,normalized)
        self.assertEqual(actual,rows);self.assertEqual(calls,[normalized])
    def test_bad_input_shape_never_calls_pipeline(self):
        calls=[]
        with self.assertRaises(ValueError):bulk_actions(lambda x:calls.append(x),Tensor([],shape=(50,6)))
        self.assertEqual(calls,[])
    def test_nan_inf_and_bad_output_shape_refused(self):
        for value in (float("nan"),float("inf")):
            rows=[[0.]*6 for _ in range(50)];rows[20][2]=value
            with self.assertRaises(ValueError):bulk_actions(lambda x:Tensor(rows),Tensor([]))
        with self.assertRaises(ValueError):bulk_actions(lambda x:Tensor([],shape=(1,49,6)),Tensor([]))
    def test_stateful_or_different_saved_pipeline_refused(self):
        cfg={"steps":[{"registry_name":"unnormalizer_processor","config":{"features":{"action":{"shape":[6]}},"norm_map":{"ACTION":"MEAN_STD"}}},{"registry_name":"device_processor","config":{"device":"cpu"}}]}
        validate_post_config(cfg)
        wrong=copy.deepcopy(cfg);wrong["steps"].append({"registry_name":"absolute_actions"})
        with self.assertRaises(ValueError):validate_post_config(wrong)

class SourceTests(unittest.TestCase):
    def test_main_and_unmodified_gates_preserved(self):
        root=prepare();source=(root/"so101_first_action_test_safe_v6_dry.py").read_text(encoding="utf-8")
        target=render(source)
        def nodes(text):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name not in ("infer_chunk","read_motor_state","read_health")}
        self.assertEqual(nodes(source),nodes(target))
    def test_python310_syntax(self):
        for path in Path(__file__).parent.glob("*.py"):ast.parse(path.read_text(encoding="utf-8"),feature_version=(3,10))
    def test_offline_device_open_guard(self):
        for path in ("/dev/ttyACM0","/dev/serial/by-id/robot","/dev/video6","/dev/v4l/by-path/camera"):
            with self.assertRaises(RuntimeError):deny_robot_device_open("open",(path,))
    def test_cuda_device_and_saved_input_are_allowed(self):
        deny_robot_device_open("open",("/dev/nvidia0",))
        deny_robot_device_open("open",("saved/frame.png",))

class BatchReadTests(unittest.TestCase):
    def bus(self,bad_register=None,bad_value=None):
        calls=[]
        class Bus:
            def sync_read(self,name,**kwargs):
                calls.append((name,kwargs));v=30 if name=="Present_Temperature" else 0
                values=dict.fromkeys(JOINTS,v)
                if name==bad_register:values[JOINTS[0]]=bad_value
                return values
        return Bus(),calls
    def test_same_conditions_five_group_requests(self):
        bus,calls=self.bus();torque_modes(bus);health(bus)
        self.assertEqual(len(calls),5)
        self.assertTrue(all(kw=={"normalize":False,"num_retry":0} for _,kw in calls))
    def test_on_or_hot_stops_before_next_request(self):
        bus,calls=self.bus("Torque_Enable",1)
        with self.assertRaises(ValueError):torque_modes(bus)
        self.assertEqual(len(calls),1)
        bus,calls=self.bus("Present_Temperature",45)
        with self.assertRaises(ValueError):health(bus)
        self.assertEqual(len(calls),1)
    def test_mode_load_status_nan_inf_and_bool_refused(self):
        for register,value in (("Operating_Mode",1),("Present_Load",200),("Status",1),("Present_Temperature",float("nan")),("Torque_Enable",True),("Present_Load",float("inf"))):
            bus,_=self.bus(register,value)
            with self.assertRaises(ValueError):
                if register in ("Torque_Enable","Operating_Mode"):torque_modes(bus)
                else:health(bus)

if __name__=="__main__":unittest.main()
