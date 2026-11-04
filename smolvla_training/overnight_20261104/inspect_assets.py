"""장치 없이 메타데이터·모델 해시·관련 코드/명령 기록을 조사한다."""
import ast
import hashlib
from importlib.metadata import version,PackageNotFoundError
import json
from pathlib import Path
import re
import shutil
import sys
import time
from artifacts import save_json,temperatures

ROOT=Path(__file__).resolve().parent
SOURCES=[('a_to_red_20261102_201029','red',10,[2]),('a_to_red_20261102_210433','red',40,[36]),
('a_to_green_20261102_201804','green',10,[]),('a_to_blue_20261102_212958','green',40,[5,21,22,29,38]),
('a_to_blue_20261103_193835','blue',41,[1,4,5,10,21,35]),('a_to_blue_20261103_193353','blue',9,[])]
def main():
    start=time.monotonic();home=Path.home();datasets=[]
    base=home/'.cache/huggingface/lerobot/Supermassive111'
    for name,color,count,excluded in SOURCES:
        root=base/name;entry=dict(name=name,color=color,expected_episodes=count,excluded_ordinals=excluded,exists=root.exists())
        info=root/'meta/info.json'
        if info.is_file():
            data=json.loads(info.read_text());entry.update(info={k:data.get(k) for k in ('fps','codebase_version','total_episodes','total_frames','data_path','video_path')},features=data.get('features',{}),
                parquet_files=len(list((root/'data').rglob('*.parquet'))),video_files=len(list((root/'videos').rglob('*.mp4'))))
        datasets.append(entry)
    checkpoint=home/'smolvla_model/pretrained_model';model=checkpoint/'model.safetensors'
    digest=hashlib.sha256();hash_start=time.monotonic()
    if model.is_file():
        with model.open('rb') as stream:
            while True:
                block=stream.read(8*1024*1024)
                if not block:break
                digest.update(block)
        model_hash=digest.hexdigest()
    else:model_hash='UNVERIFIED'
    hash_elapsed=time.monotonic()-hash_start
    configs={}
    for name in ('config.json','policy_preprocessor.json','policy_postprocessor.json','train_config.json'):
        path=checkpoint/name
        if path.exists():
            config=json.loads(path.read_text())
            if name=='train_config.json':
                configs[name]={k:config.get(k) for k in ('steps','batch_size','seed','rename_map')}
            else:configs[name]=config
    packages={}
    for name in ('torch','torchvision','lerobot','transformers','numpy','av','pyarrow','opencv-python','Pillow','psutil'):
        try:packages[name]=version(name)
        except PackageNotFoundError:packages[name]='MISSING'
    source_paths=[]
    for pattern in ('so101_first_action*.py','torque_so101*.py','home_align*.py','nov_policy*.sh'):
        source_paths.extend(home.glob(pattern))
    sites=[home/'venvs/lelab-jetson-py310/lib/python3.10/site-packages',home/'venvs/smolvla-py310/lib/python3.10/site-packages']
    for site in sites:
        for relative in ('lerobot/robots/so_follower/so_follower.py','lerobot/motors/motors_bus.py','lerobot/motors/feetech/feetech.py'):
            if (site/relative).exists():source_paths.append(site/relative)
        if (site/'lelab').exists():
            source_paths.extend((site/'lelab').rglob('record.py'));source_paths.extend((site/'lelab').rglob('rollout.py'))
    tools=[]
    for path in dict.fromkeys(source_paths):
        try:text=path.read_text();lines=text.splitlines()
        except (OSError,UnicodeError):continue
        matches=[]
        for i,line in enumerate(lines,1):
            if any(k in line for k in ('enable_torque','disable_torque','Torque_Enable','disable_torque_on_disconnect','robot.connect(','.configure(','send_action(','Goal_Position','torque_disabled')):
                # 코드 표현만 기록하며 기기/계정/주소가 든 전체 원문은 저장하지 않는다.
                tags=[k for k in ('enable_torque','disable_torque','Torque_Enable','disable_torque_on_disconnect','robot.connect(','configure','send_action','Goal_Position','torque_disabled') if k in line]
                matches.append(dict(line=i,tags=tags,setting_false='disable_torque_on_disconnect=False' in line.replace(' ','')))
        tools.append(dict(file=path.name,source_category='installed_package' if 'site-packages' in str(path) else 'home_script',sha256=hashlib.sha256(text.encode()).hexdigest(),matches=matches))
    history=[];path=home/'.bash_history'
    if path.is_file():
        for i,line in enumerate(path.read_text(errors='replace').splitlines(),1):
            if not re.search(r'lelab|lerobot|so101|torque|rollout',line,re.I):continue
            names=re.findall(r'[\w-]+\.(?:py|sh)\b',line)
            flags=[f for f in ('--live','--enable','--disable','--execute','--dry-run') if f in line]
            if names or flags:history.append(dict(line=i,script_names=names,flags=flags))
    result=dict(datasets=datasets,model_sha256=model_hash,model_hash_seconds=hash_elapsed,
        model_hash_matches=model_hash=='b84b15c70ee711ce3ec5780e8498073852f28c43ccc6a726e9196761d05a7654',
        configs=configs,packages=packages,temperatures=temperatures(),disk=shutil.disk_usage(home)._asdict(),
        ffmpeg_path=shutil.which('ffmpeg'),tool_sources=tools,bash_history_filtered=history,elapsed_seconds=time.monotonic()-start)
    save_json(ROOT/'reports/INVENTORY.json',result)
    print(json.dumps(dict(dataset_count=sum(d['exists'] for d in datasets),datasets=[dict(name=d['name'],exists=d['exists'],videos=d.get('video_files')) for d in datasets],model_hash_matches=result['model_hash_matches'],model_sha256=model_hash,packages=packages,disk_free=result['disk']['free'],temperatures=result['temperatures'],source_files=len(tools),history_records=len(history),seconds=result['elapsed_seconds']),ensure_ascii=False))
if __name__=='__main__':main()
