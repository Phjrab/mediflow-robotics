"""보존 스냅샷에서 현행 패키지를 구성한다. 하드웨어 접근 없음."""
import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LIVE=ROOT/'live'; DEPS=LIVE/'deps'
def edit(path, old, new):
    text=path.read_text(encoding='utf-8')
    if old not in text: raise RuntimeError('수정 위치 없음: '+str(path)+' '+old[:60])
    path.write_text(text.replace(old,new),encoding='utf-8',newline='\n')
def function(path,name,code):
    text=path.read_text(encoding='utf-8');lines=text.splitlines(keepends=True)
    n=next(n for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    path.write_text(''.join(lines[:n.lineno-1])+code+'\n'+''.join(lines[n.end_lineno:]),encoding='utf-8',newline='\n')

def main():
    home=json.loads((ROOT/'_archive/20261110/source_snapshot/home_state_so101.json').read_text())
    ranges=dict(zip(home['joint_names'],[[755,3448],[796,3113],[880,3087],[936,3225],[86,3643],[413,2047]]))
    limits={k:list(v) for k,v in ranges.items()};limits['elbow_flex'][1]=3082;limits['shoulder_lift'][0]=801
    c=dict(schema=1,port='/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AE6058306-if00',leader_serial='5AE6085272',
      checkpoint='~/smolvla_model/pretrained_model',calibration='~/.cache/huggingface/lerobot/calibration/robots/so_follower/so-101.json',
      model_sha256='b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654',
      calibration_sha256='2f74c1303b2fce03576eff539329e1185a652e2d551caa1ec82b026dafe29c2e',
      joint_names=home['joint_names'],home={**home,'tolerance_deg':2.0},calibration_ranges=ranges,limits=limits,
      limit_evidence={'elbow_flex':'보정 상한3087에서5틱 안쪽. 명령 상한3082로 통일',
                      'shoulder_lift':'보정 하한796에서5틱 안쪽. 실제 스토퍼 위치·하중은 미확인'},
      speed=.33,control_hz=60,model_hz=30,duration=180,overlap_actions=10,lookahead_factor=1.3,
      startup={'max_deg_s':10,'camera_first_frame_s':5,'warmup_s':3,'torque_attempts':3},
      temperature={'wait_at_c':50,'resume_below_c':45,'stop_c':55,'poll_s':10},
      gripper={'positive_direction':'open','relief_points':4,'blocked_gap_points':5,'blocked_playback_s':.5},
      task='Put medicine bottle A into the blue basket',
      cameras={'end_effector':{'path':'/dev/v4l/by-path/platform-3610000.usb-usb-0:2.2:1.0-video-index0','model_key':'camera1','fourcc':None},
               'ceiling_vertical':{'path':'/dev/v4l/by-path/so101-astra-loopback-video-index0','model_key':'camera2','fourcc':None},
               'ceiling_oblique':{'path':'/dev/v4l/by-path/platform-3610000.usb-usb-0:1.1.2:1.3-video-index0','model_key':'camera3','fourcc':'YUYV'}})
    (LIVE/'config.yaml').write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # 정책 프로세스와 런타임은 이제 이 패키지의 모듈만 사용한다.
    for name in ['first_trial_dependencies.py','latency_dependencies.py']:
        (DEPS/name).write_text('from pathlib import Path\nimport sys\ndef prepare():\n p=Path(__file__).resolve().parent\n if str(p) not in sys.path:sys.path.insert(0,str(p))\n return p\n',encoding='utf-8')
    function(DEPS/'trial_runtime.py','static_context','''def static_context():
    from config_runtime import load,path,contract
    import observation_contract as observations
    c=load();checkpoint=path(c['checkpoint']);calibration=path(c['calibration'])
    if sha(calibration)!=c['calibration_sha256'] or sha(checkpoint/'model.safetensors')!=c['model_sha256']:
        raise Hold('모델/보정 해시 불일치')
    mapping=observations.saved_mapping(checkpoint)
    expected={'observation.images.'+name:'observation.images.'+row['model_key'] for name,row in c['cameras'].items()}
    if mapping!=expected:raise Hold('저장 전처리의 카메라 대응 불일치')
    files={str(calibration):sha(calibration)}
    for name in ['config.json','policy_preprocessor.json','policy_postprocessor.json']:
        p=checkpoint/name;files[str(p)]=sha(p)
    return dict(checkpoint=checkpoint,calibration=calibration,home=c['home'],contract=contract(c),mapping=mapping,
                units=Path.home()/'units_so101.json',homefile=Path.home()/'home_state_so101.json',files=files,stop_inventory={})
''')
    function(DEPS/'trial_runtime.py','check_context','''def check_context(context):
    for name,digest in context['files'].items():
        if sha(Path(name))!=digest:raise Hold('실행 근거 파일 변경: '+Path(name).name)
''')
    function(DEPS/'device_preflight.py','inspect','''def inspect():
    from config_runtime import load
    c=load();result={'devices':{}}
    for key,cmd in [('lelab',['systemctl','--user','is-active','lelab-jetson-py310.service']),('astra',['systemctl','is-active','astra-v4l2-bridge.service'])]:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=8);result[key]=r.stdout.strip()
    paths={'serial':c['port'],**{k:v['path'] for k,v in c['cameras'].items()}}
    for role,device in paths.items():
        r=subprocess.run(['sudo','-n','fuser',device],capture_output=True,text=True,timeout=8)
        ok=allowed_owners(r.returncode,r.stdout,r.stderr,role=='ceiling_vertical',lambda pid:(Path('/proc')/pid/'cgroup').read_text())
        result['devices'][role]={'exists':Path(device).exists(),'owners_ok':ok and Path(device).exists(),'query_code':r.returncode}
    result['all_safe']=result['lelab']=='inactive' and result['astra']=='active' and all(v['owners_ok'] for v in result['devices'].values())
    return result
''')
    (DEPS/'probe_current_state.py').write_text("from config_runtime import load\nPORT=load()['port']\n",encoding='utf-8')
    p=DEPS/'direct_live.py'
    edit(p,'ELBOW_MAX = 3082',"from config_runtime import load as load_config\nELBOW_MAX = load_config()['limits']['elbow_flex'][1]")
    edit(p,"sample['temperatures'][j] >= 55","sample['temperatures'][j] >= load_config()['temperature']['stop_c']")
    p=LIVE/'stream_live.py'
    edit(p,'OVERLAP = 10',"from config_runtime import load as load_config\nCONFIG = load_config()\nOVERLAP = CONFIG['overlap_actions']")
    for old,new in [('LOOKAHEAD = 1.3',"LOOKAHEAD = CONFIG['lookahead_factor']"),('MODEL_HZ = 30.0',"MODEL_HZ = CONFIG['model_hz']"),('DEFAULT_SPEED = 0.33',"DEFAULT_SPEED = CONFIG['speed']"),('CONTROL_HZ = 60',"CONTROL_HZ = CONFIG['control_hz']"),('ELBOW_INNER_LIMIT = base.ELBOW_MAX - 5',"ELBOW_INNER_LIMIT = CONFIG['limits']['elbow_flex'][1]")]:edit(p,old,new)
    edit(p,'position - 4',"position - CONFIG['gripper']['relief_points']")
    edit(p,'position - target < 5',"position - target < CONFIG['gripper']['blocked_gap_points']")
    edit(p,'>= 0.5',">= CONFIG['gripper']['blocked_playback_s']")
    edit(p,"warmup_s=3, backend=200,\n                    fourcc='YUYV' if name=='ceiling_oblique' else None", "warmup_s=CONFIG['startup']['warmup_s'], backend=200,\n                    fourcc=CONFIG['cameras'][name]['fourcc']")
    # 첫 프레임 확인 뒤 같은5초 한도 내에서 가능한 만큼 예열 프레임을 버린다.
    edit(p,"if camera.latest_frame is not None:\n                            break", "if camera.latest_frame is not None and time.monotonic() >= deadline - 2:\n                            break")
    edit(p,"self.arm_bounds = self.raw_bounds.copy()", "self.raw_bounds = {j:tuple(CONFIG['limits'][j]) for j in base.JOINTS}\n            self.arm_bounds = self.raw_bounds.copy()")
    edit(p,"out = Path.home() / ('vla_stream_blue_' + str(time.time_ns()))", "out = Path(__file__).resolve().parents[1] / 'runs' / time.strftime('%Y%m%d_%H%M%S')\n    if out.exists():out=out.with_name(out.name+'_'+str(time.time_ns()))")
    edit(p,"'temperature_c': sample['temperatures']", "'temperature_c': sample['temperatures']") if False else None
    edit(p,"'thermal_recheck'] = io.thermal_recheck", "'thermal_recheck'] = io.thermal_recheck\n                result['startup_temperatures_c'] = getattr(io,'initial_temperatures',{})")
    edit(p,"temperature = sample['temperatures']['gripper']", "if not hasattr(self,'initial_temperatures'):self.initial_temperatures=dict(sample['temperatures'])\n            temperature = sample['temperatures']['gripper']")
    edit(p,"return 'NOT_AUTHORIZED_OR_WIRE_FAULT'", "return 'NOT_AUTHORIZED_OR_WIRE_FAULT'") if False else None
    # 시작 허용은 고정 틱 여유가 아닌 자동 복구다. 화면과 같은 설정을 읽는다.
    (LIVE/'start_position_rules.py').write_text("from config_runtime import load,criteria\nHOME_TOLERANCE=load()['home']['tolerance_deg']\ndef start_criteria(limits):return criteria()\ndef validate_position(present_raw,limits):\n for j in limits:\n  v=present_raw.get(j)\n  if type(v) is not int or not 0<=v<=4095:raise ValueError('위치 형식 오류: '+j)\n",encoding='utf-8')
    # simulation도 같은 설정과 실제 실행기 시작/stream 함수를 사용한다.
    p=ROOT/'sim/startup_simulation.py'
    edit(p,"HOME_TICKS = (2059, 843, 3082, 2575, 1839, 483)","from config_runtime import load\nCONFIG=load()\nHOME_TICKS=tuple(round((v*4095/360+(lo+hi)/2) if i<5 else lo+v*(hi-lo)/100) for i,(v,(lo,hi)) in enumerate(zip(CONFIG['home']['state'],CONFIG['calibration_ranges'].values())))")
    edit(p,"'slow_inference', 'blocked_gripper', 'ctrl_c', 'overheat')","'slow_inference', 'blocked_gripper', 'ctrl_c', 'overheat','usb_disconnect','elbow_plus_1','elbow_plus_5','elbow_plus_20','wrist_plus10','wrist_minus10')")
    edit(p,"self.target = dict(self.present)","if case.startswith('elbow_plus_'):self.present['elbow_flex']=CONFIG['limits']['elbow_flex'][1]+int(case.rsplit('_',1)[1])\n        if case.startswith('wrist_'):self.present['wrist_roll']+=round((10 if case=='wrist_plus10' else -10)/TICK_SIZE[4])\n        self.target = dict(self.present)")
    edit(p,"self.arm_bounds['gripper'] =", "self.arm_bounds['gripper'] =") if False else None
    edit(p,"self.policy_active = False","self.arm_bounds={j:tuple(row) for j,row in CONFIG['limits'].items()}\n        self.raw_bounds=self.arm_bounds.copy()\n        self.policy_active = False")
    edit(p,"sample = self.feedback()\n        base.feedback_gate(sample)","if self.case=='usb_disconnect' and self.packet_count>=20:raise OSError('팔로워 USB 끊김')\n        sample = self.feedback()\n        base.feedback_gate(sample)")
    edit(p,"base.ELBOW_MAX - 5","CONFIG['limits']['elbow_flex'][1]")
    edit(p,"'overheat': ('HOLD', '모터 상태코드/온도 중단')","'overheat': ('HOLD', '모터 상태코드/온도 중단'), 'usb_disconnect':('HOLD','팔로워 USB 끊김')")
    edit(p,"out = Path.home() / f'vla_camera_dry_{time.time_ns()}'", "out = Path(__file__).resolve().parents[1] / 'runs' / f'DRY_{time.time_ns()}'")
    for name in ['startup_simulation']:
        import shutil
        shutil.copy2(ROOT/'sim'/(name+'.py'),DEPS/(name+'.py'))
    for p in ROOT.rglob('*.py'):
        if '_archive' not in p.parts:compile(p.read_text(encoding='utf-8'),str(p),'exec')
    print('BUILD_SYNTAX_PASS')
if __name__=='__main__':main()
