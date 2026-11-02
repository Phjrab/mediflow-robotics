import copy,io,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch,create_autospec
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from medicine_sorter.vla_policy_adapter import (ActionGuard,HoldRequired,Limits,MockPolicy,MockStreamingController,
    run_mock_loop,rename_observation,readiness,task_check,start_gate,vector)
from medicine_sorter.robot import RobotController
from vla_run import SyntheticProvider,synthetic_units,main as run_main
from vla_train import load_config
from vla_record import load_tasks
from vla_data import sha
from benchmark_smolvla_jetson import benchmark,summary,classify_error,main as bench_main
from jetson_preflight import collect


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.config=load_config(ROOT/'configs/vla_train.yaml');self.units=synthetic_units();self.state=np.array([0.,0.,0.,0.,0.,50.])
        self.controller=MockStreamingController(self.state);self.task=load_tasks(ROOT/'configs/vla_tasks.yaml')['combinations']['A:red']['ko'][0]
        self.cameras={'cameras':{r:{'height':4,'width':6} for r in ('wrist','top','oblique')}}
        self.provider=SyntheticProvider(self.controller,self.cameras)
        self.binding={'root':'SYNTHETIC'}
        self.evidence=dict(dataset_binding=self.binding,audit_status='PASS',unit_status='SYNTHETIC_FIXTURE',checked_at=time.monotonic(),mean=self.state.tolist(),tolerance=[1.]*6)

    def run_case(self,change=None,policy=None,**kwargs):
        if change:
            original=self.provider.read
            def changed(task):
                p=original(task);change(p);return p
            self.provider.read=changed
        policy=policy or MockPolicy([np.tile(self.state,(2,1))]*3)
        args=dict(train_config=self.config,units=self.units,limits=Limits(),meta_info={'fps':30},task=self.task,
            start_evidence=self.evidence,dataset_binding=self.binding,max_steps=2,max_time=1,sleep=lambda _:None,cameras=self.cameras)
        args.update(kwargs)
        result=run_mock_loop(policy,self.provider,self.controller,**args)
        self.assertTrue(self.controller.holds);self.assertEqual(self.controller.disconnections,[False])
        return result

    def assert_hold(self,change=None,**kwargs):
        result=self.run_case(change,**kwargs);self.assertEqual(len(self.controller.sent),0);self.assertNotEqual(result['reason'],'max_steps');return result

    def test_nominal_mock_no_device(self):
        r=self.run_case();self.assertEqual(len(self.controller.sent),2);self.assertEqual(r['motor_writes'],0);self.assertIsNone(r['automatic_success'])
    def test_nan_action(self): self.assert_hold(policy=MockPolicy([np.full((2,6),np.nan)]))
    def test_inf_action(self): self.assert_hold(policy=MockPolicy([np.full((2,6),np.inf)]))
    def test_integer_action(self): self.assert_hold(policy=MockPolicy([np.zeros((2,6),dtype=int)]))
    def test_wrong_shape(self): self.assert_hold(policy=MockPolicy([np.zeros((2,7))]))
    def test_short_chunk(self): self.assert_hold(policy=MockPolicy([np.zeros((1,6))]))
    def test_stale_frame(self): self.assert_hold(lambda p:p['frames']['wrist'].update(timestamp=time.monotonic()-1))
    def test_future_frame(self): self.assert_hold(lambda p:p['frames']['top'].update(timestamp=time.monotonic()+1))
    def test_nonfinite_timestamp(self): self.assert_hold(lambda p:p['frames']['top'].update(timestamp=float('nan')))
    def test_skew(self): self.assert_hold(lambda p:p['frames']['top'].update(timestamp=time.monotonic()-.1))
    def test_missing_frame(self): self.assert_hold(lambda p:p['frames'].pop('top'))
    def test_extra_frame(self): self.assert_hold(lambda p:p['frames'].update(extra=p['frames']['top']))
    def test_packet_task_mismatch(self): self.assert_hold(lambda p:p.update(task='different'))
    def test_wrong_image_size(self): self.assert_hold(lambda p:p['frames']['top'].update(image=np.zeros((2,2,3),np.uint8)))
    def test_missing_state(self): self.assert_hold(lambda p:p.pop('state'))
    def test_state_nonfinite(self): self.assert_hold(lambda p:p['state'].__setitem__(0,float('nan')))
    def test_state_bounds(self): self.assert_hold(lambda p:p['state'].__setitem__(0,500.))
    def test_prestart_50(self): self.assert_hold(lambda p:p['temperatures'].__setitem__(0,50.))
    def test_temperature_55_after_inference(self):
        calls=[0]
        def change(p):
            calls[0]+=1
            if calls[0]>1:p['temperatures'][0]=55
        self.assert_hold(change)
    def test_overload(self): self.assert_hold(lambda p:p.update(overload=True))
    def test_stop_button(self): self.assert_hold(lambda p:p.update(stop_requested=True))
    def test_stop_callback(self): self.assert_hold(stop=lambda:True)
    def test_ctrl_c(self):
        self.provider.read=lambda _:(_ for _ in ()).throw(KeyboardInterrupt())
        self.assertEqual(self.assert_hold()['reason'],'Ctrl+C')
    def test_first_step_hard_jump(self):
        raw=np.tile(self.state,(2,1));raw[:,0]=11.;self.assert_hold(policy=MockPolicy([raw]))
    def test_action_bounds(self):
        raw=np.tile(self.state,(2,1));raw[:,0]=100.;self.assert_hold(policy=MockPolicy([raw]))
    def test_clipping_ratio_holds_last_sent(self):
        raw=np.tile(self.state,(2,1));raw[:,0]=5.
        r=self.run_case(policy=MockPolicy([raw]),limits=Limits(clip_window=2,max_clip_rate=.5))
        self.assertEqual(len(self.controller.sent),1);self.assertIn('clipping rate',r['reason'])
        np.testing.assert_equal(self.controller.holds[-1][0],self.controller.sent[-1])
    def test_cap_clip_and_gripper_distinct_units(self):
        guard=ActionGuard(self.units,Limits());raw=self.state.copy();raw[0]=3;raw[5]=54
        target,count,_=guard.target(raw,self.state,self.state);self.assertEqual(target[0],2);self.assertEqual(target[5],52);self.assertEqual(count,2)
    def test_first_run_half_degree(self):
        raw=self.state.copy();raw[0]=1
        target,_,_=ActionGuard(self.units,Limits(cap_deg=.5)).target(raw,self.state,self.state);self.assertEqual(target[0],.5)
    def test_cannot_weaken_cap(self):
        with self.assertRaises(ValueError):Limits(cap_deg=3)
    def test_unit_unknown(self): self.units.evidence_status='UNKNOWN';self.assert_hold()
    def test_gripper_unit_unknown(self): self.units.policy_gripper_unit='degree';self.assert_hold()
    def test_joint_order(self): self.units.joint_names=list(reversed(self.units.joint_names));self.assert_hold()
    def test_calibration_coordinate(self): self.units.calibration_coordinate='other';self.assert_hold()
    def test_explicit_radian_roundtrip(self):
        self.units.policy_arm_unit='radian';raw=self.state.copy();raw[0]=np.pi/180
        target=self.units.convert(raw);self.assertAlmostEqual(target[0],1);np.testing.assert_allclose(self.units.policy_state(target),raw)
    def test_start_missing(self): self.assert_hold(start_evidence=None)
    def test_start_wrong_dataset(self): self.evidence['dataset_binding']={};self.assert_hold()
    def test_start_stale(self): self.evidence['checked_at']-=3;self.assert_hold()
    def test_start_distribution(self): self.evidence['mean'][0]=20.;self.assert_hold()
    def test_blank_task(self): self.assert_hold(task=' ')
    def test_unregistered_task(self): self.assert_hold(task='unknown')
    def test_unseen_warn_not_gate_bypass(self):
        self.units.evidence_status='UNKNOWN';r=self.assert_hold(task='unknown',allow_unseen=True);self.assertTrue(r['task']['unseen_task'])
    def test_timeout_late_action_discard(self):
        state=self.state.copy()
        class Slow(MockPolicy):
            def predict(self,obs):time.sleep(.04);return np.tile(state,(2,1))
        r=self.assert_hold(policy=Slow([]),limits=Limits(timeout_s=.005));time.sleep(.06)
        self.assertIn('timeout',r['reason']);self.assertEqual(self.controller.sent,[])
    def test_stop_while_inference(self):
        flag=threading.Event();state=self.state.copy()
        class StopPolicy(MockPolicy):
            def predict(self,obs):flag.set();time.sleep(.02);return np.tile(state,(2,1))
        self.assert_hold(policy=StopPolicy([]),stop=flag.is_set)
    def test_shadow_sim_no_target_writes(self):
        r=self.run_case(shadow_sim=True);self.assertEqual(self.controller.sent,[]);self.assertTrue(all(s['sent_action'] is None for s in r['records']))
    def test_live_fake_proof_cannot_bypass(self):
        for mode in ('live','shadow'):
            self.assertEqual(readiness(mode,True,{'all_verified':True})['status'],'BLOCKED')
        self.assertIn('--execute required',readiness('live')['blockers'])
    def test_existing_controller_api_not_invented(self):
        mock=create_autospec(RobotController,instance=True,spec_set=True)
        self.assertFalse(hasattr(mock,'submit_bounded_target'));self.assertFalse(hasattr(mock,'hold_last'))
    def test_rename_source_to_model_observation_only(self):
        obs={'observation.images.'+r:r for r in ('wrist','top','oblique')};obs.update(action='unchanged',task='unchanged')
        result=rename_observation(obs,self.config)
        for k,v in self.config['rename_map'].items():self.assertEqual(result[v],obs[k]);self.assertNotIn(k,result)
        self.assertEqual(result['action'],'unchanged');self.assertEqual(result['task'],'unchanged')
    def test_common_mapping_not_hardcoded_in_adapter(self):
        text=(ROOT/'medicine_sorter/vla_policy_adapter.py').read_text()
        self.assertNotIn('observation.images.camera1',text)
        self.assertNotIn('import serial',text)
    def test_official_source_hash_and_rename_direction(self):
        ev=json.loads((ROOT/'configs/vla_inference_source_evidence.json').read_text());source=Path(self.config['official_source'])
        for path,digest in ev['official_files'].items():self.assertEqual(sha(source/path),digest)
        text=(source/'src/lerobot/processor/rename_processor.py').read_text();self.assertIn('processed_obs[self.rename_map[key]] = value',text)


class PrepTests(unittest.TestCase):
    def test_benchmark_cli_nonfinite_and_counts_rejected(self):
        for args in (['--fps','nan'],['--fps','inf'],['--warmup','-1'],['--repeats','0']):
            with self.subTest(args=args),patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):bench_main(args)
    def test_policy_path_default_no_download(self):
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            bench_main([]);self.assertEqual(json.loads(out.getvalue())['policy_path'],'lerobot/smolvla_base')
    def test_benchmark_warmup_excluded(self):
        clock=iter([0,1,2,4,5,8]);calls=[]
        result=benchmark(lambda _:calls.append(1),{},n=2,fps=10,warmup=1,repeats=2,clock=lambda:next(clock))
        self.assertEqual(len(calls),3);self.assertEqual(result['p50_s'],2.5);self.assertEqual(result['max_s'],3)
    def test_benchmark_unmeasured_null(self):self.assertIsNone(summary([],50,30)['p95_s'])
    def test_benchmark_synthetic_label(self):self.assertEqual(summary([.1],50,30,synthetic=True)['status'],'SYNTHETIC_NOT_JETSON')
    def test_benchmark_error_classes(self):
        self.assertEqual(classify_error(ImportError()),'MISSING_EXTRAS');self.assertEqual(classify_error(RuntimeError('CUDA out of memory')),'OOM')
        self.assertEqual(classify_error(RuntimeError('CUDA driver')),'TORCH_CUDA_OR_DTYPE');self.assertEqual(classify_error(RuntimeError('offline cache')),'LOCAL_WEIGHTS_OR_NETWORK_BLOCKED')
    def test_preflight_dry_no_queries(self):
        with patch('jetson_preflight.platform.platform',side_effect=AssertionError('probe')):r=collect(False)
        self.assertEqual(r['device_open'],0);self.assertIsNone(r['by_id']);self.assertIsNone(r['cuda_build'])
    def test_mock_cli_manifest_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as t,patch('sys.stdout',new_callable=io.StringIO),patch('medicine_sorter.vla_policy_adapter.SmolVLAWrapper.__init__',side_effect=AssertionError('model')):
            out=Path(t)/'new';self.assertEqual(run_main(['--mode','mock','--shadow-sim','--out',str(out),'--steps','1']),0)
            m=json.loads((out/'manifest.json').read_text());self.assertEqual(m['checkpoint_status'],'MOCK_NO_MODEL')
            with self.assertRaises(SystemExit):run_main(['--mode','mock','--out',str(out)])
    def test_live_cli_refuses_without_model_or_device(self):
        with patch('sys.stdout',new_callable=io.StringIO):self.assertEqual(run_main(['--mode','live','--execute']),2)
    def test_benchmark_plan_imports_no_model(self):
        with patch('sys.stdout',new_callable=io.StringIO) as out,patch('benchmark_smolvla_jetson.SmolVLAWrapper',side_effect=AssertionError('model')):
            self.assertEqual(bench_main([]),0);self.assertEqual(json.loads(out.getvalue())['results']['fp32']['status'],'NOT_RUN')


if __name__=='__main__':unittest.main()
