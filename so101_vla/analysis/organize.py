"""삭제 없이 이동하고 목록을 남긴다. 모델·보정·데이터 원본은 이동하지 않는다."""
import argparse
import json
import os
import re
import shutil
from pathlib import Path
from datetime import datetime,timezone,timedelta

def protected(p):
    if p.is_symlink():return False
    if p.name in ['so-101.json','model.safetensors','smolvla_model','venvs','miniforge3','miniconda3','models','so101-medicine-bootstrap','smolvla_three_color_020000.zip','so101_pilot_dataset_ready.tar.gz']:return True
    if p.is_dir():
        for dp,ds,fs in os.walk(p):
            ds[:]=[d for d in ds if d not in ['.git','node_modules','__pycache__']]
            if 'so-101.json' in fs or 'model.safetensors' in fs:return True
            if Path(dp).name=='meta' and 'info.json' in fs:return True
    return False

def run_name(p):
    candidates=[p/'summary.json',p/'result.json',p/'RESULT.json']
    row={}
    for f in candidates:
        try:row=json.loads(f.read_text(encoding='utf-8'));break
        except (OSError,ValueError):pass
    steps=row.get('steps',row.get('motor_goal_packets'))
    status=str(row.get('status','UNKNOWN'))
    reason=str(row.get('reason',row.get('error','')))
    stamp=p.stat().st_mtime
    match=re.search(r'(179\d{16})',p.name)
    if match:stamp=int(match[1])/1e9
    day=datetime.fromtimestamp(stamp,timezone(timedelta(hours=9))).strftime('%Y%m%d_%H%M%S')
    kind='temperature' if ('온도' in reason or 'temperature' in reason) else 'communication' if ('통신' in reason or '응답' in reason) else ''
    suffix=re.sub(r'[^A-Za-z0-9_-]','_',p.name)[-36:]
    return f'{day}_{status}_{kind}_{steps if steps is not None else "UNKNOWN"}steps_{suffix}',steps

def plan(base,root,platform):
    moves=[];skips=[];archive=root/'_archive/20261110'/platform
    if platform=='windows':
        candidates=[base/'tmp',base/'vla_prep_local',base/'output']
        for p in (base/'reports').iterdir() if (base/'reports').exists() else []:
            candidates.append(p)
        for p in (base/'.local').iterdir() if (base/'.local').exists() else []:
            if p.name not in ['models']:candidates.append(p)
    else:
        prefixes=('vla_','first_vla_','smolvla_dry','smolvla_agent','v6_','nov_','servo','torque_','home_align','home_arm','home_monitor','run_servo','run_smolvla','switch_to_astra','patch_astra','patch_realsense','thermal_','vendor_temp','vendor_temperature','elbow_','bundle_','goal_','motion_limits','control_timing','lelab-direct','lelab-present','lelab-teleop','lerobot_base_trace','target67','readonly','first_trial','read_goal','infer_test','load_test','check_v3','collect_home','units_so101','so101-','lelab-safe','lelab_start','lerobot-')
        candidates=[p for p in base.iterdir() if p.name!='so101_vla' and (p.name.startswith(prefixes) or p.name=='__pycache__')]
    for p in candidates:
        if not p.exists() or p.is_symlink():continue
        if protected(p):skips.append({'path':str(p),'reason':'모델·보정·원본 데이터 또는 그 상위 폴더'});continue
        # 서비스가 아직 참조하는 YOLO/LeLab 설치 경로는 이 대상 목록에 넣지 않는다.
        is_run=p.is_dir() and p.name.startswith(('vla_stream_blue_','vla_direct_blue_','first_vla_from_hold_','vla_camera_dry_','smolvla_dry_','smolvla_agent_dry_'))
        if is_run:
            name,steps=run_name(p)
            target=(archive/'zero_step_runs'/name) if steps==0 else root/'runs'/name
        elif platform=='windows' and p.parent.name=='reports':
            date=re.search(r'(2026\d{4})',p.name)
            target=root/'reports'/(date[1] if date else 'undated')/p.name
        else:
            parent='local' if p.parent.name=='.local' else 'home' if platform=='jetson' else 'workspace'
            target=archive/parent/p.name
        moves.append((p,target))
    return moves,skips

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',type=Path,required=True);parser.add_argument('--platform',choices=['windows','jetson'],required=True);parser.add_argument('--execute',action='store_true');a=parser.parse_args()
    base=a.base.resolve();root=base/'so101_vla';moves,skips=plan(base,root,a.platform)
    done=[]
    for source,target in moves:
        assert source.resolve().is_relative_to(base) and target.resolve().is_relative_to(root)
        assert not target.exists(),str(target)
        if a.execute:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(source),str(target))
        done.append({'from':str(source.relative_to(base)),'to':str(target.relative_to(base))})
    name='MOVES_'+a.platform+('_APPLIED' if a.execute else '_PLAN')+'.json'
    with (root/'reports/20261112'/name).open('x',encoding='utf-8') as f:json.dump({'moves':done,'protected':skips},f,ensure_ascii=False,indent=2)
    print(json.dumps({'mode':'APPLIED' if a.execute else 'PLAN','count':len(done),'protected_count':len(skips)}))
if __name__=='__main__':main()
